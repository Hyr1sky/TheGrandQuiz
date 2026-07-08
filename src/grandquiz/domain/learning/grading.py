"""判卷工具——照 Reader 的 LLM 槽模式，对一次作答产出结构化判决（缝 3）。

ADR-0004 的两个 LLM 槽之二：判卷走 **role=basic**（deepseek），只产 ``{verdict, cited_evidence}``。
**LLM 判卷，代码记账**：LLM 只给三值判决 + 所引证据；``weak_item_id`` 由调用方（``assess_once``）
按 ``verdict`` 用代码算，**不由 LLM 产**——记账必须确定、可 eval、可回放。

与出题同源的三条约束（结构化输出契约 / 判卷校验门 / 事件上脊柱）见 ``question.py`` 模块 docstring。
判卷校验门（缝 3，与出题门对称）：``cited_evidence`` 非空，且每条引文都锚定被考 item 的
``evidence.quote``（是其子串即可，见 ``ungrounded_citations``）——判卷必须锚定真实证据、可复查；
空 / 幽灵引文 → ``ModelRetry``。
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from grandquiz.domain.learning.models import (
    CitedEvidence,
    KnowledgeItem,
    ungrounded_citations,
)
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.question import MultipleChoiceQuestion
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.kernel.recovery import ErrorClass
from grandquiz.providers.base import Completion, Message, Provider

# 判决三值——出题 / 判卷 / 记账全链路共用的枚举（assessment 的 AssessmentResult 亦复用之）。
VerdictLabel = Literal["对", "勉强", "错"]


def grade_multiple_choice(chosen: str, mc: MultipleChoiceQuestion) -> VerdictLabel:
    """选择题确定性判卷（纯函数、**不调 LLM**）：所选项文本 == 正确项 → ``对``，否则 ``错``。

    ADR-0004 / PRD："选择题确定性比对"——MC 不占 LLM 判卷槽，故 MC **判卷无判卷 model span、
    无需 cassette、更确定**（MC 出题槽仍打真实 LLM、录放照旧）。MC 只有两值（对 / 错），无"勉强"
    （选项非对即错）。``chosen`` 是
    responder 返回的所选项文本，与 ``mc.options[mc.answer_index]`` 逐字比对——记账（薄弱与否）仍
    由 ``assess_once`` 的代码按此 verdict 算，与开放题判卷统一走同一条记账路径。
    """
    return "对" if chosen == mc.options[mc.answer_index] else "错"


def _stable_error_summary(exc: ValidationError) -> str:
    """把 pydantic 校验错误压成版本无关摘要（只取 loc + type）——理由同 reader / question。"""
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in exc.errors())


class GradingError(Exception):
    """判卷失败——有界重试用尽仍拿不到合法 ``Verdict``。

    ``assess_once`` 视其为基础设施级失败、闭合 assessment span 后原样冒泡；``error_class``
    标 ``DEGRADED`` 示"本轮可恢复"——由 kernel ``RecoveryPolicy`` 统一裁决为跳过本轮。
    """

    error_class = ErrorClass.DEGRADED


class ModelRetry(Exception):
    """结构化输出 / 校验门失败的重试信号（借 pydantic-ai 语义，同 reader / question）。"""


class Verdict(BaseModel):
    """判卷 LLM 的结构化输出契约：三值判决 + 一句话诊断理由 + 所引原文证据。

    刻意不含 ``weak_item_id``——薄弱记账由代码按 ``verdict`` 算（ADR-0004），不由 LLM 产。
    ``cited_evidence`` 非空由 ``grade_answer`` 的校验门把关（判卷必须引证据）。

    ``reason``：判官对本次作答的一句话诊断（错 / 勉强：缺 / 偏了哪点；对：命中哪个要点），**只
    展示、不驱动记账**——``weak_item_id`` / 三态转移仍由代码按 ``verdict`` 算。可选默认空串以保向后
    兼容：旧 cassette / 旧模型输出无 ``reason`` 字段时照常解析（缺省为空），不触发校验门重试。
    """

    verdict: VerdictLabel
    reason: str = ""
    cited_evidence: CitedEvidence


async def grade_answer(
    item: KnowledgeItem,
    question: str,
    answer: str,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    max_attempts: int = 3,
    language: str = "中文",
) -> Verdict:
    """对 ``answer``（针对 ``question`` / ``item``）产出结构化判决；持续失败 → ``GradingError``。

    ``max_attempts``：1 次初始调用 + 最多 ``max_attempts - 1`` 次重试（默认 3；测试可收紧）。
    ``language``：判决与反馈语言（默认"中文"，由 ``assess_once`` 从 task 下传）——用字面
    ``str.replace`` 把模板里的 ``{{LANGUAGE}}`` 哨兵换成它（**不用 str.format**：模板含 JSON
    schema 示例的字面花括号，format 会崩）。版本号跨语言稳定；只 message / replay_key 随语言变。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    prompt = load_prompt("answer_grade")
    valid_quotes = {ev.quote for ev in item.evidence}
    evidence_block = "\n".join(f"- {ev.quote}" for ev in item.evidence)
    base_messages = [
        Message(role="system", content=prompt.text.replace("{{LANGUAGE}}", language)),
        Message(
            role="user",
            content=(
                "被考知识点：\n"
                f"概念：{item.concept}\n"
                "原文证据（cited_evidence 应从中引用）：\n"
                f"{evidence_block}\n"
                f"题目：{question}\n"
                f"学习者作答：{answer}"
            ),
        ),
    ]
    retry_note: str | None = None
    last_error = ""
    for _ in range(max_attempts):
        messages = list(base_messages)
        if retry_note is not None:
            messages.append(Message(role="user", content=retry_note))
        completion = await _call_model(
            messages,
            provider=provider,
            emitter=emitter,
            parent_span_id=parent_span_id,
            prompt_version=prompt.version,
        )
        try:
            return _parse(completion.text, valid_quotes)
        except ModelRetry as exc:
            last_error = str(exc)
            retry_note = f"上一次判卷无法采用：{exc}。请只返回合法 JSON，且引用真实证据。"
    raise GradingError(f"判卷失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")


async def _call_model(
    messages: list[Message],
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    prompt_version: str,
) -> Completion:
    # 照 reader._call_model：一对 MODEL_STARTED / MODEL_ENDED 共享 span_id；判卷走 role=basic。
    span_id = emitter.new_span_id()
    emitter.emit(
        EventType.MODEL_STARTED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload={
            "messages": [m.model_dump() for m in messages],
            "prompt_version": prompt_version,
            "role": "basic",
        },
    )
    try:
        completion = await provider.complete(messages, role="basic")
    except Exception as exc:
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=span_id,
            parent_span_id=parent_span_id,
            payload={"ok": False, "error": repr(exc)},
        )
        raise
    emitter.emit(
        EventType.MODEL_ENDED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload={"ok": True, "output": completion.text, "usage": completion.usage.model_dump()},
    )
    return completion


def _parse(text: str, valid_quotes: set[str]) -> Verdict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        verdict = Verdict.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc
    # 判卷校验门（缝 3，与出题门对称）：cited_evidence 非空 + 每条锚定被考 item 证据（子串即可）。
    # 防判卷 LLM 引伪造 / 空串"原文"蒙混——判决必须锚定真实证据、可复查。
    if not verdict.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：判卷必须引用原文证据")
    ghost = ungrounded_citations(verdict.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
    return verdict

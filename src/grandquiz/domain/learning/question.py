"""出题工具——照 Reader 的 LLM 槽模式，为一个 KnowledgeItem 产出 grounded 题（缝 3）。

ADR-0004 的两个 LLM 槽之一（另一个是判卷）：出题走 **role=enrich**（qwen），只产
``{question, cited_evidence}``——它不碰记账（``weak_item_id`` 由判卷后的代码算，见 grading）。

三条设计约束（与 reader 同源）：

- **结构化输出契约（缝 3）**：provider 返回文本经 JSON 解析 + ``GeneratedQuestion`` pydantic
  校验；失败触发 ``ModelRetry``（有界重试，错误反馈进下一次上下文），用尽 → ``QuestionError``。
- **出题校验门（缝 3，eval case 3）——防幽灵题**：``cited_evidence`` 非空，且其中**每条引文都必须
  锚定被考 item 的某条 ``evidence.quote``**（是其子串即可，见 ``ungrounded_citations``——放行
  "Reader 抽长段落、出题引其中短句"）。题必须锚定真实存在的 item 且引真实证据，
  不满足 → ``ModelRetry``。这是运行时的门，不只是 eval 断言。
- **事件上同一条脊柱**：照 ``runner.run_turn`` / ``Reader`` 的模式，每次调用 provider 发
  ``MODEL_STARTED`` →（``payload`` 含 messages 与 prompt_version）→ ``await provider.complete`` →
  ``MODEL_ENDED``。多次重试 = 多个 model span，都挂在 assessment span 下。provider 传输异常
  照 reader 模式：先发 ``MODEL_ENDED(ok=False)`` 闭合 span，再原样冒泡（不吞成 ``QuestionError``，
  以免把 ``ReplayMiss`` 等 harness 错误静默掩盖）。
"""

import json

from pydantic import BaseModel, ValidationError

from grandquiz.domain.learning.models import (
    CitedEvidence,
    KnowledgeItem,
    NonEmptyStr,
    ungrounded_citations,
)
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Provider


def _stable_error_summary(exc: ValidationError) -> str:
    """把 pydantic 校验错误压成**版本无关**摘要（只取 loc + type）——理由同 reader。

    ``str(exc)`` 含 pydantic 带版本的 url，会经 retry_note 进下一次 prompt、被 hash 进 replay_key，
    让回放随 pydantic 版本漂移、毁掉逐字节回放（determinism 缝）。故只取稳定的 loc/type。
    """
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in exc.errors())


class QuestionError(Exception):
    """出题失败——有界重试用尽仍拿不到合法、锚定真实证据的 ``GeneratedQuestion``。

    非领域优雅分支：``assess_once`` 视其为基础设施级失败，闭合 assessment span 后原样冒泡
    （不掩盖）；优雅降级属 M6 RecoveryPolicy。
    """


class ModelRetry(Exception):
    """结构化输出 / 校验门失败的重试信号（借 pydantic-ai 语义，同 reader）。

    被本模块的有界重试循环捕获——把校验错误反馈进下一次调用的上下文；重试预算耗尽则升级为
    ``QuestionError``。它是"输出可验证"的运行时门，不冒泡给调用方。
    """


class GeneratedQuestion(BaseModel):
    """出题 LLM 的结构化输出契约：一道题 + 其锚定的原文证据引文。

    ``question`` 非空（``NonEmptyStr``，strip 后为空也拒）；``cited_evidence`` 的非空与"锚定
    被考 item 证据（子串即可）"由 ``generate_question`` 的校验门把关。刻意不产 ``item_id`` /
    ``weak_item_id``——出题不记账，被考 item 由调用方指定、记账由判卷后的代码算（ADR-0004）。
    """

    question: NonEmptyStr
    cited_evidence: CitedEvidence


class MultipleChoiceQuestion(BaseModel):
    """选择题 LLM 的结构化输出契约：题干 + 选项 + 正确项下标 + 锚定的原文证据引文。

    ``answer_index`` 是正确项在 ``options`` 里的下标——判卷走**确定性代码**（responder 所选项文本
    == ``options[answer_index]`` → 对 / 错），**不调 LLM**（PRD："选择题确定性比对"）。合法性
    （``options`` ≥ 2、``answer_index`` 合法、``cited_evidence`` 非空且子串锚定被考 item 证据）
    由 ``generate_multiple_choice`` 的校验门把关（防幽灵题 + 防不可判卷）。与出题同源：刻意不产
    ``item_id`` / ``weak_item_id``——出题不记账（ADR-0004）。
    """

    question: NonEmptyStr
    options: list[NonEmptyStr]  # 非空（strip 后也非空）；两两可区分由 _parse_mc 的门把关
    answer_index: int
    cited_evidence: CitedEvidence


async def generate_question(
    item: KnowledgeItem,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    max_attempts: int = 3,
    prompt_name: str = "question_generate",
) -> GeneratedQuestion:
    """为 ``item`` 产出一道 grounded 题；持续失败 → ``QuestionError``。见模块 docstring。

    ``max_attempts``：1 次初始调用 + 最多 ``max_attempts - 1`` 次重试（默认 3；测试可收紧）。
    ``prompt_name``：出题 system prompt 模板名——默认 ``question_generate``（标准开放题）；
    追问深挖传 ``question_probe``（同一 schema、仅换 prompt 逼深一层）。trace 记的 prompt_version
    随之反映所用变体，故 eval 回归可归因到具体题型 prompt（追问用例即靠此断言走了 probe）。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    prompt = load_prompt(prompt_name)
    valid_quotes = {ev.quote for ev in item.evidence}
    evidence_block = "\n".join(f"- {ev.quote}" for ev in item.evidence)
    base_messages = [
        Message(role="system", content=prompt.text),
        Message(
            role="user",
            content=(
                "被考知识点：\n"
                f"概念：{item.concept}\n"
                f"摘要：{item.summary}\n"
                "可引用的原文证据（cited_evidence 只能逐字从中挑选）：\n"
                f"{evidence_block}"
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
            retry_note = f"上一次出题无法采用：{exc}。请只返回合法 JSON，且引用真实证据。"
    raise QuestionError(f"出题失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")


async def _call_model(
    messages: list[Message],
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    prompt_version: str,
) -> Completion:
    # 照 reader._call_model：一对 MODEL_STARTED / MODEL_ENDED 共享 span_id；出题走 role=enrich。
    span_id = emitter.new_span_id()
    emitter.emit(
        EventType.MODEL_STARTED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload={
            "messages": [m.model_dump() for m in messages],
            "prompt_version": prompt_version,
            "role": "enrich",
        },
    )
    try:
        completion = await provider.complete(messages, role="enrich")
    except Exception as exc:
        # provider 传输异常 / ReplayMiss：先闭合 span（started/ended 配对不变量），再原样冒泡。
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


def _parse(text: str, valid_quotes: set[str]) -> GeneratedQuestion:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        question = GeneratedQuestion.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc
    # 出题校验门（缝 3，eval case 3）：非空 + 每条引文都锚定被考 item 的真实证据（防幽灵题）。
    # 锚定 = 引文是某条 evidence.quote 的子串（见 ungrounded_citations）——放行"抽长段落、引短句"。
    if not question.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：题必须引用被考知识点的原文证据")
    ghost = ungrounded_citations(question.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
    return question


async def generate_multiple_choice(
    item: KnowledgeItem,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    max_attempts: int = 3,
) -> MultipleChoiceQuestion:
    """为 ``item`` 产一道锚定的选择题（首次接触概念的热身题型）；持续失败 → ``QuestionError``。

    与 ``generate_question`` 同源（role=enrich、结构化输出契约、事件上脊柱、有界重试），仅多两条
    MC 专属校验门（缝 3）：

    - **可判卷门**：``options`` 至少 2 项、``answer_index`` 是 ``options`` 的合法下标——否则确定性
      判卷（``grade_multiple_choice``）无从比对，出题即不合格。
    - **防幽灵题门**：``cited_evidence`` 非空且每条锚定被考 item 证据（子串即可，与开放题同规则）。

    任一不满足 → ``ModelRetry``（反馈进下一次上下文）；重试预算耗尽 → ``QuestionError``。
    provider 传输异常照 ``_call_model`` 模式先闭合 model span 后原样冒泡（不吞）。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    prompt = load_prompt("question_multiple_choice")
    valid_quotes = {ev.quote for ev in item.evidence}
    evidence_block = "\n".join(f"- {ev.quote}" for ev in item.evidence)
    base_messages = [
        Message(role="system", content=prompt.text),
        Message(
            role="user",
            content=(
                "被考知识点：\n"
                f"概念：{item.concept}\n"
                f"摘要：{item.summary}\n"
                "可引用的原文证据（cited_evidence 只能逐字从中挑选）：\n"
                f"{evidence_block}"
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
            return _parse_mc(completion.text, valid_quotes)
        except ModelRetry as exc:
            last_error = str(exc)
            retry_note = f"上一次出题无法采用：{exc}。请只返回合法 JSON，且引用真实证据。"
    raise QuestionError(f"选择题出题失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")


def _parse_mc(text: str, valid_quotes: set[str]) -> MultipleChoiceQuestion:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        mc = MultipleChoiceQuestion.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc
    # 可判卷门：选项 ≥ 2、正确项下标合法——否则确定性判卷无从比对。
    if len(mc.options) < 2:
        raise ModelRetry(f"选项至少需 2 项（现 {len(mc.options)} 项）：选择题需可区分的干扰项")
    if not 0 <= mc.answer_index < len(mc.options):
        raise ModelRetry(
            f"answer_index 越界：{mc.answer_index} 不在合法下标 [0, {len(mc.options)}) 内"
        )
    # 选项须两两可区分：确定性判卷按文本比对，重复选项会让"选了干扰项"被误判为对、污染薄弱账本。
    # （空 / 纯空白选项已由 options 的 NonEmptyStr 挡下。）
    if len(set(mc.options)) != len(mc.options):
        raise ModelRetry(f"选项含重复文本：{mc.options}——确定性判卷按文本比对，选项须两两可区分")
    # 防幽灵题门（与开放题同规则）：cited_evidence 非空 + 每条都锚定被考 item 真实证据（子串即可）。
    if not mc.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：题必须引用被考知识点的原文证据")
    ghost = ungrounded_citations(mc.cited_evidence, valid_quotes)
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
    return mc

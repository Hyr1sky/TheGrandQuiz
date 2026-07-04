"""出题工具——照 Reader 的 LLM 槽模式，为一个 KnowledgeItem 产出 grounded 题（缝 3）。

ADR-0004 的两个 LLM 槽之一（另一个是判卷）：出题走 **role=enrich**（qwen），只产
``{question, cited_evidence}``——它不碰记账（``weak_item_id`` 由判卷后的代码算，见 grading）。

三条设计约束（与 reader 同源）：

- **结构化输出契约（缝 3）**：provider 返回文本经 JSON 解析 + ``GeneratedQuestion`` pydantic
  校验；失败触发 ``ModelRetry``（有界重试，错误反馈进下一次上下文），用尽 → ``QuestionError``。
- **出题校验门（缝 3，eval case 3）——防幽灵题**：``cited_evidence`` 非空，且其中**每条引文都必须
  逐字命中被考 item 的某条 ``evidence.quote``**。题必须锚定真实存在的 item 且引真实证据，
  不满足 → ``ModelRetry``。这是运行时的门，不只是 eval 断言。
- **事件上同一条脊柱**：照 ``runner.run_turn`` / ``Reader`` 的模式，每次调用 provider 发
  ``MODEL_STARTED`` →（``payload`` 含 messages 与 prompt_version）→ ``await provider.complete`` →
  ``MODEL_ENDED``。多次重试 = 多个 model span，都挂在 assessment span 下。provider 传输异常
  照 reader 模式：先发 ``MODEL_ENDED(ok=False)`` 闭合 span，再原样冒泡（不吞成 ``QuestionError``，
  以免把 ``ReplayMiss`` 等 harness 错误静默掩盖）。
"""

import json

from pydantic import BaseModel, ValidationError

from grandquiz.domain.learning.models import KnowledgeItem, NonEmptyStr
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

    ``question`` 非空（``NonEmptyStr``，strip 后为空也拒）；``cited_evidence`` 的非空与"逐字命中
    被考 item 证据"由 ``generate_question`` 的校验门把关（防幽灵题）。刻意不产 ``item_id`` /
    ``weak_item_id``——出题不记账，被考 item 由调用方指定、记账由判卷后的代码算（ADR-0004）。
    """

    question: NonEmptyStr
    cited_evidence: list[str]


async def generate_question(
    item: KnowledgeItem,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    max_attempts: int = 3,
) -> GeneratedQuestion:
    """为 ``item`` 产出一道 grounded 题；持续失败 → ``QuestionError``。见模块 docstring。

    ``max_attempts``：1 次初始调用 + 最多 ``max_attempts - 1`` 次重试（默认 3；测试可收紧）。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    prompt = load_prompt("question_generate")
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
    # 出题校验门（缝 3，eval case 3）：非空 + 每条引文逐字命中被考 item 的真实证据（防幽灵题）。
    if not question.cited_evidence:
        raise ModelRetry("cited_evidence 不能为空：题必须引用被考知识点的原文证据")
    ghost = [quote for quote in question.cited_evidence if quote not in valid_quotes]
    if ghost:
        raise ModelRetry(f"引用了不属于被考知识点的证据（幽灵引文）：{ghost}")
    return question

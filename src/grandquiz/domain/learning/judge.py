"""质量评审——Tier-2 LLM-judge 骨架（R3 首个具体能力）：评"产出好不好"，不评"作答对不对"。

与 ``grading.py`` 的判卷（评学习者作答）是两件事：判卷是 Tier-1 能测的正确性判决（对/勉强/错，
有确定性证据锚定校验门）；本模块评的是**出题官自己产出的质量**（干扰项有没有迷惑性）——这类
"好不好"的判断没有确定性真值可比对，M8 PRD 早就把它明确划给 Tier-2、排除在规则断言之外
（``evals/__init__.py`` docstring："Tier-2 LLM judge 仍待建"）。

结构上刻意照抄 ``grading.py`` 的判卷槽模式（结构化输出契约 + 校验门 + 有界重试），因为"LLM 产出
结构化判断、代码校验合法性后再采信"这套纪律与评的是作答还是产出无关，值得复用而非另起一套。
首个具体场景：选择题干扰项 plausibility——architecture.md 与 M8 PRD 都点名它是"确定性 harness
结构上看不见、必须靠 judge"的教科书案例。
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.kernel.recovery import ErrorClass
from grandquiz.providers.base import Completion, Message, Provider

DistractorLabel = Literal["合理干扰", "较弱干扰", "无效干扰"]


class JudgeError(Exception):
    """质量评审失败——有界重试用尽仍拿不到合法判定。

    ``error_class = DEGRADED``：质量评审是离线 / 事后分析用途，不是考核链路的实时环节，失败
    应可恢复地跳过这一条评审，不该拖垮调用方（同 ``GradingError`` 的分类理由）。
    """

    error_class = ErrorClass.DEGRADED


class ModelRetry(Exception):
    """结构化输出校验门失败的重试信号（借 pydantic-ai 语义，同 grading / question 模块）。"""


class DistractorVerdict(BaseModel):
    """质量评审 LLM 的结构化输出契约：三档判定 + 一句话理由。

    刻意用离散三档（``合理干扰``/``较弱干扰``/``无效干扰``）而非连续分数——同 ``grading.Verdict``
    不用打分改用三值判决的理由一致：三档已经是"好不好"这个判断需要的全部语义，连续分数只会引入
    "0.72 和 0.75 到底差在哪"这种假精度。
    """

    label: DistractorLabel
    rationale: str


async def judge_distractor(
    item: KnowledgeItem,
    question: str,
    correct_answer: str,
    distractor: str,
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None = None,
    max_attempts: int = 3,
) -> DistractorVerdict:
    """评一个选择题干扰项的 plausibility；持续失败 → ``JudgeError``。

    ``max_attempts``：1 次初始调用 + 最多 ``max_attempts - 1`` 次重试（默认 3，同判卷槽）。走
    role=basic（同判卷槽，这是"判断"而非"生成"的角色分工，enrich 只管出题）。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 至少为 1")
    prompt = load_prompt("judge_distractor_plausibility")
    evidence_block = "\n".join(f"- {ev.quote}" for ev in item.evidence)
    base_messages = [
        Message(role="system", content=prompt.text),
        Message(
            role="user",
            content=(
                "被考知识点：\n"
                f"概念：{item.concept}\n"
                "原文证据：\n"
                f"{evidence_block}\n"
                f"题目：{question}\n"
                f"正确答案：{correct_answer}\n"
                f"待评干扰项：{distractor}"
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
            return _parse(completion.text)
        except ModelRetry as exc:
            last_error = str(exc)
            retry_note = f"上一次评审无法采用：{exc}。请只返回合法 JSON。"
    raise JudgeError(f"质量评审失败（{max_attempts} 次尝试仍无合法输出）：{last_error}")


async def _call_model(
    messages: list[Message],
    *,
    provider: Provider,
    emitter: EventEmitter,
    parent_span_id: str | None,
    prompt_version: str,
) -> Completion:
    # 照 grading._call_model：一对 MODEL_STARTED/MODEL_ENDED 共享 span_id；评审走 role=basic。
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


def _parse(text: str) -> DistractorVerdict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRetry(f"非法 JSON：{exc}") from exc
    try:
        return DistractorVerdict.model_validate(data)
    except ValidationError as exc:
        raise ModelRetry(f"输出不符合 schema：{_stable_error_summary(exc)}") from exc


def _stable_error_summary(exc: ValidationError) -> str:
    # 照 grading._stable_error_summary：把校验错误压成版本无关摘要，重试提示不泄露内部字段路径细节。
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}:{e['type']}" for e in exc.errors())

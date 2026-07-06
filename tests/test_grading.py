"""判卷结构化输出契约测试（缝 3）——注入假 provider，无真实 LLM。

被测：合法 verdict JSON → Verdict（三种 verdict 都能解析）；verdict 非法枚举值 / cited_evidence
为空 → 有界重试用尽 GradingError（provider 被多调）；判卷走 role=basic。
"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.grading import (
    GradingError,
    Verdict,
    grade_answer,
    grade_multiple_choice,
)
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.domain.learning.question import MultipleChoiceQuestion
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, Usage

_QUOTE = "闭包捕获的是变量而非值"


class _FixedProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        self.roles.append(role)
        return Completion(text=self.text, usage=Usage(prompt_tokens=5, completion_tokens=2))


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


def _item() -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id="res",
        index=0,
        concept="闭包",
        summary="函数捕获定义时的作用域",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )


async def _grade(provider: _FixedProvider, *, max_attempts: int = 3) -> Verdict:
    emitter, _ = _emitter()
    return await grade_answer(
        _item(),
        "什么是闭包？",
        "闭包能捕获外层变量",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        max_attempts=max_attempts,
    )


async def test_valid_verdict_parses() -> None:
    provider = _FixedProvider(json.dumps({"verdict": "对", "cited_evidence": [_QUOTE]}))
    emitter, events = _emitter()

    verdict = await grade_answer(
        _item(),
        "什么是闭包？",
        "闭包能捕获外层变量",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
    )

    assert isinstance(verdict, Verdict)
    assert verdict.verdict == "对"
    assert verdict.cited_evidence == [_QUOTE]
    assert provider.calls == 1
    assert provider.roles == ["basic"]  # 判卷走 basic 角色
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]


async def test_string_cited_evidence_is_coerced_to_list() -> None:
    # 真机 LLM 常把单条 cited_evidence 写成裸字符串（正是这次真机踩到的 list_type 报错）——
    # 被宽容纳成单元素列表，锚定门在其后照常把关。
    provider = _FixedProvider(json.dumps({"verdict": "错", "cited_evidence": _QUOTE}))
    verdict = await _grade(provider)
    assert verdict.cited_evidence == [_QUOTE]
    assert provider.calls == 1  # 裸字符串被纳成列表 + 引文命中真实证据 → 无需重试


async def test_substring_citation_is_accepted() -> None:
    # 判卷锚定门放宽为子串（与出题门对称）：判卷只引长证据里一句短句，仍属真实原文，首次即过。
    provider = _FixedProvider(json.dumps({"verdict": "对", "cited_evidence": ["捕获的是变量"]}))
    verdict = await _grade(provider)
    assert verdict.cited_evidence == ["捕获的是变量"]
    assert provider.calls == 1  # 子串命中真实证据 → 无需重试


@pytest.mark.parametrize("label", ["对", "勉强", "错"])
async def test_all_three_verdicts_parse(label: str) -> None:
    provider = _FixedProvider(json.dumps({"verdict": label, "cited_evidence": [_QUOTE]}))
    verdict = await _grade(provider)
    assert verdict.verdict == label


async def test_illegal_verdict_enum_retries_then_raises() -> None:
    # verdict 非三值枚举 → schema 校验失败 → ModelRetry 用尽 → GradingError。
    provider = _FixedProvider(json.dumps({"verdict": "满分", "cited_evidence": [_QUOTE]}))
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


async def test_empty_cited_evidence_retries_then_raises() -> None:
    # 判卷校验门：cited_evidence 为空 → ModelRetry 用尽 → GradingError。
    provider = _FixedProvider(json.dumps({"verdict": "对", "cited_evidence": []}))
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


async def test_fabricated_cited_evidence_retries_then_raises() -> None:
    # 判卷锚定门（与出题门对称）：引了伪造的"原文" → ModelRetry 用尽 → GradingError。
    provider = _FixedProvider(
        json.dumps({"verdict": "对", "cited_evidence": ["这句原文根本不存在"]})
    )
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


async def test_malformed_json_retries_then_raises() -> None:
    provider = _FixedProvider("这不是 JSON")
    with pytest.raises(GradingError):
        await _grade(provider, max_attempts=2)
    assert provider.calls == 2


class _RaisingProvider:
    """provider.complete 抛传输类异常（模拟网络 / 超时 / 5xx，或 ReplayMiss）。计被调次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        raise RuntimeError("网络超时")


# --- M3.4 选择题确定性判卷（缝 2，纯代码不调 LLM）------------------------------------


def _mc() -> MultipleChoiceQuestion:
    return MultipleChoiceQuestion(
        question="闭包捕获的是？",
        options=["值的快照", "变量本身", "函数体"],
        answer_index=1,
        cited_evidence=[_QUOTE],
    )


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        ("变量本身", "对"),  # == options[answer_index] → 对
        ("值的快照", "错"),  # 其它选项 → 错
        ("函数体", "错"),
        ("压根不在选项里的文本", "错"),  # 非选项文本 → 错（MC 无"勉强"）
    ],
)
def test_grade_multiple_choice_is_deterministic(chosen: str, expected: str) -> None:
    # 确定性判卷：所选项文本与正确项逐字比对，纯代码、不构造任何 provider / emitter。
    assert grade_multiple_choice(chosen, _mc()) == expected


async def test_provider_exception_closes_model_span_and_propagates() -> None:
    # provider 基础设施异常：先发 MODEL_ENDED(ok=False) 闭合 span，再原样冒泡（不吞、不重试）。
    provider = _RaisingProvider()
    emitter, events = _emitter()

    with pytest.raises(RuntimeError):
        await grade_answer(
            _item(),
            "什么是闭包？",
            "闭包能捕获外层变量",
            provider=provider,
            emitter=emitter,
            parent_span_id="a",
        )

    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]
    assert events[-1].payload["ok"] is False
    assert provider.calls == 1

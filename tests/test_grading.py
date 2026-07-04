"""判卷结构化输出契约测试（缝 3）——注入假 provider，无真实 LLM。

被测：合法 verdict JSON → Verdict（三种 verdict 都能解析）；verdict 非法枚举值 / cited_evidence
为空 → 有界重试用尽 GradingError（provider 被多调）；判卷走 role=basic。
"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.grading import GradingError, Verdict, grade_answer
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
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


async def _grade(provider: _FixedProvider, **kwargs: int) -> Verdict:
    emitter, _ = _emitter()
    return await grade_answer(
        _item(),
        "什么是闭包？",
        "闭包能捕获外层变量",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        **kwargs,
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

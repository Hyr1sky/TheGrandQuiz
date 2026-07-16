"""质量评审结构化输出契约测试（Tier-2 骨架，缝 3）——注入假 provider，无真实 LLM。

照 test_grading.py 的模式：合法三档判定都能解析；非法枚举值 / 畸形 JSON → 有界重试用尽
JudgeError（provider 被多调）；评审走 role=basic；provider 基础设施异常闭合 span 后原样冒泡。
"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.judge import (
    DistractorVerdict,
    JudgeError,
    judge_distractor,
)
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, Usage

_QUOTE = "闭包捕获变量而非值"


class _FixedProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
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
        concept="闭包",
        summary="函数捕获定义时的作用域",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )


async def _judge(provider: _FixedProvider, *, max_attempts: int = 3) -> DistractorVerdict:
    emitter, _ = _emitter()
    return await judge_distractor(
        _item(),
        "闭包捕获的是什么？",
        "变量",
        "值",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        max_attempts=max_attempts,
    )


@pytest.mark.parametrize("label", ["合理干扰", "较弱干扰", "无效干扰"])
async def test_all_three_labels_parse(label: str) -> None:
    provider = _FixedProvider(json.dumps({"label": label, "rationale": "理由"}))
    verdict = await _judge(provider)
    assert verdict.label == label
    assert verdict.rationale == "理由"
    assert provider.calls == 1
    assert provider.roles == ["basic"]  # 评审走 basic 角色（判断而非生成）


async def test_events_emitted_around_model_call() -> None:
    provider = _FixedProvider(json.dumps({"label": "合理干扰", "rationale": "理由"}))
    emitter, events = _emitter()
    await judge_distractor(
        _item(),
        "闭包捕获的是什么？",
        "变量",
        "值",
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
    )
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]


async def test_illegal_label_enum_retries_then_raises() -> None:
    provider = _FixedProvider(json.dumps({"label": "完美", "rationale": "理由"}))
    with pytest.raises(JudgeError):
        await _judge(provider, max_attempts=2)
    assert provider.calls == 2


async def test_malformed_json_retries_then_raises() -> None:
    provider = _FixedProvider("这不是 JSON")
    with pytest.raises(JudgeError):
        await _judge(provider, max_attempts=2)
    assert provider.calls == 2


class _RaisingProvider:
    """provider.complete 抛传输类异常（模拟网络 / 超时 / 5xx，或 ReplayMiss）。计被调次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        raise RuntimeError("网络超时")


async def test_provider_exception_closes_model_span_and_propagates() -> None:
    provider = _RaisingProvider()
    emitter, events = _emitter()
    with pytest.raises(RuntimeError):
        await judge_distractor(
            _item(),
            "闭包捕获的是什么？",
            "变量",
            "值",
            provider=provider,
            emitter=emitter,
            parent_span_id="a",
        )
    assert [e.type for e in events] == [EventType.MODEL_STARTED, EventType.MODEL_ENDED]
    assert events[-1].payload["ok"] is False
    assert provider.calls == 1

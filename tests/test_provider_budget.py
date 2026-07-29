"""SH-S7：完整 messages + tool specs 在每次 Provider 出站前受硬预算。"""

from collections.abc import AsyncIterator, Sequence

import pytest
from pydantic import BaseModel

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import Tool, ToolRegistry
from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    ProviderStreamEvent,
    Role,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from grandquiz.providers.budget import BudgetedProvider, ProviderRequestBudgetExceeded


class _CharCounter:
    def count(self, text: str) -> int:
        return len(text)


class _CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        return Completion(text="ok")


class _StreamingCountingProvider(_CountingProvider):
    def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        del messages, role, tools

        async def stream() -> AsyncIterator[ProviderStreamEvent]:
            self.calls += 1
            yield TextDelta(text="o")
            yield CompletionFinished(completion=Completion(text="ok"))

        return stream()


async def test_tool_schema_is_counted_before_provider_call() -> None:
    inner = _CountingProvider()
    provider = BudgetedProvider(inner=inner, counter=_CharCounter(), ceiling=100)
    tool = ToolSpec(
        name="large",
        description="x" * 200,
        parameters={"type": "object", "properties": {}},
    )

    with pytest.raises(ProviderRequestBudgetExceeded) as captured:
        await provider.complete([Message(role="user", content="hi")], tools=[tool])

    assert captured.value.tools > 100
    assert inner.calls == 0


async def test_budgeted_provider_preserves_native_streaming_capability() -> None:
    inner = _StreamingCountingProvider()
    provider = BudgetedProvider(
        inner=inner,
        counter=_CharCounter(),
        ceiling=1_000,
    )

    events = [
        event async for event in provider.stream_complete([Message(role="user", content="hi")])
    ]

    assert events == [
        TextDelta(text="o"),
        CompletionFinished(completion=Completion(text="ok")),
    ]
    assert inner.calls == 1


class _NoParams(BaseModel):
    pass


class _FirstCallUsesTool:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        return Completion(
            text="",
            tool_calls=[ToolCall(id="call-1", name="large_result", arguments={})],
        )


async def test_large_tool_result_is_rejected_before_second_remote_call() -> None:
    registry = ToolRegistry()

    async def large_result(_params: _NoParams) -> str:
        return "x" * 2_000

    registry.register(
        Tool(
            name="large_result",
            description="返回大结果",
            params=_NoParams,
            handler=large_result,
        )
    )
    inner = _FirstCallUsesTool()
    provider = BudgetedProvider(inner=inner, counter=_CharCounter(), ceiling=1_000)
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="budget")
    runner = Runner(provider=provider, emitter=emitter, tools=registry)

    with pytest.raises(ProviderRequestBudgetExceeded) as captured:
        await runner.run_agent_turn("运行工具")

    assert captured.value.messages > captured.value.tools
    assert inner.calls == 1  # 第二次调用在本地预算门处被挡，未交给远端

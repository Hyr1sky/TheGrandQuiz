from collections.abc import Sequence

import pytest

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.providers.base import Completion, Message, Role, ToolSpec
from grandquiz.providers.echo import DemoEchoProvider
from grandquiz.providers.legacy import LegacyPurposeProvider
from grandquiz.providers.models import with_identity
from grandquiz.providers.profiles import ModelIdentity


def _make_runner() -> tuple[Runner, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")
    runner = Runner(provider=DemoEchoProvider(), emitter=emitter)
    return runner, events


async def test_run_turn_echoes_user_message() -> None:
    runner, _ = _make_runner()
    reply = await runner.run_turn("hello")
    assert reply == "echo: hello"


async def test_run_turn_emits_lifecycle_events_in_order() -> None:
    runner, events = _make_runner()
    await runner.run_turn("hi")
    assert [e.type for e in events] == [
        EventType.TURN_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        EventType.TURN_ENDED,
    ]


async def test_model_span_nests_under_turn_span() -> None:
    runner, events = _make_runner()
    await runner.run_turn("hi")
    by_type = {e.type: e for e in events}
    turn_span = by_type[EventType.TURN_STARTED].span_id
    model = by_type[EventType.MODEL_STARTED]
    assert model.parent_span_id == turn_span
    assert by_type[EventType.MODEL_ENDED].span_id == model.span_id
    assert by_type[EventType.TURN_ENDED].span_id == turn_span


async def test_history_accumulates_across_turns() -> None:
    runner, events = _make_runner()
    await runner.run_turn("first")
    await runner.run_turn("second")
    second_model_started = [e for e in events if e.type == EventType.MODEL_STARTED][1]
    roles = [m["role"] for m in second_model_started.payload["messages"]]
    assert roles == ["user", "assistant", "user"]


class _RaisingProvider(LegacyPurposeProvider):
    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        raise RuntimeError("boom")


class _FlakyProvider(LegacyPurposeProvider):
    """Raises on the first call, echoes on subsequent calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return Completion(text=f"echo: {last_user}")


async def test_error_path_closes_model_span() -> None:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")
    runner = Runner(provider=_RaisingProvider(), emitter=emitter)

    with pytest.raises(RuntimeError):
        await runner.run_turn("q")

    assert [e.type for e in events] == [
        EventType.TURN_STARTED,
        EventType.MODEL_STARTED,
        EventType.ERROR,
        EventType.MODEL_ENDED,
        EventType.TURN_ENDED,
    ]
    by_type = {e.type: e for e in events}
    # every *.started has a matching *.ended sharing span_id, even on error
    assert by_type[EventType.MODEL_ENDED].span_id == by_type[EventType.MODEL_STARTED].span_id
    assert by_type[EventType.MODEL_ENDED].payload["ok"] is False


async def test_failed_turn_leaves_no_orphan_user_message() -> None:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")
    runner = Runner(provider=_FlakyProvider(), emitter=emitter)

    with pytest.raises(RuntimeError):
        await runner.run_turn("q1")
    reply = await runner.run_turn("q2")

    assert reply == "echo: q2"
    # the rolled-back q1 must not linger: the successful turn has no two consecutive users
    model_starts = [e for e in events if e.type == EventType.MODEL_STARTED]
    roles = [m["role"] for m in model_starts[-1].payload["messages"]]
    assert roles == ["user"]


class _NamedModel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.messages: list[list[Message]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del tools
        self.messages.append(list(messages))
        return Completion(text=self.name)


async def test_agent_turn_can_use_one_frozen_model_without_changing_runner_history() -> None:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="selection")
    default = _NamedModel("default")
    explicit = _NamedModel("explicit")
    identity = ModelIdentity(
        purpose="chat",
        selection_source="explicit_profile",
        configuration_fingerprint="1" * 64,
        policy_fingerprint="2" * 64,
    )
    runner = Runner(provider=default, emitter=emitter)

    first = await runner.run_agent_turn("first", model=with_identity(explicit, identity))
    second = await runner.run_agent_turn("second")

    assert first == "explicit"
    assert second == "default"
    assert len(explicit.messages) == len(default.messages) == 1
    assert [message.content for message in default.messages[0]] == [
        "first",
        "explicit",
        "second",
    ]
    started = [event for event in events if event.type == EventType.MODEL_STARTED]
    assert started[0].payload["model_identity"] == identity.model_dump()
    assert "model_identity" not in started[1].payload

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.providers.echo import DemoEchoProvider


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

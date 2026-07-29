import pytest
from pydantic import ValidationError

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType


def test_emitter_stamps_monotonic_seq_and_clock_ts() -> None:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(start=10.0, tick=1.0), trace_id="t")

    emitter.emit(EventType.TURN_STARTED)
    emitter.emit(EventType.TURN_ENDED)

    assert [e.seq for e in events] == [0, 1]
    assert [e.ts for e in events] == [10.0, 11.0]
    assert all(e.trace_id == "t" for e in events)


def test_new_span_id_is_unique_and_deterministic() -> None:
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="t")
    assert emitter.new_span_id() == "t:s0"
    assert emitter.new_span_id() == "t:s1"


def test_emitter_can_resume_a_persisted_trace_without_reusing_ids() -> None:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(
        sink,
        ManualClock(),
        trace_id="t",
        initial_seq=7,
        initial_span_counter=3,
    )

    assert emitter.new_span_id() == "t:s3"
    emitter.emit("approval.decided")

    assert events[0].seq == 7


def test_emit_returns_event_with_payload_and_span_links() -> None:
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="t")
    event = emitter.emit(
        EventType.MODEL_STARTED,
        payload={"k": "v"},
        span_id="t:s1",
        parent_span_id="t:s0",
    )
    assert event.type == EventType.MODEL_STARTED
    assert event.payload == {"k": "v"}
    assert event.span_id == "t:s1"
    assert event.parent_span_id == "t:s0"


def test_agent_event_is_frozen() -> None:
    event = AgentEvent(type="x", seq=0, ts=0.0, trace_id="t")
    with pytest.raises(ValidationError):
        event.seq = 5


def test_payload_is_isolated_from_source_mutation() -> None:
    inner = [1, 2]
    source = {"a": inner}
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="t")
    event = emitter.emit(EventType.MODEL_STARTED, payload=source)
    inner.append(3)
    assert event.payload == {"a": [1, 2]}

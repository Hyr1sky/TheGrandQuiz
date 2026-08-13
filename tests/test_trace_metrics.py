"""Trace token metrics shared by every event-spine consumer."""

from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.kernel.trace import summarize_token_usage


def _event(seq: int, event_type: str, payload: dict[str, object]) -> AgentEvent:
    return AgentEvent(
        type=event_type,
        seq=seq,
        ts=float(seq),
        trace_id="trace-usage",
        span_id=f"trace-usage:s{seq}",
        payload=payload,
    )


def test_trace_token_usage_has_one_projection_for_all_event_spine_consumers() -> None:
    events = [
        _event(0, EventType.MODEL_STARTED, {"usage": {"prompt_tokens": 999}}),
        _event(
            1,
            EventType.MODEL_ENDED,
            {"usage": {"prompt_tokens": 12, "completion_tokens": 3}},
        ),
        _event(
            2,
            EventType.MODEL_ENDED,
            {"usage": {"prompt_tokens": 8, "completion_tokens": 2}},
        ),
        _event(3, EventType.MODEL_ENDED, {"usage": {"prompt_tokens": "invalid"}}),
    ]

    usage = summarize_token_usage(events)

    assert usage.prompt_tokens == 20
    assert usage.completion_tokens == 5
    assert usage.total_tokens == 25

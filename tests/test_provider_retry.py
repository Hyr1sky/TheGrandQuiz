"""Application-owned transport retry: one logical call, visible attempts, no unsafe replay."""

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.model_execution import complete_model_call, stream_model_call
from grandquiz.kernel.trace import summarize_token_usage
from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    ProviderStreamEvent,
    TextDelta,
    ToolSpec,
    Usage,
)
from grandquiz.providers.failure import (
    ProviderFailure,
    ProviderFailureCategory,
    parse_retry_after,
)
from grandquiz.providers.models import with_identity
from grandquiz.providers.profiles import ModelIdentity
from grandquiz.providers.retry import ProviderRetryPolicy, RetryRuntime


class _RetryClock:
    def __init__(self, *, monotonic: float = 0.0, utc_timestamp: float = 0.0) -> None:
        self.monotonic_value = monotonic
        self.utc_value = utc_timestamp

    def monotonic(self) -> float:
        return self.monotonic_value

    def utc_timestamp(self) -> float:
        return self.utc_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.utc_value += seconds


class _Sleeper:
    def __init__(self, clock: _RetryClock, *, cancel: bool = False) -> None:
        self.clock = clock
        self.cancel = cancel
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.cancel:
            raise asyncio.CancelledError
        self.clock.advance(seconds)


class _FixedRng:
    def random(self) -> float:
        return 0.5


class _SequenceModel:
    def __init__(self, outcomes: Sequence[Completion | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, tools
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _StreamSequenceModel:
    def __init__(self, attempts: Sequence[Sequence[ProviderStreamEvent | BaseException]]) -> None:
        self.attempts = [list(attempt) for attempt in attempts]
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, tools
        raise AssertionError("stream path expected")

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        del messages, tools
        attempt = self.attempts[self.calls]
        self.calls += 1
        for outcome in attempt:
            if isinstance(outcome, BaseException):
                raise outcome
            yield outcome


def _identity() -> ModelIdentity:
    return ModelIdentity(
        purpose="chat",
        selection_source="default",
        configuration_fingerprint="1" * 64,
        policy_fingerprint="2" * 64,
    )


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="retry"), events


def _runtime(
    *,
    policy: ProviderRetryPolicy | None = None,
    cancel_wait: bool = False,
) -> tuple[RetryRuntime, _Sleeper]:
    clock = _RetryClock(utc_timestamp=1_700_000_000.0)
    sleeper = _Sleeper(clock, cancel=cancel_wait)
    return (
        RetryRuntime(
            policy=policy or ProviderRetryPolicy(),
            clock=clock,
            sleeper=sleeper,
            rng=_FixedRng(),
        ),
        sleeper,
    )


def _temporary_failure(
    *,
    retry_after_seconds: float | None = None,
    retry_after_at: float | None = None,
    response_started: bool = False,
    replay_safe: bool | None = None,
) -> ProviderFailure:
    return ProviderFailure(
        category=ProviderFailureCategory.RATE_LIMITED,
        retryable=True,
        status_code=429,
        retry_after_seconds=retry_after_seconds,
        retry_after_at=retry_after_at,
        response_started=response_started,
        replay_safe=replay_safe,
    )


async def test_retry_after_waits_then_succeeds_with_one_logical_terminal() -> None:
    runtime, sleeper = _runtime()
    inner = _SequenceModel(
        [
            _temporary_failure(retry_after_seconds=2.0),
            Completion(text="ok", usage=Usage(prompt_tokens=7, completion_tokens=3)),
        ]
    )
    model = with_identity(inner, _identity(), retry_runtime=runtime)
    emitter, events = _emitter()

    completion = await complete_model_call(
        model=model,
        messages=[Message(role="user", content="hello")],
        emitter=emitter,
        parent_span_id="parent",
        prompt_version="prompt@v1",
    )

    assert completion.text == "ok"
    assert inner.calls == 2
    assert sleeper.calls == [2.0]
    assert [event.type for event in events] == [
        EventType.MODEL_STARTED,
        EventType.MODEL_ATTEMPT_STARTED,
        EventType.MODEL_ATTEMPT_ENDED,
        EventType.MODEL_RETRY_DECIDED,
        EventType.MODEL_RETRY_WAIT_STARTED,
        EventType.MODEL_RETRY_WAIT_ENDED,
        EventType.MODEL_ATTEMPT_STARTED,
        EventType.MODEL_ATTEMPT_ENDED,
        EventType.MODEL_ENDED,
    ]
    attempts = [event for event in events if event.type == EventType.MODEL_ATTEMPT_ENDED]
    assert attempts[0].payload["usage_status"] == "unknown"
    assert attempts[1].payload["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
    }
    logical_ends = [event for event in events if event.type == EventType.MODEL_ENDED]
    assert len(logical_ends) == 1
    assert logical_ends[0].payload["attempt_count"] == 2
    assert summarize_token_usage(events).total_tokens == 10


async def test_non_retryable_failure_stops_after_one_attempt() -> None:
    runtime, sleeper = _runtime()
    failure = ProviderFailure(
        category=ProviderFailureCategory.AUTHENTICATION,
        retryable=False,
        status_code=401,
    )
    inner = _SequenceModel([failure])
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure) as caught:
        await complete_model_call(
            model=with_identity(inner, _identity(), retry_runtime=runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert caught.value is failure
    assert inner.calls == 1
    assert sleeper.calls == []
    decision = next(event for event in events if event.type == EventType.MODEL_RETRY_DECIDED)
    assert decision.payload["action"] == "stop"
    assert decision.payload["reason"] == "non_retryable"


async def test_retry_after_beyond_deadline_is_not_shortened() -> None:
    policy = ProviderRetryPolicy(deadline_seconds=10.0, max_total_wait_seconds=30.0)
    runtime, sleeper = _runtime(policy=policy)
    inner = _SequenceModel([_temporary_failure(retry_after_seconds=20.0)])
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure):
        await complete_model_call(
            model=with_identity(inner, _identity(), retry_runtime=runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert sleeper.calls == []
    decision = next(event for event in events if event.type == EventType.MODEL_RETRY_DECIDED)
    assert decision.payload["action"] == "stop"
    assert decision.payload["reason"] == "deadline_exhausted"
    assert decision.payload["delay_seconds"] == 20.0


async def test_oversized_retry_after_stops_at_wait_budget_without_sleeping() -> None:
    runtime, sleeper = _runtime()
    inner = _SequenceModel([_temporary_failure(retry_after_seconds=1e100)])
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure):
        await complete_model_call(
            model=with_identity(inner, _identity(), retry_runtime=runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert sleeper.calls == []
    decision = next(event for event in events if event.type == EventType.MODEL_RETRY_DECIDED)
    assert decision.payload["reason"] == "wait_budget_exhausted"


async def test_past_retry_after_date_falls_back_to_local_backoff() -> None:
    runtime, sleeper = _runtime()
    inner = _SequenceModel(
        [
            _temporary_failure(retry_after_at=1_699_999_000.0),
            Completion(text="ok"),
        ]
    )
    emitter, _events = _emitter()

    await complete_model_call(
        model=with_identity(inner, _identity(), retry_runtime=runtime),
        messages=[Message(role="user", content="hello")],
        emitter=emitter,
    )

    assert sleeper.calls == [0.5]


async def test_attempt_limit_includes_initial_request() -> None:
    runtime, sleeper = _runtime()
    inner = _SequenceModel([_temporary_failure(), _temporary_failure(), _temporary_failure()])
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure):
        await complete_model_call(
            model=with_identity(inner, _identity(), retry_runtime=runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert inner.calls == 3
    assert sleeper.calls == [0.5, 1.0]
    decisions = [event for event in events if event.type == EventType.MODEL_RETRY_DECIDED]
    assert decisions[-1].payload["reason"] == "attempt_limit"


async def test_explicitly_unsafe_completion_failure_is_not_replayed() -> None:
    runtime, sleeper = _runtime()
    inner = _SequenceModel([_temporary_failure(replay_safe=False)])
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure):
        await complete_model_call(
            model=with_identity(inner, _identity(), retry_runtime=runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert inner.calls == 1
    assert sleeper.calls == []
    decision = next(event for event in events if event.type == EventType.MODEL_RETRY_DECIDED)
    assert decision.payload["reason"] == "replay_unsafe"


async def test_cancellation_during_retry_wait_closes_wait_and_logical_call() -> None:
    runtime, sleeper = _runtime(cancel_wait=True)
    inner = _SequenceModel([_temporary_failure()])
    emitter, events = _emitter()

    with pytest.raises(asyncio.CancelledError):
        await complete_model_call(
            model=with_identity(inner, _identity(), retry_runtime=runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert sleeper.calls == [0.5]
    wait_end = next(event for event in events if event.type == EventType.MODEL_RETRY_WAIT_ENDED)
    assert wait_end.payload["cancelled"] is True
    logical_end = next(event for event in events if event.type == EventType.MODEL_ENDED)
    assert logical_end.payload["cancelled"] is True
    assert inner.calls == 1


async def test_cancellation_during_request_closes_attempt_and_logical_call() -> None:
    runtime, sleeper = _runtime()
    inner = _SequenceModel([asyncio.CancelledError()])
    emitter, events = _emitter()

    with pytest.raises(asyncio.CancelledError):
        await complete_model_call(
            model=with_identity(inner, _identity(), retry_runtime=runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert sleeper.calls == []
    attempt_end = next(event for event in events if event.type == EventType.MODEL_ATTEMPT_ENDED)
    assert attempt_end.payload["cancelled"] is True
    logical_end = next(event for event in events if event.type == EventType.MODEL_ENDED)
    assert logical_end.payload["cancelled"] is True


async def test_stream_failure_after_visible_delta_is_never_replayed() -> None:
    runtime, sleeper = _runtime()
    failure = _temporary_failure(response_started=True, replay_safe=False)
    inner = _StreamSequenceModel([[TextDelta(text="prefix"), failure]])
    model = with_identity(inner, _identity(), retry_runtime=runtime)
    emitter, events = _emitter()
    deltas: list[str] = []

    with pytest.raises(ProviderFailure):
        await stream_model_call(
            model=model,
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
            on_text_delta=lambda event, _span_id: deltas.append(event.text),
        )

    assert deltas == ["prefix"]
    assert inner.calls == 1
    assert sleeper.calls == []
    decision = next(event for event in events if event.type == EventType.MODEL_RETRY_DECIDED)
    assert decision.payload["reason"] == "replay_unsafe"
    attempt_end = next(event for event in events if event.type == EventType.MODEL_ATTEMPT_ENDED)
    assert attempt_end.payload["output_delivered"] is True


async def test_stream_failure_before_any_response_can_retry() -> None:
    runtime, sleeper = _runtime()
    inner = _StreamSequenceModel(
        [
            [_temporary_failure()],
            [
                TextDelta(text="done"),
                CompletionFinished(completion=Completion(text="done")),
            ],
        ]
    )
    emitter, _events = _emitter()
    deltas: list[str] = []

    completion = await stream_model_call(
        model=with_identity(inner, _identity(), retry_runtime=runtime),
        messages=[Message(role="user", content="hello")],
        emitter=emitter,
        on_text_delta=lambda event, _span_id: deltas.append(event.text),
    )

    assert completion.text == "done"
    assert deltas == ["done"]
    assert inner.calls == 2
    assert sleeper.calls == [0.5]


@pytest.mark.parametrize(
    ("value", "seconds", "timestamp", "invalid"),
    [
        ("12", 12.0, None, False),
        ("Sun, 06 Nov 1994 08:49:37 GMT", None, 784111777.0, False),
        ("-1", None, None, True),
        ("not-a-delay SECRET", None, None, True),
        ("1e999", None, None, True),
    ],
)
def test_retry_after_parser_supports_seconds_and_http_date_without_leaking_raw_value(
    value: str,
    seconds: float | None,
    timestamp: float | None,
    invalid: bool,
) -> None:
    parsed = parse_retry_after(value)

    assert parsed.seconds == seconds
    assert parsed.utc_timestamp == timestamp
    assert parsed.invalid is invalid
    assert value not in repr(parsed)

"""Explicit fallback: authorized candidates share retry budgets and replay safety."""

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.model_execution import complete_model_call, stream_model_call
from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    ProviderStreamEvent,
    TextDelta,
    ToolSpec,
)
from grandquiz.providers.budget import budget_model
from grandquiz.providers.failure import ProviderFailure, ProviderFailureCategory
from grandquiz.providers.fallback import ProviderFallbackPolicy
from grandquiz.providers.models import (
    ModelExecutionCandidate,
    ModelFallbackPlan,
    fallback_plan_of,
    with_identity,
)
from grandquiz.providers.profiles import ModelCapabilities, ModelIdentity, SelectionSource
from grandquiz.providers.retry import ProviderRetryPolicy, RetryRuntime


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def utc_timestamp(self) -> float:
        return 1_700_000_000.0 + self.now


class _Sleeper:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.now += seconds


class _Rng:
    def random(self) -> float:
        return 0.5


class _Counter:
    def count(self, text: str) -> int:
        return len(text)


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


class _StreamModel:
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


def _identity(value: str, *, source: SelectionSource = "default") -> ModelIdentity:
    return ModelIdentity(
        purpose="chat",
        selection_source=source,
        configuration_fingerprint=value * 64,
        policy_fingerprint="9" * 64,
    )


def _failure(
    category: ProviderFailureCategory = ProviderFailureCategory.RATE_LIMITED,
    *,
    response_started: bool = False,
) -> ProviderFailure:
    return ProviderFailure(
        category=category,
        retryable=category
        in {
            ProviderFailureCategory.RATE_LIMITED,
            ProviderFailureCategory.TIMEOUT,
            ProviderFailureCategory.CONNECTION,
            ProviderFailureCategory.SERVER_ERROR,
        },
        status_code=429 if category == ProviderFailureCategory.RATE_LIMITED else None,
        response_started=response_started,
    )


def _runtime(*, max_attempts: int = 3) -> tuple[RetryRuntime, _Sleeper]:
    clock = _Clock()
    sleeper = _Sleeper(clock)
    return (
        RetryRuntime(
            policy=ProviderRetryPolicy(max_attempts=max_attempts),
            clock=clock,
            sleeper=sleeper,
            rng=_Rng(),
        ),
        sleeper,
    )


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="fallback"), events


def _fallback_model(
    models: Sequence[_SequenceModel | _StreamModel],
    runtime: RetryRuntime,
    *,
    local_attempts: int = 1,
    capabilities: Sequence[ModelCapabilities] | None = None,
):
    candidate_capabilities = capabilities or [
        ModelCapabilities(tools="supported", native_streaming="supported") for _ in models
    ]
    candidates = tuple(
        ModelExecutionCandidate(
            model=model,
            identity=_identity(str(index + 1), source="default" if index == 0 else "fallback"),
            capabilities=candidate_capabilities[index],
        )
        for index, model in enumerate(models)
    )
    plan = ModelFallbackPlan(
        candidates=candidates,
        policy=ProviderFallbackPolicy(
            enabled=True,
            max_attempts_per_candidate=local_attempts,
        ),
    )
    return with_identity(
        models[0],
        candidates[0].identity,
        retry_runtime=runtime,
        fallback_plan=plan,
    )


async def test_retry_and_fallback_share_three_total_attempts() -> None:
    runtime, sleeper = _runtime(max_attempts=3)
    primary = _SequenceModel([_failure(), _failure()])
    backup = _SequenceModel([Completion(text="backup")])
    emitter, events = _emitter()

    completion = await complete_model_call(
        model=_fallback_model([primary, backup], runtime, local_attempts=2),
        messages=[Message(role="user", content="hello")],
        emitter=emitter,
    )

    assert completion.text == "backup"
    assert primary.calls == 2
    assert backup.calls == 1
    assert sleeper.calls == [0.5]
    assert sum(event.type == EventType.MODEL_ATTEMPT_STARTED for event in events) == 3
    switched = [event for event in events if event.type == EventType.MODEL_FALLBACK_DECIDED]
    assert len(switched) == 1
    assert switched[0].payload["action"] == "switch"
    assert switched[0].payload["from_candidate"] == 1
    assert switched[0].payload["to_candidate"] == 2
    ended = next(event for event in events if event.type == EventType.MODEL_ENDED)
    assert ended.payload["fallback_count"] == 1
    assert ended.payload["selected_model_identity"]["configuration_fingerprint"] == "2" * 64


async def test_global_attempt_limit_prevents_each_candidate_getting_a_fresh_budget() -> None:
    runtime, _ = _runtime(max_attempts=3)
    first = _SequenceModel([_failure(), _failure()])
    second = _SequenceModel([_failure()])
    third = _SequenceModel([Completion(text="must-not-run")])
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure):
        await complete_model_call(
            model=_fallback_model([first, second, third], runtime, local_attempts=2),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert (first.calls, second.calls, third.calls) == (2, 1, 0)
    assert sum(event.type == EventType.MODEL_ATTEMPT_STARTED for event in events) == 3
    ended = next(event for event in events if event.type == EventType.MODEL_ENDED)
    assert ended.payload["attempt_count"] == 3
    assert len(ended.payload["provider_failure_chain"]) == 3


async def test_authentication_failure_never_switches_provider() -> None:
    runtime, _ = _runtime()
    primary = _SequenceModel([_failure(ProviderFailureCategory.AUTHENTICATION)])
    backup = _SequenceModel([Completion(text="must-not-run")])
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure):
        await complete_model_call(
            model=_fallback_model([primary, backup], runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert backup.calls == 0
    decision = next(event for event in events if event.type == EventType.MODEL_FALLBACK_DECIDED)
    assert decision.payload["action"] == "stop"
    assert decision.payload["reason"] == "failure_not_allowed"


async def test_unknown_exception_is_not_swallowed_or_sent_to_a_backup() -> None:
    runtime, _ = _runtime()
    failure = RuntimeError("local contract bug")
    primary = _SequenceModel([failure])
    backup = _SequenceModel([Completion(text="must-not-run")])
    emitter, events = _emitter()

    with pytest.raises(RuntimeError) as caught:
        await complete_model_call(
            model=_fallback_model([primary, backup], runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert caught.value is failure
    assert backup.calls == 0
    assert not any(event.type == EventType.MODEL_FALLBACK_DECIDED for event in events)


async def test_retry_after_beyond_deadline_can_switch_without_waiting() -> None:
    runtime, sleeper = _runtime()
    unavailable = ProviderFailure(
        category=ProviderFailureCategory.RATE_LIMITED,
        retryable=True,
        retry_after_seconds=120.0,
    )
    primary = _SequenceModel([unavailable])
    backup = _SequenceModel([Completion(text="backup")])
    emitter, _ = _emitter()

    completion = await complete_model_call(
        model=_fallback_model([primary, backup], runtime, local_attempts=2),
        messages=[Message(role="user", content="hello")],
        emitter=emitter,
    )

    assert completion.text == "backup"
    assert sleeper.calls == []


async def test_cancelled_call_never_switches_candidate() -> None:
    runtime, _ = _runtime()
    primary = _SequenceModel([asyncio.CancelledError()])
    backup = _SequenceModel([Completion(text="must-not-run")])
    emitter, events = _emitter()

    with pytest.raises(asyncio.CancelledError):
        await complete_model_call(
            model=_fallback_model([primary, backup], runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
        )

    assert backup.calls == 0
    assert not any(event.type == EventType.MODEL_FALLBACK_DECIDED for event in events)


async def test_stream_failure_after_any_upstream_event_never_replays_on_backup() -> None:
    runtime, _ = _runtime()
    primary = _StreamModel([[TextDelta(text="partial"), _failure(response_started=True)]])
    backup = _StreamModel(
        [[TextDelta(text="backup"), CompletionFinished(completion=Completion(text="backup"))]]
    )
    emitter, events = _emitter()
    delivered: list[str] = []

    with pytest.raises(ProviderFailure):
        await stream_model_call(
            model=_fallback_model([primary, backup], runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
            on_text_delta=lambda delta, _span: delivered.append(delta.text),
        )

    assert delivered == ["partial"]
    assert backup.calls == 0
    decision = next(event for event in events if event.type == EventType.MODEL_FALLBACK_DECIDED)
    assert decision.payload["reason"] == "replay_unsafe"


async def test_stream_failure_after_completion_event_never_replays_on_backup() -> None:
    runtime, _ = _runtime()
    primary = _StreamModel(
        [[CompletionFinished(completion=Completion(text="")), _failure(response_started=False)]]
    )
    backup = _StreamModel(
        [[TextDelta(text="backup"), CompletionFinished(completion=Completion(text="backup"))]]
    )
    emitter, events = _emitter()

    with pytest.raises(ProviderFailure):
        await stream_model_call(
            model=_fallback_model([primary, backup], runtime),
            messages=[Message(role="user", content="hello")],
            emitter=emitter,
            on_text_delta=lambda _delta, _span: None,
        )

    assert backup.calls == 0
    decision = next(event for event in events if event.type == EventType.MODEL_FALLBACK_DECIDED)
    assert decision.payload["reason"] == "replay_unsafe"


async def test_stream_can_switch_before_any_upstream_event() -> None:
    runtime, _ = _runtime()
    primary = _StreamModel([[_failure()]])
    backup = _StreamModel(
        [[TextDelta(text="backup"), CompletionFinished(completion=Completion(text="backup"))]]
    )
    emitter, events = _emitter()
    delivered: list[str] = []

    completion = await stream_model_call(
        model=_fallback_model([primary, backup], runtime),
        messages=[Message(role="user", content="hello")],
        emitter=emitter,
        on_text_delta=lambda delta, _span: delivered.append(delta.text),
    )

    assert completion.text == "backup"
    assert delivered == ["backup"]
    assert primary.calls == backup.calls == 1
    assert any(
        event.type == EventType.MODEL_FALLBACK_DECIDED and event.payload["action"] == "switch"
        for event in events
    )


async def test_ineligible_tool_candidate_is_skipped_without_a_transport_attempt() -> None:
    runtime, _ = _runtime()
    first = _SequenceModel([_failure()])
    ineligible = _SequenceModel([Completion(text="must-not-run")])
    eligible = _SequenceModel([Completion(text="eligible")])
    capabilities = [
        ModelCapabilities(tools="supported"),
        ModelCapabilities(tools="unsupported"),
        ModelCapabilities(tools="supported"),
    ]
    emitter, events = _emitter()

    completion = await complete_model_call(
        model=_fallback_model(
            [first, ineligible, eligible],
            runtime,
            capabilities=capabilities,
        ),
        messages=[Message(role="user", content="hello")],
        tools=[ToolSpec(name="lookup", description="lookup", parameters={"type": "object"})],
        emitter=emitter,
    )

    assert completion.text == "eligible"
    assert (first.calls, ineligible.calls, eligible.calls) == (1, 0, 1)
    decision = next(event for event in events if event.type == EventType.MODEL_FALLBACK_DECIDED)
    assert decision.payload["to_candidate"] == 3


async def test_request_budget_wrapper_preserves_the_frozen_fallback_plan() -> None:
    runtime, _ = _runtime()
    primary = _SequenceModel([_failure()])
    backup = _SequenceModel([Completion(text="backup")])
    wrapped = budget_model(
        _fallback_model([primary, backup], runtime),
        counter=_Counter(),
        ceiling=10_000,
    )
    emitter, _ = _emitter()

    assert fallback_plan_of(wrapped) is not None
    completion = await complete_model_call(
        model=wrapped,
        messages=[Message(role="user", content="hello")],
        emitter=emitter,
    )

    assert completion.text == "backup"
    assert primary.calls == backup.calls == 1

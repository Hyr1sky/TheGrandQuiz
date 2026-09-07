"""Versioned replay at the new bound-model interface; no legacy key fallback."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from grandquiz.evals.quality import QualityJudge, QualityRequest
from grandquiz.evals.resources import MODEL_IDENTITY_EVAL_CASSETTE, eval_fixture_path
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.model_execution import complete_model_call
from grandquiz.providers.base import Completion, Message, Model, ToolSpec
from grandquiz.providers.failure import ProviderFailure, ProviderFailureCategory
from grandquiz.providers.fallback import ProviderFallbackPolicy
from grandquiz.providers.model_replay import ModelCassette, RecordingModel, ReplayModel
from grandquiz.providers.models import ModelExecutionCandidate, ModelFallbackPlan, with_identity
from grandquiz.providers.profiles import ModelCapabilities, ModelIdentity
from grandquiz.providers.replay import ReplayMiss
from grandquiz.providers.retry import ProviderRetryPolicy, RetryRuntime


class SequenceModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        return Completion(text=f"result-{self.calls}")


def identity(purpose: str = "question_generation", fingerprint: str = "1") -> ModelIdentity:
    return ModelIdentity(
        purpose=purpose,
        selection_source="default",
        configuration_fingerprint=fingerprint * 64,
        policy_fingerprint="2" * 64,
    )


async def test_v4_recording_round_trips_in_sequence_without_network(tmp_path: Path) -> None:
    source = SequenceModel()
    cassette = ModelCassette()
    recorder = RecordingModel(with_identity(source, identity()), cassette)
    messages = [Message(role="user", content="same")]
    assert (await recorder.complete(messages)).text == "result-1"
    assert (await recorder.complete(messages)).text == "result-2"
    path = tmp_path / "recording.json"
    cassette.save(path)
    replay = ReplayModel(ModelCassette.load(path), identity())
    assert (await replay.complete(messages)).text == "result-1"
    assert (await replay.complete(messages)).text == "result-2"
    with pytest.raises(ReplayMiss):
        await replay.complete(messages)
    assert source.calls == 2
    assert '"model-cassette.v4"' in path.read_text()


async def test_v4_never_confuses_purpose_deployment_or_tool_contract() -> None:
    cassette = ModelCassette()
    messages = [Message(role="user", content="same")]
    tool = ToolSpec(name="lookup", description="look up a concept", parameters={"type": "object"})
    await RecordingModel(with_identity(SequenceModel(), identity()), cassette).complete(
        messages, tools=[tool]
    )
    for changed in (identity("answer_grading"), identity(fingerprint="3")):
        with pytest.raises(ReplayMiss):
            await ReplayModel(cassette, changed).complete(messages, tools=[tool])
    with pytest.raises(ReplayMiss):
        await ReplayModel(cassette, identity()).complete(messages)


def test_model_reader_rejects_legacy_data_instead_of_trying_old_keys(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text('{"old-key":{"text":"legacy"}}')
    with pytest.raises(ReplayMiss, match="模型录制"):
        ModelCassette.load(path)


class _RetryClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def utc_timestamp(self) -> float:
        return 1_700_000_000.0 + self.now


class _Sleeper:
    def __init__(self, clock: _RetryClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.now += seconds


class _FixedRng:
    def random(self) -> float:
        return 0.5


class _FailureThenSuccessModel:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            raise ProviderFailure(
                category=ProviderFailureCategory.RATE_LIMITED,
                retryable=True,
                status_code=429,
                retry_after_seconds=2.0,
            )
        return Completion(text="recovered")


def _retry_runtime() -> tuple[RetryRuntime, _Sleeper]:
    clock = _RetryClock()
    sleeper = _Sleeper(clock)
    return (
        RetryRuntime(
            policy=ProviderRetryPolicy(),
            clock=clock,
            sleeper=sleeper,
            rng=_FixedRng(),
        ),
        sleeper,
    )


def _emitter(trace_id: str) -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id=trace_id), events


async def test_v4_replays_failure_retry_success_with_identical_decisions(
    tmp_path: Path,
) -> None:
    messages = [Message(role="user", content="same")]
    source = _FailureThenSuccessModel()
    record_runtime, record_sleeper = _retry_runtime()
    recorder = RecordingModel(
        with_identity(source, identity(), retry_runtime=record_runtime),
        ModelCassette(),
        checkpoint_path=tmp_path / "retry.json",
    )
    record_emitter, record_events = _emitter("record")

    recorded = await complete_model_call(
        model=recorder,
        messages=messages,
        emitter=record_emitter,
    )

    replay_runtime, replay_sleeper = _retry_runtime()
    replay = ReplayModel(
        ModelCassette.load(tmp_path / "retry.json"),
        identity(),
        retry_runtime=replay_runtime,
    )
    replay_emitter, replay_events = _emitter("replay")
    restored = await complete_model_call(
        model=replay,
        messages=messages,
        emitter=replay_emitter,
    )

    assert recorded == restored == Completion(text="recovered")
    assert source.calls == 2
    assert record_sleeper.calls == replay_sleeper.calls == [2.0]
    relevant = {
        EventType.MODEL_ATTEMPT_STARTED,
        EventType.MODEL_ATTEMPT_ENDED,
        EventType.MODEL_RETRY_DECIDED,
        EventType.MODEL_RETRY_WAIT_STARTED,
        EventType.MODEL_RETRY_WAIT_ENDED,
    }
    assert [event.type for event in record_events if event.type in relevant] == [
        event.type for event in replay_events if event.type in relevant
    ]


class _AlwaysUnavailableModel:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, tools
        raise ProviderFailure(
            category=ProviderFailureCategory.SERVER_ERROR,
            retryable=True,
            status_code=503,
        )


def _fallback_bound_model(
    primary: Model,
    backup: Model,
    runtime: RetryRuntime,
) -> Model:
    primary_identity = identity(fingerprint="4")
    backup_identity = identity(fingerprint="5").model_copy(update={"selection_source": "fallback"})
    capabilities = ModelCapabilities(tools="supported", native_streaming="supported")
    plan = ModelFallbackPlan(
        candidates=(
            ModelExecutionCandidate(
                model=primary,
                identity=primary_identity,
                capabilities=capabilities,
            ),
            ModelExecutionCandidate(
                model=backup,
                identity=backup_identity,
                capabilities=capabilities,
            ),
        ),
        policy=ProviderFallbackPolicy(enabled=True, max_attempts_per_candidate=1),
    )
    return with_identity(
        primary,
        primary_identity,
        retry_runtime=runtime,
        fallback_plan=plan,
    )


async def test_v4_replays_the_frozen_candidate_chain_and_fallback_decision(
    tmp_path: Path,
) -> None:
    messages = [Message(role="user", content="same")]
    record_runtime, _ = _retry_runtime()
    checkpoint = tmp_path / "fallback.json"
    recorder = RecordingModel(
        _fallback_bound_model(_AlwaysUnavailableModel(), SequenceModel(), record_runtime),
        ModelCassette(),
        checkpoint_path=checkpoint,
    )
    record_emitter, record_events = _emitter("record-fallback")

    recorded = await complete_model_call(
        model=recorder,
        messages=messages,
        emitter=record_emitter,
    )

    cassette = ModelCassette.load(checkpoint)
    replay_runtime, _ = _retry_runtime()
    primary_identity = identity(fingerprint="4")
    backup_identity = identity(fingerprint="5").model_copy(update={"selection_source": "fallback"})
    replay = _fallback_bound_model(
        ReplayModel(cassette, primary_identity),
        ReplayModel(cassette, backup_identity),
        replay_runtime,
    )
    replay_emitter, replay_events = _emitter("replay-fallback")
    restored = await complete_model_call(
        model=replay,
        messages=messages,
        emitter=replay_emitter,
    )

    assert recorded == restored == Completion(text="result-1")
    relevant = {
        EventType.MODEL_ATTEMPT_STARTED,
        EventType.MODEL_ATTEMPT_ENDED,
        EventType.MODEL_FALLBACK_DECIDED,
    }
    assert [event.type for event in record_events if event.type in relevant] == [
        event.type for event in replay_events if event.type in relevant
    ]


async def test_packaged_v3_fixture_drives_real_quality_consumer_with_identity() -> None:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="v3-eval")
    replay = ReplayModel(
        ModelCassette.load(eval_fixture_path(MODEL_IDENTITY_EVAL_CASSETTE)),
        identity("eval_quality"),
    )

    result = await QualityJudge(provider=replay, max_attempts=1).evaluate(
        QualityRequest(
            rubric_id="grounded_answer",
            question="什么是事件信封？",
            candidate="AgentEvent 使用 type、元数据和不透明 payload。",
            reference="AgentEvent 是包含 type、元数据和不透明 payload 的事件信封。",
        ),
        emitter=emitter,
    )

    assert result.passed is True
    started = next(event for event in events if event.type == EventType.MODEL_STARTED)
    assert started.payload["model_identity"]["purpose"] == "eval_quality"

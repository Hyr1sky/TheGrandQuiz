"""Versioned replay at the new bound-model interface; no legacy key fallback."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from grandquiz.evals.quality import QualityJudge, QualityRequest
from grandquiz.evals.resources import MODEL_IDENTITY_EVAL_CASSETTE, eval_fixture_path
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, ToolSpec
from grandquiz.providers.model_replay import ModelCassette, RecordingModel, ReplayModel
from grandquiz.providers.models import with_identity
from grandquiz.providers.profiles import ModelIdentity
from grandquiz.providers.replay import ReplayMiss


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


async def test_v3_recording_round_trips_in_sequence_without_network(tmp_path: Path) -> None:
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
    assert '"model-cassette.v3"' in path.read_text()


async def test_v3_never_confuses_purpose_deployment_or_tool_contract() -> None:
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


def test_v3_reader_rejects_legacy_data_instead_of_trying_old_keys(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text('{"old-key":{"text":"legacy"}}')
    with pytest.raises(ReplayMiss, match="v3"):
        ModelCassette.load(path)


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

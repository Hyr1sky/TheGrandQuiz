"""Persistent VoiceRun state machine before it is exposed through FastAPI."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections import deque
from collections.abc import Sequence
from pathlib import Path

import pytest

from grandquiz.domain.learning.recognition_lexicon import (
    TranscriptionHintEntry,
    TranscriptionHints,
)
from grandquiz.interfaces.api.assessment_runs import (
    AnswerSubmissionRequest,
    AssessmentQuestionView,
    AssessmentView,
)
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.api.voice_runs import (
    VoiceRunCommandConflict,
    VoiceRunManager,
    VoiceRunStartCommand,
    VoiceRunSubmitCommand,
)
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.speech import (
    SpeechRecognitionError,
    TranscriptionRequest,
    TranscriptionResult,
)


class _MutableClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def now(self) -> float:
        return self.value


class _HintSelector:
    def select_for_items(self, item_ids: Sequence[str]) -> TranscriptionHints:
        assert list(item_ids) == ["item-1"]
        return TranscriptionHints(
            hint_set_id="hints-1",
            lexicon_ids=("lexicon-1",),
            item_ids=("item-1",),
            selector_version="selector.v1",
            entries=(TranscriptionHintEntry(entry_id="entry-1", term="ReAct", priority=5),),
        )


def _assessment_view(
    status: str = "awaiting_answer",
    *,
    question_type: str = "开放",
) -> AssessmentView:
    return AssessmentView(
        session_id="assessment-1",
        trace_id="assessment-trace-1",
        status=status,  # type: ignore[arg-type]
        round_index=1,
        rounds=1,
        question=AssessmentQuestionView(
            question_id="question-1",
            item_id="item-1",
            text="请解释 ReAct。",
            question_type=question_type,
            options=[] if question_type == "开放" else ["A", "B"],
            evidence_revealed=False,
            evidence=[],
        ),
    )


class _AssessmentPort:
    def __init__(self) -> None:
        self.view = _assessment_view()
        self.submissions: list[AnswerSubmissionRequest] = []

    def get(self, session_id: str) -> AssessmentView | None:
        return self.view if session_id == "assessment-1" else None

    def submit_answer(
        self,
        session_id: str,
        question_id: str,
        command: AnswerSubmissionRequest,
    ) -> AssessmentView | None:
        assert session_id == "assessment-1"
        assert question_id == "question-1"
        if self.submissions:
            previous = self.submissions[0]
            if previous == command:
                return self.view
            raise AssertionError("assessment received two different submissions")
        self.submissions.append(command)
        self.view = _assessment_view("grading")
        return self.view


class _MissingAssessmentPort(_AssessmentPort):
    def get(self, session_id: str) -> AssessmentView | None:
        del session_id
        return None


class _QueuedSpeechProvider:
    provider_identity = "fake-speech"

    def __init__(self, *outcomes: TranscriptionResult | SpeechRecognitionError) -> None:
        self.outcomes = deque(outcomes)
        self.requests: list[TranscriptionRequest] = []

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.requests.append(request)
        outcome = self.outcomes.popleft()
        if isinstance(outcome, SpeechRecognitionError):
            raise outcome
        return outcome


class _LateSpeechProvider:
    provider_identity = "late-speech"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
        return TranscriptionResult(
            transcript="不应晋升的迟到结果",
            provider_request_id="late-provider-request",
            latency_ms=100,
        )


class _BlockingSpeechProvider:
    provider_identity = "blocking-speech"

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _start_command(*, request_id: str = "voice-command-1") -> VoiceRunStartCommand:
    return VoiceRunStartCommand(
        request_id=request_id,
        assessment_session_id="assessment-1",
        question_id="question-1",
        mime_type="audio/webm;codecs=opus",
        client_duration_ms=12_000,
    )


async def _wait_for_status(
    manager: VoiceRunManager,
    voice_run_id: str,
    expected: str,
) -> None:
    for _ in range(50):
        current = manager.get(voice_run_id)
        if current is not None and current.status == expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"VoiceRun did not reach {expected}: {manager.get(voice_run_id)}")


@pytest.mark.asyncio
async def test_voice_run_is_idempotent_and_submits_through_assessment_once(
    tmp_path: Path,
) -> None:
    assessment = _AssessmentPort()
    speech = _QueuedSpeechProvider(
        TranscriptionResult(
            transcript="ReAct 交替执行推理与动作。",
            provider_request_id="provider-1",
            provider_audio_duration_ms=12_000,
            latency_ms=125,
        )
    )
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=speech,
        hints=_HintSelector(),
        assessments=assessment,
        clock=_MutableClock(),
    )
    try:
        manager.set_hints_enabled(True)
        started = manager.start(_start_command(), b"private-webm")
        manager.set_hints_enabled(False)
        duplicate = manager.start(_start_command(), b"private-webm")
        assert duplicate.voice_run_id == started.voice_run_id
        await _wait_for_status(manager, started.voice_run_id, "reviewable")

        reviewable = manager.get(started.voice_run_id)
        assert reviewable is not None
        assert reviewable.reviewable_transcript == "ReAct 交替执行推理与动作。"
        assert reviewable.hint_set_id == "hints-1"
        assert reviewable.hint_count == 1
        assert len(speech.requests) == 1
        assert speech.requests[0].hints.entries[0].term == "ReAct"
        assert speech.requests[0].material_hints_enabled is True

        submitted = manager.submit(
            started.voice_run_id,
            VoiceRunSubmitCommand(
                request_id="voice-submit-1",
                edited_text="ReAct 是推理与动作交替的流程。",
            ),
        )
        duplicate_submit = manager.submit(
            started.voice_run_id,
            VoiceRunSubmitCommand(
                request_id="voice-submit-1",
                edited_text="ReAct 是推理与动作交替的流程。",
            ),
        )
    finally:
        await manager.aclose()

    assert submitted.status == "submitted"
    assert submitted.reviewable_transcript is None
    assert duplicate_submit == submitted
    assert len(assessment.submissions) == 1
    assert assessment.submissions[0].input_modality == "voice"


@pytest.mark.asyncio
async def test_reviewable_voice_run_cannot_submit_after_assessment_moves_to_next_question(
    tmp_path: Path,
) -> None:
    assessment = _AssessmentPort()
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=_QueuedSpeechProvider(
            TranscriptionResult(transcript="旧题语音草稿", latency_ms=10)
        ),
        hints=_HintSelector(),
        assessments=assessment,
        clock=_MutableClock(),
    )
    try:
        started = manager.start(_start_command(), b"private-webm")
        await _wait_for_status(manager, started.voice_run_id, "reviewable")
        assert assessment.view.question is not None
        assessment.view = assessment.view.model_copy(
            update={
                "question": assessment.view.question.model_copy(
                    update={"question_id": "question-2"}
                )
            }
        )

        with pytest.raises(VoiceRunCommandConflict, match="题目已经变化"):
            manager.submit(
                started.voice_run_id,
                VoiceRunSubmitCommand(
                    request_id="stale-submit",
                    edited_text="旧题语音草稿",
                ),
            )
        persisted = manager.get(started.voice_run_id)
    finally:
        await manager.aclose()

    assert persisted is not None
    assert persisted.status == "reviewable"
    assert assessment.submissions == []


@pytest.mark.asyncio
async def test_voice_run_rejects_idempotency_key_reuse_with_different_audio(
    tmp_path: Path,
) -> None:
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=_LateSpeechProvider(),
        hints=_HintSelector(),
        assessments=_AssessmentPort(),
        clock=_MutableClock(),
    )
    try:
        manager.start(_start_command(), b"audio-one")
        with pytest.raises(VoiceRunCommandConflict):
            manager.start(_start_command(), b"audio-two")
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_voice_run_rejects_non_open_assessment_question(tmp_path: Path) -> None:
    assessments = _AssessmentPort()
    assessments.view = _assessment_view(question_type="选择题")
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=_LateSpeechProvider(),
        hints=_HintSelector(),
        assessments=assessments,
        clock=_MutableClock(),
    )
    try:
        with pytest.raises(VoiceRunCommandConflict, match="仅适用于当前开放题"):
            manager.start(_start_command(), b"private-webm")
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_cancel_request_prevents_late_upload_from_creating_voice_run(
    tmp_path: Path,
) -> None:
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=_LateSpeechProvider(),
        hints=_HintSelector(),
        assessments=_AssessmentPort(),
        clock=_MutableClock(),
    )
    try:
        assert manager.cancel_request("voice-command-1") is None
        with pytest.raises(VoiceRunCommandConflict, match="已经取消"):
            manager.start(_start_command(), b"private-webm")
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_cancelled_voice_run_ignores_provider_late_result(tmp_path: Path) -> None:
    speech = _LateSpeechProvider()
    trace_store = TraceStore(tmp_path / "trace.db")
    observatory = TraceObservatory(trace_store)
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=speech,
        hints=_HintSelector(),
        assessments=_AssessmentPort(),
        clock=_MutableClock(),
        trace_store=trace_store,
        trace_observatory=observatory,
    )
    try:
        started = manager.start(_start_command(), b"private-webm")
        await speech.started.wait()
        cancelled = manager.cancel(started.voice_run_id)
        speech.release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        persisted = manager.get(started.voice_run_id)
        snapshot = observatory.snapshot(started.trace_id)
    finally:
        await manager.aclose()
        trace_store.close()

    with sqlite3.connect(tmp_path / "voice.db") as audit_conn:
        attempt_audit = audit_conn.execute(
            "SELECT status, provider_request_id, ended_at FROM voice_provider_attempts"
        ).fetchone()

    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert persisted is not None
    assert persisted.status == "cancelled"
    assert persisted.reviewable_transcript is None
    assert snapshot.summary.status == "cancelled"
    assert len(snapshot.spans) == 2
    assert all(span.ended_at is not None for span in snapshot.spans)
    assert all(span.status == "failed" for span in snapshot.spans)
    assert attempt_audit is not None
    assert attempt_audit[0] == "cancelled"
    assert attempt_audit[1] == "late-provider-request"
    assert attempt_audit[2] is not None


@pytest.mark.asyncio
async def test_failed_voice_run_allows_one_explicit_retry_only(tmp_path: Path) -> None:
    speech = _QueuedSpeechProvider(
        SpeechRecognitionError(
            "provider_rate_limited",
            "语音服务限流",
            retryable=True,
            provider_status=429,
        ),
        TranscriptionResult(transcript="第二次成功", latency_ms=100),
    )
    trace_store = TraceStore(tmp_path / "trace.db")
    observatory = TraceObservatory(trace_store)
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=speech,
        hints=_HintSelector(),
        assessments=_AssessmentPort(),
        clock=_MutableClock(),
        trace_store=trace_store,
        trace_observatory=observatory,
    )
    try:
        started = manager.start(_start_command(), b"private-webm")
        await _wait_for_status(manager, started.voice_run_id, "failed")
        failed = manager.get(started.voice_run_id)
        assert failed is not None
        assert failed.retryable is True
        assert failed.error is not None
        assert failed.error.code == "provider_rate_limited"

        manager.retry(started.voice_run_id, request_id="retry-1", audio_bytes=b"private-webm")
        await _wait_for_status(manager, started.voice_run_id, "reviewable")
        reviewable = manager.get(started.voice_run_id)
        retry_snapshot = observatory.snapshot(started.trace_id)
        with pytest.raises(VoiceRunCommandConflict):
            manager.retry(
                started.voice_run_id,
                request_id="retry-1",
                audio_bytes=b"different-audio",
            )
    finally:
        await manager.aclose()
        trace_store.close()

    assert reviewable is not None
    assert reviewable.provider_attempt_count == 2
    assert reviewable.retryable is False
    assert len(speech.requests) == 2
    assert retry_snapshot.summary.status == "waiting_input"
    assert [span.status for span in retry_snapshot.spans] == [
        "failed",
        "failed",
        "running",
        "completed",
    ]


@pytest.mark.asyncio
async def test_restart_recovery_and_review_ttl_are_persistent(tmp_path: Path) -> None:
    db_path = tmp_path / "voice.db"
    clock = _MutableClock()
    assessment = _AssessmentPort()
    first = VoiceRunManager(
        db_path=db_path,
        speech_provider=_QueuedSpeechProvider(
            TranscriptionResult(transcript="可恢复草稿", latency_ms=100)
        ),
        hints=_HintSelector(),
        assessments=assessment,
        clock=clock,
    )
    started = first.start(_start_command(), b"private-webm")
    await _wait_for_status(first, started.voice_run_id, "reviewable")
    await first.aclose()

    restarted = VoiceRunManager(
        db_path=db_path,
        speech_provider=_QueuedSpeechProvider(),
        hints=_HintSelector(),
        assessments=assessment,
        clock=clock,
    )
    try:
        restored = restarted.get(started.voice_run_id)
        assert restored is not None
        assert restored.reviewable_transcript == "可恢复草稿"

        clock.value += 30 * 60 + 1
        expired = restarted.get(started.voice_run_id)
    finally:
        await restarted.aclose()

    assert expired is not None
    assert expired.status == "expired"
    assert expired.reviewable_transcript is None


@pytest.mark.asyncio
async def test_review_ttl_is_swept_without_a_get_request(tmp_path: Path) -> None:
    clock = _MutableClock()
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=_QueuedSpeechProvider(
            TranscriptionResult(transcript="短期草稿", latency_ms=10)
        ),
        hints=_HintSelector(),
        assessments=_AssessmentPort(),
        clock=clock,
        sweep_interval_seconds=0.001,
    )
    try:
        started = manager.start(_start_command(), b"private-webm")
        await _wait_for_status(manager, started.voice_run_id, "reviewable")
        clock.value += 30 * 60 + 1
        for _ in range(20):
            await asyncio.sleep(0.002)
            with sqlite3.connect(tmp_path / "voice.db") as audit_conn:
                row = audit_conn.execute(
                    "SELECT status FROM voice_runs WHERE voice_run_id=?",
                    (started.voice_run_id,),
                ).fetchone()
            if row is not None and row[0] == "expired":
                break
        swept = manager.get(started.voice_run_id)
    finally:
        await manager.aclose()

    assert swept is not None
    assert swept.status == "expired"


@pytest.mark.asyncio
async def test_restart_expires_review_when_assessment_session_cannot_be_restored(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "voice.db"
    clock = _MutableClock()
    first = VoiceRunManager(
        db_path=db_path,
        speech_provider=_QueuedSpeechProvider(
            TranscriptionResult(transcript="不可提交的草稿", latency_ms=10)
        ),
        hints=_HintSelector(),
        assessments=_AssessmentPort(),
        clock=clock,
    )
    started = first.start(_start_command(), b"private-webm")
    await _wait_for_status(first, started.voice_run_id, "reviewable")
    await first.aclose()

    restarted = VoiceRunManager(
        db_path=db_path,
        speech_provider=_QueuedSpeechProvider(),
        hints=_HintSelector(),
        assessments=_MissingAssessmentPort(),
        clock=clock,
    )
    try:
        recovered = restarted.get(started.voice_run_id)
    finally:
        await restarted.aclose()

    assert recovered is not None
    assert recovered.status == "expired"
    assert recovered.reviewable_transcript is None


@pytest.mark.asyncio
async def test_service_stop_converges_interrupted_transcription_to_retryable_failure(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "voice.db"
    clock = _MutableClock()
    assessment = _AssessmentPort()
    blocking = _BlockingSpeechProvider()
    trace_store = TraceStore(tmp_path / "trace.db")
    observatory = TraceObservatory(trace_store)
    first = VoiceRunManager(
        db_path=db_path,
        speech_provider=blocking,
        hints=_HintSelector(),
        assessments=assessment,
        clock=clock,
        trace_store=trace_store,
        trace_observatory=observatory,
    )
    started = first.start(_start_command(), b"private-webm")
    await blocking.started.wait()
    await first.aclose()
    stopped_snapshot = observatory.snapshot(started.trace_id)

    restarted = VoiceRunManager(
        db_path=db_path,
        speech_provider=_QueuedSpeechProvider(),
        hints=_HintSelector(),
        assessments=assessment,
        clock=clock,
        trace_store=trace_store,
        trace_observatory=observatory,
    )
    try:
        recovered = restarted.get(started.voice_run_id)
    finally:
        await restarted.aclose()
        trace_store.close()

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.retryable is True
    assert recovered.error is not None
    assert recovered.error.code == "service_stopped"
    assert stopped_snapshot.summary.status == "failed"
    assert len(stopped_snapshot.spans) == 2
    assert all(span.ended_at is not None for span in stopped_snapshot.spans)


@pytest.mark.asyncio
async def test_voice_trace_is_balanced_and_excludes_audio_transcript_and_terms(
    tmp_path: Path,
) -> None:
    trace_store = TraceStore(tmp_path / "trace.db")
    observatory = TraceObservatory(trace_store)
    manager = VoiceRunManager(
        db_path=tmp_path / "voice.db",
        speech_provider=_QueuedSpeechProvider(
            TranscriptionResult(
                transcript="ReAct 是推理与动作交替。",
                provider_request_id="provider-safe-id",
                latency_ms=100,
            )
        ),
        hints=_HintSelector(),
        assessments=_AssessmentPort(),
        clock=_MutableClock(),
        trace_store=trace_store,
        trace_observatory=observatory,
    )
    try:
        started = manager.start(_start_command(), b"private-webm-audio")
        await _wait_for_status(manager, started.voice_run_id, "reviewable")
        waiting_snapshot = observatory.snapshot(started.trace_id)
        serialized = waiting_snapshot.model_dump_json()
        assert waiting_snapshot.summary.status == "waiting_input"
        for forbidden in ("private-webm-audio", "ReAct", "推理与动作", "entry-1"):
            assert forbidden not in serialized

        manager.submit(
            started.voice_run_id,
            VoiceRunSubmitCommand(
                request_id="voice-submit-trace-1",
                edited_text="ReAct 是推理与动作交替。",
            ),
        )
        terminal_snapshot = observatory.snapshot(started.trace_id)
    finally:
        await manager.aclose()
        trace_store.close()

    assert terminal_snapshot.summary.status == "completed"
    assert [span.status for span in terminal_snapshot.spans] == ["completed", "completed"]
    assert "ReAct" not in json.dumps(
        terminal_snapshot.model_dump(mode="json"),
        ensure_ascii=False,
    )

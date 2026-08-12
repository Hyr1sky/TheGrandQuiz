"""Persistent, review-before-submit VoiceRun application workflow.

VoiceRun owns only capture/transcription/review state.  The existing AssessmentManager remains
the sole owner of answer acceptance, grading and learning-memory writes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.recognition_lexicon import TranscriptionHints
from grandquiz.interfaces.api.assessment_runs import (
    AnswerSubmissionRequest,
    AssessmentView,
)
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.kernel.clock import Clock
from grandquiz.kernel.db import connect, migrate, transaction
from grandquiz.kernel.events import EventEmitter, EventSink, EventType
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.speech import (
    MAX_AUDIO_BYTES,
    SpeechRecognitionError,
    SpeechRecognitionProvider,
    TranscriptionRequest,
    TranscriptionResult,
)

VoiceRunStatus = Literal[
    "accepted",
    "transcribing",
    "reviewable",
    "submitted",
    "failed",
    "cancelled",
    "expired",
]

VOICE_RUN_STARTED = "voice.run.started"
VOICE_RUN_ENDED = "voice.run.ended"
VOICE_PROVIDER_ATTEMPT_STARTED = "voice.provider_attempt.started"
VOICE_PROVIDER_ATTEMPT_ENDED = "voice.provider_attempt.ended"
VOICE_REVIEWABLE = "voice.reviewable"
VOICE_CANCELLED = "voice.cancelled"
VOICE_SUBMITTED = "voice.submitted"
VOICE_EXPIRED = "voice.expired"

_SCHEMA_VERSION = "voice-run.v1"
_MIGRATIONS_DIR = Path(__file__).parent / "voice_migrations"
_MIME_TYPE = "audio/webm;codecs=opus"
_MAX_DURATION_MS = 90_000
_PROVIDER_TIMEOUT_SECONDS = 30
_MAX_PROVIDER_ATTEMPTS = 2
_REVIEW_TTL_SECONDS = 30 * 60
_CANCELLATION_TOMBSTONE_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_SWEEP_INTERVAL_SECONDS = 30.0


class VoiceRunCommandConflict(ValueError):
    """A command would violate VoiceRun identity or state-machine rules."""


class VoiceRunErrorView(BaseModel):
    code: str
    stage: Literal["validation", "provider", "runtime", "submit"]
    reason: str
    retryable: bool


class VoiceRunStartCommand(BaseModel):
    request_id: str = Field(min_length=1)
    assessment_session_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    mime_type: Literal["audio/webm;codecs=opus"]
    client_duration_ms: int = Field(ge=1, le=_MAX_DURATION_MS)

    @field_validator("request_id", "assessment_session_id", "question_id")
    @classmethod
    def value_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class VoiceRunSubmitCommand(BaseModel):
    request_id: str = Field(min_length=1)
    edited_text: str = Field(min_length=1)

    @field_validator("request_id", "edited_text")
    @classmethod
    def value_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class VoiceRunView(BaseModel):
    schema_version: Literal["voice-run.v1"] = _SCHEMA_VERSION
    voice_run_id: str
    request_id: str
    assessment_session_id: str
    question_id: str
    item_id: str
    status: VoiceRunStatus
    version: int
    mime_type: str
    byte_count: int
    client_duration_ms: int
    audio_sha256: str
    hint_set_id: str
    hint_count: int
    hints_applied: bool
    provider_attempt_count: int
    active_provider_attempt_id: str | None = None
    reviewable_transcript: str | None = None
    retryable: bool = False
    error: VoiceRunErrorView | None = None
    trace_id: str
    created_at: float
    updated_at: float
    expires_at: float | None = None


class TranscriptionHintSelector(Protocol):
    def select_for_items(self, item_ids: Sequence[str]) -> TranscriptionHints: ...


class AssessmentSubmissionPort(Protocol):
    def get(self, session_id: str) -> AssessmentView | None: ...

    def submit_answer(
        self,
        session_id: str,
        question_id: str,
        command: AnswerSubmissionRequest,
    ) -> AssessmentView | None: ...


class VoiceRunManager:
    """Own durable VoiceRun state while delegating final answers to Assessment."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        speech_provider: SpeechRecognitionProvider,
        hints: TranscriptionHintSelector,
        assessments: AssessmentSubmissionPort,
        clock: Clock,
        trace_store: TraceStore | None = None,
        trace_observatory: TraceObservatory | None = None,
        sweep_interval_seconds: float = _DEFAULT_SWEEP_INTERVAL_SECONDS,
        hints_enabled: bool | None = None,
    ) -> None:
        self._conn = connect(db_path)
        self._conn.row_factory = sqlite3.Row
        migrate(self._conn, _MIGRATIONS_DIR)
        self._speech_provider = speech_provider
        self._hints_applied = (
            bool(getattr(speech_provider, "hints_enabled", False))
            if hints_enabled is None
            else hints_enabled
        )
        self._hints = hints
        self._assessments = assessments
        self._clock = clock
        self._trace_store = trace_store
        self._trace_observatory = trace_observatory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._emitters: dict[str, EventEmitter] = {}
        self._closed = False
        self._recover_interrupted_runs()
        self._expire_orphaned_reviews()
        self._expire_stale_reviews()
        self._sweep_interval_seconds = sweep_interval_seconds
        self._sweep_task = asyncio.create_task(
            self._sweep_expired_state(),
            name="grandquiz-voice-expiry-sweep",
        )

    @property
    def hints_enabled(self) -> bool:
        return self._hints_applied

    def set_hints_enabled(self, enabled: bool) -> None:
        """Change hint policy for future VoiceRuns; an accepted run keeps its frozen snapshot."""
        self._hints_applied = enabled

    def start(self, command: VoiceRunStartCommand, audio_bytes: bytes) -> VoiceRunView:
        audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()
        existing = self._row_for_request(command.request_id)
        if existing is not None:
            if not self._same_start(existing, command, audio_bytes, audio_sha256):
                raise VoiceRunCommandConflict("request_id 已绑定到另一段录音或题目")
            return self._view(existing)

        cancellation = self._conn.execute(
            "SELECT 1 FROM voice_request_cancellations WHERE request_id=?",
            (command.request_id,),
        ).fetchone()
        if cancellation is not None:
            raise VoiceRunCommandConflict("语音上传已经取消")

        if not audio_bytes:
            raise ValueError("录音不能为空")
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise ValueError(f"录音不能超过 {MAX_AUDIO_BYTES} bytes")
        assessment = self._current_assessment(command)
        question = assessment.question
        if question is None:
            raise VoiceRunCommandConflict("当前考核没有可回答的问题")
        selected_hints = self._hints.select_for_items([question.item_id])
        now = self._clock.now()
        voice_run_id = uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        run_span_id = f"{trace_id}:s0"
        hints_payload = json.dumps(
            selected_hints.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO voice_runs ("
                "voice_run_id, schema_version, request_id, assessment_session_id, "
                "question_id, item_id, status, version, mime_type, byte_count, "
                "client_duration_ms, audio_sha256, hint_set_id, hint_count, "
                "hints_applied, hints_payload, "
                "provider_attempt_count, retryable, trace_id, run_span_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, 'accepted', 1, ?, ?, ?, ?, ?, ?, ?, ?, "
                "0, 0, ?, ?, ?, ?)",
                (
                    voice_run_id,
                    _SCHEMA_VERSION,
                    command.request_id,
                    command.assessment_session_id,
                    command.question_id,
                    question.item_id,
                    command.mime_type,
                    len(audio_bytes),
                    command.client_duration_ms,
                    audio_sha256,
                    selected_hints.hint_set_id,
                    len(selected_hints.entries),
                    int(self._hints_applied),
                    hints_payload,
                    trace_id,
                    run_span_id,
                    now,
                    now,
                ),
            )
        self._emit_run_started(voice_run_id)
        self._launch_attempt(voice_run_id, audio_bytes, retry_request_id=None)
        view = self.get(voice_run_id)
        if view is None:  # pragma: no cover - insert/read invariant
            raise AssertionError("VoiceRun insert was not readable")
        return view

    def get(self, voice_run_id: str) -> VoiceRunView | None:
        row = self._row(voice_run_id)
        if row is None:
            return None
        expires_at = row["expires_at"]
        if (
            row["status"] == "reviewable"
            and expires_at is not None
            and self._clock.now() >= float(expires_at)
        ):
            self._expire(voice_run_id)
            row = self._row(voice_run_id)
            if row is None:  # pragma: no cover - update/read invariant
                return None
        return self._view(row)

    def cancel(self, voice_run_id: str) -> VoiceRunView | None:
        row = self._row(voice_run_id)
        if row is None:
            return None
        if row["status"] in {"submitted", "cancelled", "expired"}:
            return self._view(row)
        now = self._clock.now()
        active_attempt_ids = self._active_attempt_ids(voice_run_id)
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE voice_runs SET status='cancelled', version=version+1, "
                "reviewable_transcript=NULL, retryable=0, error_code=NULL, error_stage=NULL, "
                "error_reason=NULL, expires_at=NULL, updated_at=? WHERE voice_run_id=?",
                (now, voice_run_id),
            )
            self._conn.execute(
                "UPDATE voice_provider_attempts SET status='cancelled', "
                "error_code='cancelled', error_reason='用户取消语音识别', ended_at=? "
                "WHERE voice_run_id=? AND status='running'",
                (now, voice_run_id),
            )
        for attempt_id in active_attempt_ids:
            self._emit_attempt_ended(
                voice_run_id,
                attempt_id,
                status="cancelled",
                error_code="cancelled",
            )
        task = self._tasks.get(voice_run_id)
        if task is not None and not task.done():
            task.cancel()
        self._emit(voice_run_id, VOICE_CANCELLED, {"status": "cancelled"})
        if row["status"] != "failed":
            self._emit_run_ended(voice_run_id, "cancelled")
        current = self._row(voice_run_id)
        return None if current is None else self._view(current)

    def cancel_request(self, request_id: str) -> VoiceRunView | None:
        """Cancel an accepted run or reserve cancellation while its upload is in flight."""

        normalized = request_id.strip()
        if not normalized:
            raise ValueError("request_id 不能为空")
        existing = self._row_for_request(normalized)
        if existing is not None:
            return self.cancel(str(existing["voice_run_id"]))
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR IGNORE INTO voice_request_cancellations (request_id, created_at) "
                "VALUES (?, ?)",
                (normalized, self._clock.now()),
            )
        return None

    def retry(
        self,
        voice_run_id: str,
        *,
        request_id: str,
        audio_bytes: bytes,
    ) -> VoiceRunView:
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("request_id 不能为空")
        row = self._required_row(voice_run_id)
        if hashlib.sha256(audio_bytes).hexdigest() != row["audio_sha256"]:
            raise VoiceRunCommandConflict("重试必须使用同一段录音")
        if len(audio_bytes) != int(row["byte_count"]):
            raise VoiceRunCommandConflict("重试录音长度与原请求不一致")
        if row["retry_request_id"] == normalized_request_id:
            return self._view(row)
        if row["status"] != "failed" or not bool(row["retryable"]):
            raise VoiceRunCommandConflict("当前语音任务不允许重试")
        if int(row["provider_attempt_count"]) >= _MAX_PROVIDER_ATTEMPTS:
            raise VoiceRunCommandConflict("语音识别尝试次数已用尽")
        self._launch_attempt(
            voice_run_id,
            audio_bytes,
            retry_request_id=normalized_request_id,
        )
        return self._view(self._required_row(voice_run_id))

    def submit(self, voice_run_id: str, command: VoiceRunSubmitCommand) -> VoiceRunView:
        row = self._required_row(voice_run_id)
        answer_sha256 = hashlib.sha256(command.edited_text.encode()).hexdigest()
        if row["status"] == "submitted":
            if (
                row["submit_request_id"] == command.request_id
                and row["submitted_answer_sha256"] == answer_sha256
            ):
                return self._view(row)
            raise VoiceRunCommandConflict("当前语音答案已经提交")
        if row["status"] != "reviewable":
            raise VoiceRunCommandConflict("当前语音任务尚不可提交")
        assessment = self._assessments.get(str(row["assessment_session_id"]))
        if (
            assessment is None
            or assessment.question is None
            or assessment.question.question_id != row["question_id"]
            or assessment.question.item_id != row["item_id"]
        ):
            raise VoiceRunCommandConflict("考核题目已经变化，旧语音草稿不能提交")
        accepted = self._assessments.submit_answer(
            str(row["assessment_session_id"]),
            str(row["question_id"]),
            AnswerSubmissionRequest(
                request_id=command.request_id,
                answer=command.edited_text,
                input_modality="voice",
            ),
        )
        if accepted is None:
            raise VoiceRunCommandConflict("考核题目不存在")
        now = self._clock.now()
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE voice_runs SET status='submitted', version=version+1, "
                "reviewable_transcript=NULL, hints_payload='{}', retryable=0, expires_at=NULL, "
                "submit_request_id=?, submitted_answer_sha256=?, updated_at=? "
                "WHERE voice_run_id=? AND status='reviewable'",
                (command.request_id, answer_sha256, now, voice_run_id),
            )
        self._emit(voice_run_id, VOICE_SUBMITTED, {"status": "submitted"})
        self._emit_run_ended(voice_run_id, "submitted")
        return self._view(self._required_row(voice_run_id))

    async def aclose(self) -> None:
        if self._closed:
            return
        self._sweep_task.cancel()
        await asyncio.gather(self._sweep_task, return_exceptions=True)
        self._interrupt_active_runs()
        active = [task for task in self._tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.sleep(0)
            for task in active:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
        self._conn.close()
        self._closed = True

    def _current_assessment(self, command: VoiceRunStartCommand) -> AssessmentView:
        assessment = self._assessments.get(command.assessment_session_id)
        if (
            assessment is None
            or assessment.status != "awaiting_answer"
            or assessment.question is None
            or assessment.question.question_id != command.question_id
            or assessment.question.question_type != "开放"
            or bool(assessment.question.options)
        ):
            raise VoiceRunCommandConflict("语音回答仅适用于当前开放题")
        return assessment

    def _launch_attempt(
        self,
        voice_run_id: str,
        audio_bytes: bytes,
        *,
        retry_request_id: str | None,
    ) -> None:
        row = self._required_row(voice_run_id)
        attempt_number = int(row["provider_attempt_count"]) + 1
        if attempt_number > _MAX_PROVIDER_ATTEMPTS:
            raise VoiceRunCommandConflict("语音识别尝试次数已用尽")
        attempt_id = uuid.uuid4().hex
        now = self._clock.now()
        is_retry = retry_request_id is not None
        run_span_id = (
            f"{row['trace_id']}:s{attempt_number - 1}" if is_retry else str(row["run_span_id"])
        )
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO voice_provider_attempts "
                "(provider_attempt_id, voice_run_id, attempt_number, status, started_at) "
                "VALUES (?, ?, ?, 'running', ?)",
                (attempt_id, voice_run_id, attempt_number, now),
            )
            self._conn.execute(
                "UPDATE voice_runs SET status='transcribing', version=version+1, "
                "provider_attempt_count=?, active_provider_attempt_id=?, retry_request_id=?, "
                "run_span_id=?, retryable=0, error_code=NULL, error_stage=NULL, "
                "error_reason=NULL, updated_at=? "
                "WHERE voice_run_id=?",
                (
                    attempt_number,
                    attempt_id,
                    retry_request_id,
                    run_span_id,
                    now,
                    voice_run_id,
                ),
            )
        if is_retry:
            self._emit_run_started(voice_run_id, resume=True)
        task = asyncio.create_task(
            self._run_attempt(voice_run_id, attempt_id, audio_bytes),
            name=f"grandquiz-voice:{voice_run_id}:{attempt_number}",
        )
        self._tasks[voice_run_id] = task

    async def _run_attempt(
        self,
        voice_run_id: str,
        attempt_id: str,
        audio_bytes: bytes,
    ) -> None:
        row = self._required_row(voice_run_id)
        hints = TranscriptionHints.model_validate_json(str(row["hints_payload"]))
        self._emit(
            voice_run_id,
            VOICE_PROVIDER_ATTEMPT_STARTED,
            {
                "provider_attempt_id": attempt_id,
                "attempt": int(row["provider_attempt_count"]),
                "mime_type": row["mime_type"],
                "byte_count": int(row["byte_count"]),
                "hint_set_id": row["hint_set_id"],
                "hint_count": int(row["hint_count"]),
                "hints_applied": bool(row["hints_applied"]),
            },
            child_span=True,
        )
        try:
            result = await self._speech_provider.transcribe(
                TranscriptionRequest(
                    audio_bytes=audio_bytes,
                    mime_type=cast("Literal['audio/webm;codecs=opus']", row["mime_type"]),
                    hints=hints,
                    material_hints_enabled=bool(row["hints_applied"]),
                    timeout_seconds=_PROVIDER_TIMEOUT_SECONDS,
                )
            )
        except asyncio.CancelledError:
            raise
        except SpeechRecognitionError as exc:
            self._complete_error(voice_run_id, attempt_id, exc)
        except Exception:
            self._complete_error(
                voice_run_id,
                attempt_id,
                SpeechRecognitionError(
                    "provider_unavailable",
                    "语音服务暂时不可用",
                    retryable=True,
                ),
            )
        else:
            self._complete_success(voice_run_id, attempt_id, result)

    def _complete_success(
        self,
        voice_run_id: str,
        attempt_id: str,
        result: TranscriptionResult,
    ) -> None:
        now = self._clock.now()
        with transaction(self._conn):
            attempt_changed = self._conn.execute(
                "UPDATE voice_provider_attempts SET status='completed', provider_request_id=?, "
                "latency_ms=?, ended_at=? WHERE provider_attempt_id=? AND status='running'",
                (result.provider_request_id, result.latency_ms, now, attempt_id),
            ).rowcount
            changed = self._conn.execute(
                "UPDATE voice_runs SET status='reviewable', version=version+1, "
                "reviewable_transcript=?, retryable=0, error_code=NULL, error_stage=NULL, "
                "error_reason=NULL, expires_at=?, updated_at=? "
                "WHERE voice_run_id=? AND status='transcribing' "
                "AND active_provider_attempt_id=?",
                (
                    result.transcript,
                    now + _REVIEW_TTL_SECONDS,
                    now,
                    voice_run_id,
                    attempt_id,
                ),
            ).rowcount
            if not attempt_changed:
                self._conn.execute(
                    "UPDATE voice_provider_attempts SET provider_request_id=?, latency_ms=? "
                    "WHERE provider_attempt_id=? AND status='cancelled'",
                    (result.provider_request_id, result.latency_ms, attempt_id),
                )
        if attempt_changed:
            self._emit_attempt_ended(
                voice_run_id,
                attempt_id,
                status="completed",
                provider_request_id=result.provider_request_id,
                latency_ms=result.latency_ms,
            )
        if changed:
            self._emit(
                voice_run_id,
                VOICE_REVIEWABLE,
                {"status": "reviewable", "expires_in_seconds": _REVIEW_TTL_SECONDS},
            )

    def _complete_error(
        self,
        voice_run_id: str,
        attempt_id: str,
        error: SpeechRecognitionError,
    ) -> None:
        row = self._required_row(voice_run_id)
        retryable = error.retryable and int(row["provider_attempt_count"]) < _MAX_PROVIDER_ATTEMPTS
        now = self._clock.now()
        with transaction(self._conn):
            attempt_changed = self._conn.execute(
                "UPDATE voice_provider_attempts SET status='failed', provider_request_id=?, "
                "error_code=?, error_reason=?, ended_at=? "
                "WHERE provider_attempt_id=? AND status='running'",
                (error.provider_request_id, error.code, error.reason, now, attempt_id),
            ).rowcount
            changed = self._conn.execute(
                "UPDATE voice_runs SET status='failed', version=version+1, retryable=?, "
                "error_code=?, error_stage='provider', error_reason=?, updated_at=? "
                "WHERE voice_run_id=? AND status='transcribing' "
                "AND active_provider_attempt_id=?",
                (int(retryable), error.code, error.reason, now, voice_run_id, attempt_id),
            ).rowcount
            if not attempt_changed:
                self._conn.execute(
                    "UPDATE voice_provider_attempts SET provider_request_id=? "
                    "WHERE provider_attempt_id=? AND status='cancelled'",
                    (error.provider_request_id, attempt_id),
                )
        if attempt_changed:
            self._emit_attempt_ended(
                voice_run_id,
                attempt_id,
                status="failed",
                error_code=error.code,
                retryable=retryable,
                provider_request_id=error.provider_request_id,
            )
        if changed:
            self._emit(
                voice_run_id,
                EventType.ERROR,
                {
                    "status": "failed",
                    "code": error.code,
                    "stage": "provider",
                    "retryable": retryable,
                },
            )
            self._emit_run_ended(voice_run_id, "failed")

    def _expire(self, voice_run_id: str) -> None:
        now = self._clock.now()
        with transaction(self._conn):
            changed = self._conn.execute(
                "UPDATE voice_runs SET status='expired', version=version+1, "
                "reviewable_transcript=NULL, hints_payload='{}', retryable=0, updated_at=? "
                "WHERE voice_run_id=? AND status='reviewable'",
                (now, voice_run_id),
            ).rowcount
        if changed:
            self._emit(voice_run_id, VOICE_EXPIRED, {"status": "expired"})
            self._emit_run_ended(voice_run_id, "expired")

    def _recover_interrupted_runs(self) -> None:
        now = self._clock.now()
        recovered: list[tuple[str, bool, list[str]]] = []
        with transaction(self._conn):
            interrupted = self._conn.execute(
                "SELECT voice_run_id, provider_attempt_count FROM voice_runs "
                "WHERE status IN ('accepted', 'transcribing')"
            ).fetchall()
            for row in interrupted:
                attempt_ids = self._active_attempt_ids(str(row["voice_run_id"]))
                retryable = int(row["provider_attempt_count"]) < _MAX_PROVIDER_ATTEMPTS
                self._conn.execute(
                    "UPDATE voice_runs SET status='failed', version=version+1, retryable=?, "
                    "error_code='service_restarted', error_stage='runtime', "
                    "error_reason='服务重启中断了语音识别，请显式重试', updated_at=? "
                    "WHERE voice_run_id=?",
                    (int(retryable), now, row["voice_run_id"]),
                )
                self._conn.execute(
                    "UPDATE voice_provider_attempts SET status='failed', "
                    "error_code='service_restarted', error_reason='服务重启中断', ended_at=? "
                    "WHERE voice_run_id=? AND status='running'",
                    (now, row["voice_run_id"]),
                )
                recovered.append((str(row["voice_run_id"]), retryable, attempt_ids))
        for voice_run_id, retryable, attempt_ids in recovered:
            self._resume_emitter(voice_run_id)
            for attempt_id in attempt_ids:
                self._emit_attempt_ended(
                    voice_run_id,
                    attempt_id,
                    status="failed",
                    error_code="service_restarted",
                    retryable=retryable,
                )
            self._emit(
                voice_run_id,
                EventType.ERROR,
                {
                    "code": "service_restarted",
                    "stage": "runtime",
                    "retryable": retryable,
                    "status": "failed",
                },
            )
            self._emit_run_ended(voice_run_id, "failed")

    def _expire_orphaned_reviews(self) -> None:
        rows = self._conn.execute(
            "SELECT voice_run_id, assessment_session_id, question_id, item_id "
            "FROM voice_runs WHERE status='reviewable'"
        ).fetchall()
        for row in rows:
            assessment = self._assessments.get(str(row["assessment_session_id"]))
            if (
                assessment is None
                or assessment.question is None
                or assessment.question.question_id != row["question_id"]
                or assessment.question.item_id != row["item_id"]
            ):
                self._expire(str(row["voice_run_id"]))

    def _expire_stale_reviews(self) -> None:
        now = self._clock.now()
        stale_ids = [
            str(row["voice_run_id"])
            for row in self._conn.execute(
                "SELECT voice_run_id FROM voice_runs "
                "WHERE status='reviewable' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            ).fetchall()
        ]
        for voice_run_id in stale_ids:
            self._expire(voice_run_id)

    async def _sweep_expired_state(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._sweep_interval_seconds)
                self._expire_stale_reviews()
                cutoff = self._clock.now() - _CANCELLATION_TOMBSTONE_TTL_SECONDS
                with transaction(self._conn):
                    self._conn.execute(
                        "DELETE FROM voice_request_cancellations WHERE created_at <= ?",
                        (cutoff,),
                    )
        except asyncio.CancelledError:
            return

    def _interrupt_active_runs(self) -> None:
        active = self._conn.execute(
            "SELECT voice_run_id FROM voice_runs WHERE status IN ('accepted', 'transcribing')"
        ).fetchall()
        for row in active:
            voice_run_id = str(row["voice_run_id"])
            attempt_ids = self._active_attempt_ids(voice_run_id)
            now = self._clock.now()
            retryable = (
                int(self._required_row(voice_run_id)["provider_attempt_count"])
                < _MAX_PROVIDER_ATTEMPTS
            )
            with transaction(self._conn):
                self._conn.execute(
                    "UPDATE voice_runs SET status='failed', version=version+1, retryable=?, "
                    "error_code='service_stopped', error_stage='runtime', "
                    "error_reason='服务停止中断了语音识别，请显式重试', updated_at=? "
                    "WHERE voice_run_id=? AND status IN ('accepted', 'transcribing')",
                    (int(retryable), now, voice_run_id),
                )
                self._conn.execute(
                    "UPDATE voice_provider_attempts SET status='failed', "
                    "error_code='service_stopped', error_reason='服务停止中断', ended_at=? "
                    "WHERE voice_run_id=? AND status='running'",
                    (now, voice_run_id),
                )
            for attempt_id in attempt_ids:
                self._emit_attempt_ended(
                    voice_run_id,
                    attempt_id,
                    status="failed",
                    error_code="service_stopped",
                    retryable=retryable,
                )
            self._emit(
                voice_run_id,
                EventType.ERROR,
                {
                    "status": "failed",
                    "code": "service_stopped",
                    "stage": "runtime",
                    "retryable": retryable,
                },
            )
            self._emit_run_ended(voice_run_id, "failed")

    def _active_attempt_ids(self, voice_run_id: str) -> list[str]:
        return [
            str(row["provider_attempt_id"])
            for row in self._conn.execute(
                "SELECT provider_attempt_id FROM voice_provider_attempts "
                "WHERE voice_run_id=? AND status='running' ORDER BY attempt_number",
                (voice_run_id,),
            ).fetchall()
        ]

    def _emit_attempt_ended(
        self,
        voice_run_id: str,
        attempt_id: str,
        *,
        status: str,
        error_code: str | None = None,
        retryable: bool | None = None,
        provider_request_id: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "provider_attempt_id": attempt_id,
            "status": status,
        }
        if error_code is not None:
            payload["error_code"] = error_code
        if retryable is not None:
            payload["retryable"] = retryable
        if provider_request_id is not None:
            payload["provider_request_id"] = provider_request_id
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        self._emit(
            voice_run_id,
            VOICE_PROVIDER_ATTEMPT_ENDED,
            payload,
            child_span=True,
        )

    def _resume_emitter(self, voice_run_id: str) -> None:
        if self._trace_store is None:
            return
        row = self._required_row(voice_run_id)
        trace_id = str(row["trace_id"])
        events = self._trace_store.events(trace_id)
        sink = EventSink()
        sink.register_durable(self._trace_store)
        if self._trace_observatory is not None:
            self._trace_observatory.register_trace(trace_id)
            sink.register(self._trace_observatory)
        self._emitters[voice_run_id] = EventEmitter(
            sink,
            self._clock,
            trace_id=trace_id,
            initial_seq=len(events),
        )

    def _emit_run_started(self, voice_run_id: str, *, resume: bool = False) -> None:
        row = self._required_row(voice_run_id)
        if self._trace_store is None:
            return
        trace_id = str(row["trace_id"])
        if self._trace_observatory is not None:
            self._trace_observatory.register_trace(trace_id)
        if resume:
            self._resume_emitter(voice_run_id)
            emitter = self._emitters[voice_run_id]
        else:
            sink = EventSink()
            sink.register_durable(self._trace_store)
            if self._trace_observatory is not None:
                sink.register(self._trace_observatory)
            emitter = EventEmitter(sink, self._clock, trace_id=trace_id)
            self._emitters[voice_run_id] = emitter
        emitter.emit(
            VOICE_RUN_STARTED,
            span_id=str(row["run_span_id"]),
            payload={
                "status": "running",
                "voice_run_id": voice_run_id,
                "assessment_session_id": row["assessment_session_id"],
                "question_id": row["question_id"],
                "item_id": row["item_id"],
                "mime_type": row["mime_type"],
                "byte_count": int(row["byte_count"]),
                "client_duration_ms": int(row["client_duration_ms"]),
                "hint_set_id": row["hint_set_id"],
                "hint_count": int(row["hint_count"]),
                "attempt": int(row["provider_attempt_count"]),
            },
        )

    def _emit(
        self,
        voice_run_id: str,
        event_type: str,
        payload: dict[str, object],
        *,
        child_span: bool = False,
    ) -> None:
        emitter = self._emitters.get(voice_run_id)
        if emitter is None:
            self._resume_emitter(voice_run_id)
            emitter = self._emitters.get(voice_run_id)
        if emitter is None:
            return
        row = self._required_row(voice_run_id)
        if child_span:
            attempt_id = str(payload.get("provider_attempt_id", "attempt"))
            span_id = f"{row['trace_id']}:attempt:{attempt_id}"
            emitter.emit(
                event_type,
                span_id=span_id,
                parent_span_id=str(row["run_span_id"]),
                payload=payload,
            )
            return
        emitter.emit(event_type, span_id=str(row["run_span_id"]), payload=payload)

    def _emit_run_ended(self, voice_run_id: str, status: str) -> None:
        self._emit(voice_run_id, VOICE_RUN_ENDED, {"status": status})

    def _same_start(
        self,
        row: sqlite3.Row,
        command: VoiceRunStartCommand,
        audio_bytes: bytes,
        audio_sha256: str,
    ) -> bool:
        return (
            row["assessment_session_id"] == command.assessment_session_id
            and row["question_id"] == command.question_id
            and row["mime_type"] == command.mime_type
            and int(row["client_duration_ms"]) == command.client_duration_ms
            and int(row["byte_count"]) == len(audio_bytes)
            and row["audio_sha256"] == audio_sha256
        )

    def _row_for_request(self, request_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM voice_runs WHERE request_id=?",
            (request_id,),
        ).fetchone()

    def _row(self, voice_run_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM voice_runs WHERE voice_run_id=?",
            (voice_run_id,),
        ).fetchone()

    def _required_row(self, voice_run_id: str) -> sqlite3.Row:
        row = self._row(voice_run_id)
        if row is None:
            raise KeyError(f"VoiceRun 不存在：{voice_run_id}")
        return row

    def _view(self, row: sqlite3.Row) -> VoiceRunView:
        error = None
        if row["error_code"] is not None:
            error = VoiceRunErrorView(
                code=str(row["error_code"]),
                stage=cast(
                    "Literal['validation', 'provider', 'runtime', 'submit']",
                    row["error_stage"],
                ),
                reason=str(row["error_reason"]),
                retryable=bool(row["retryable"]),
            )
        return VoiceRunView(
            voice_run_id=str(row["voice_run_id"]),
            request_id=str(row["request_id"]),
            assessment_session_id=str(row["assessment_session_id"]),
            question_id=str(row["question_id"]),
            item_id=str(row["item_id"]),
            status=cast("VoiceRunStatus", row["status"]),
            version=int(row["version"]),
            mime_type=str(row["mime_type"]),
            byte_count=int(row["byte_count"]),
            client_duration_ms=int(row["client_duration_ms"]),
            audio_sha256=str(row["audio_sha256"]),
            hint_set_id=str(row["hint_set_id"]),
            hint_count=int(row["hint_count"]),
            hints_applied=bool(row["hints_applied"]),
            provider_attempt_count=int(row["provider_attempt_count"]),
            active_provider_attempt_id=(
                None
                if row["active_provider_attempt_id"] is None
                else str(row["active_provider_attempt_id"])
            ),
            reviewable_transcript=(
                None if row["reviewable_transcript"] is None else str(row["reviewable_transcript"])
            ),
            retryable=bool(row["retryable"]),
            error=error,
            trace_id=str(row["trace_id"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=None if row["expires_at"] is None else float(row["expires_at"]),
        )

"""Web Acquisition 的异步编排与安全浏览器投影。"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import PurePath
from typing import Literal, cast
from urllib.parse import quote, urlparse

from pydantic import BaseModel, Field

from grandquiz.domain.learning.acquisition import (
    AcquisitionFailureCode,
    AcquisitionFailureStage,
    AcquisitionLedger,
    AcquisitionRun,
    AcquisitionTransitionError,
)
from grandquiz.domain.learning.approval import (
    emit_approval_decided,
    emit_approval_requested,
)
from grandquiz.domain.learning.classification import ClassificationProposal
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest import (
    IngestFailure,
    IngestResult,
    abort_ingest,
    emit_prepared_ingest_committed,
    persist_prepared_ingest,
    prepare_ingest,
)
from grandquiz.domain.learning.ingest.fetch import ALLOW_ANY_DOMAIN, FetchSource
from grandquiz.domain.learning.ingest.pipeline import public_ingest_failure_reason
from grandquiz.domain.learning.ingest.web_fetch import create_http_source
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.learning_outbox import publish_pending_learning_facts
from grandquiz.kernel.clock import Clock, SystemClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider

_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_MAX_FETCH_BYTES = 4 * 1024 * 1024
_TOKEN_TTL_SECONDS = 24 * 60 * 60
_ALLOWED_UPLOAD_SUFFIXES = {".md", ".markdown", ".txt"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
AcquisitionUiEventType = Literal[
    "acquisition.queued",
    "acquisition.started",
    "acquisition.fetched",
    "acquisition.candidates_ready",
    "acquisition.needs_input",
    "acquisition.succeeded",
    "acquisition.failed",
    "acquisition.cancelled",
]


def _empty_candidates() -> list[AcquisitionCandidateView]:
    return []


class AcquisitionCandidateView(BaseModel):
    item_id: str
    concept: str
    summary: str
    confidence: float
    evidence: list[str]
    classification: ClassificationProposal


class AcquisitionView(BaseModel):
    run_id: str
    trace_id: str
    kind: Literal["upload", "url"]
    display_name: str
    status: Literal["queued", "running", "needs_input", "succeeded", "failed", "cancelled"]
    candidates: list[AcquisitionCandidateView] = Field(default_factory=_empty_candidates)
    resource_id: str | None = None
    error_code: AcquisitionFailureCode | None = None
    error_stage: AcquisitionFailureStage | None = None
    error_message: str | None = None
    created_at: float
    updated_at: float


class AcquisitionCreated(AcquisitionView):
    resume_token: str
    token_expires_at: float


class AcquisitionUiEvent(BaseModel):
    sequence: int
    type: AcquisitionUiEventType
    run_id: str
    trace_id: str
    data: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class AcquisitionInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AcquisitionCommitError(RuntimeError):
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        super().__init__("知识快照提交失败")


def _public_failure_data(
    run: AcquisitionRun,
) -> dict[str, str]:
    code = run.error_code or "processing_failed"
    if code == "processing_failed":
        stage: AcquisitionFailureStage = "processing"
        reason = "材料处理失败，请通过 trace_id 查看详情"
    elif code == "interrupted":
        stage = "runtime"
        reason = "服务在材料处理期间重启，请重试"
    else:
        stage = run.error_stage or "reader"
        reason = public_ingest_failure_reason(code)
    return {"code": code, "stage": stage, "reason": reason}


class AcquisitionManager:
    """把持久领域状态机接到 Provider、事件脊柱和后台 task。"""

    def __init__(
        self,
        *,
        persistence: LearningPersistence,
        provider: Provider,
        trace_store: TraceStore,
        clock: Clock | None = None,
        http_source: FetchSource | None = None,
    ) -> None:
        self._persistence = persistence
        self._ledger: AcquisitionLedger = persistence.acquisitions
        self._provider = provider
        self._trace_store = trace_store
        self._clock = clock or SystemClock()
        self._http_source = http_source or create_http_source()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._changed: dict[str, asyncio.Event] = {}
        interrupted = self._ledger.in_flight()
        self._ledger.fail_interrupted_runs(now=self._clock.now())
        for run in interrupted:
            failed_run = self._ledger.require(run.run_id)
            failure = _public_failure_data(failed_run)
            emitter = self._resume_emitter(run.trace_id)
            open_span = self._open_ingest_span(run.trace_id)
            if open_span is not None:
                abort_ingest(open_span, reason="interrupted", emitter=emitter)
            emitter.emit(
                EventType.ERROR,
                payload={
                    "classification": failure["code"],
                    **failure,
                },
            )
            emitter.emit(
                "acquisition.failed",
                payload={
                    "run_id": run.run_id,
                    **failure,
                    "status": "failed",
                },
            )

    def start_upload(self, *, filename: str, content: str) -> AcquisitionCreated:
        suffix = PurePath(filename).suffix.lower()
        if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
            raise AcquisitionInputError("unsupported_upload_type", "仅支持 Markdown 或纯文本文件")
        encoded = content.encode("utf-8")
        if not content.strip():
            raise AcquisitionInputError("empty_upload", "上传文件没有可读取的正文")
        if len(encoded) > _MAX_UPLOAD_BYTES:
            raise AcquisitionInputError("upload_too_large", "上传文件不能超过 2 MiB")
        safe_name = PurePath(filename).name
        digest = hashlib.sha256(encoded).hexdigest()[:16]
        locator = f"file://local/upload/{digest}/{quote(safe_name)}"
        return self._start(
            kind="upload",
            locator=locator,
            display_name=safe_name,
            request_payload={"content": content},
        )

    def start_url(self, *, url: str) -> AcquisitionCreated:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise AcquisitionInputError("invalid_url", "请输入带主机名的 http(s) URL")
        display_name = f"{parsed.hostname}{parsed.path}".rstrip("/")
        return self._start(
            kind="url",
            locator=url,
            display_name=display_name,
            request_payload={},
        )

    def get(self, run_id: str) -> AcquisitionView | None:
        run = self._ledger.get(run_id)
        return None if run is None else self._view(run)

    def recent(self, *, limit: int = 20) -> list[AcquisitionView]:
        return [self._view(run) for run in self._ledger.recent(limit=limit)]

    async def approve(
        self,
        run_id: str,
        *,
        resume_token: str,
        approved_item_ids: list[str],
    ) -> AcquisitionView:
        run = self._ledger.require(run_id)
        prepared = run.prepared
        if prepared is None:
            raise AcquisitionTransitionError("当前运行没有可审批候选")
        token_hash = self._token_hash(resume_token)
        emitter = self._resume_emitter(run.trace_id)
        selected_ids = set(approved_item_ids)
        candidate_ids = {item.item_id for item in prepared.candidates}
        if not selected_ids <= candidate_ids:
            raise AcquisitionTransitionError("审批包含未知知识点")
        approved = [item for item in prepared.candidates if item.item_id in selected_ids]
        try:
            with self._persistence.transaction_owner.transaction():
                self._ledger.consume_approval_token(
                    run_id,
                    token_hash=token_hash,
                    now=self._clock.now(),
                )
                result = persist_prepared_ingest(
                    prepared,
                    approved=approved,
                    store=self._persistence.store,
                    classifications=self._persistence.classifications,
                    trace_id=run.trace_id,
                )
                updated = self._ledger.mark_succeeded(
                    run_id,
                    resource_id=result.resource_id,
                    now=self._clock.now(),
                )
        except AcquisitionTransitionError:
            raise
        except Exception as exc:
            emitter.emit(
                EventType.ERROR,
                parent_span_id=prepared.ingest_span_id,
                payload={
                    "classification": "acquisition_commit_failed",
                    "error_type": type(exc).__name__,
                },
            )
            self._notify(run_id)
            raise AcquisitionCommitError(run.trace_id) from exc
        emit_approval_decided(
            prepared.candidates,
            approved,
            outcome="approved" if approved else "rejected_all",
            decision_source="human_web",
            emitter=emitter,
            parent_span_id=prepared.ingest_span_id,
        )
        emit_prepared_ingest_committed(prepared, result, emitter=emitter)
        emitter.emit(
            "acquisition.succeeded",
            payload={
                "run_id": run_id,
                "resource_id": result.resource_id,
                "status": "completed",
            },
        )
        publish_pending_learning_facts(
            self._persistence.learning_facts,
            self._trace_store,
            clock=self._clock,
        )
        self._notify(run_id)
        return self._view(updated)

    async def cancel(self, run_id: str, *, resume_token: str) -> AcquisitionView:
        run = self._ledger.require(run_id)
        self._verify_token(run, resume_token)
        if run.status in _TERMINAL:
            return self._view(run)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        updated = self._ledger.mark_cancelled(run_id, now=self._clock.now())
        emitter = self._resume_emitter(run.trace_id)
        open_span = self._open_ingest_span(run.trace_id)
        if open_span is not None:
            abort_ingest(open_span, reason="cancelled", emitter=emitter)
        emitter.emit(
            "acquisition.cancelled",
            payload={"run_id": run_id, "status": "cancelled"},
        )
        self._notify(run_id)
        return self._view(updated)

    async def iter_events(
        self, run_id: str, *, after: int = 0
    ) -> AsyncIterator[AcquisitionUiEvent]:
        cursor = after
        changed = self._changed.setdefault(run_id, asyncio.Event())
        while True:
            run = self._ledger.require(run_id)
            projected = self._project_events(run, self._trace_store.events(run.trace_id))
            for event in projected:
                if event.sequence > cursor:
                    cursor = event.sequence
                    yield event
            if run.status in _TERMINAL or run.status == "needs_input":
                return
            changed.clear()
            try:
                await asyncio.wait_for(changed.wait(), timeout=1.0)
            except TimeoutError:
                continue

    async def aclose(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _start(
        self,
        *,
        kind: Literal["upload", "url"],
        locator: str,
        display_name: str,
        request_payload: Mapping[str, str],
    ) -> AcquisitionCreated:
        now = self._clock.now()
        run_id = uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        run = self._ledger.create(
            run_id=run_id,
            trace_id=trace_id,
            kind=kind,
            locator=locator,
            display_name=display_name,
            request_payload=request_payload,
            token_hash=self._token_hash(token),
            token_expires_at=now + _TOKEN_TTL_SECONDS,
            now=now,
        )
        emitter = self._new_emitter(trace_id)
        emitter.emit(
            "acquisition.queued",
            payload={"run_id": run_id, "kind": kind},
        )
        self._changed[run_id] = asyncio.Event()
        self._tasks[run_id] = asyncio.create_task(
            self._execute(run_id, emitter),
            name=f"grandquiz-acquisition:{run_id}",
        )
        return AcquisitionCreated(
            **self._view(run).model_dump(),
            resume_token=token,
            token_expires_at=run.token_expires_at,
        )

    async def _execute(self, run_id: str, emitter: EventEmitter) -> None:
        run = self._ledger.mark_running(run_id, now=self._clock.now())
        emitter.emit("acquisition.started", payload={"run_id": run_id})
        self._notify(run_id)
        source: FetchSource
        if run.kind == "upload":
            content = run.request_payload["content"]

            def uploaded_content(_url: str) -> str:
                return content

            source = uploaded_content
        else:
            source = self._http_source
        try:
            result = await prepare_ingest(
                run.locator,
                source=source,
                provider=self._provider,
                store=self._persistence.store,
                emitter=emitter,
                max_bytes=_MAX_FETCH_BYTES,
                allowed_domains=ALLOW_ANY_DOMAIN,
                persist_failed_resource=False,
            )
            if isinstance(result, IngestResult):
                failure = result.failure or IngestFailure(
                    code="ingest_failed",
                    stage="reader",
                    reason="材料读取或深读失败，请检查内容后重试",
                )
                failed_run = self._ledger.mark_failed(
                    run_id,
                    code=failure.code,
                    stage=failure.stage,
                    message=failure.reason,
                    now=self._clock.now(),
                )
                public_failure = _public_failure_data(failed_run)
                emitter.emit(
                    EventType.ERROR,
                    payload={
                        "classification": public_failure["code"],
                        **public_failure,
                    },
                )
                emitter.emit(
                    "acquisition.failed",
                    payload={
                        "run_id": run_id,
                        **public_failure,
                        "status": "failed",
                    },
                )
                return
            self._ledger.mark_needs_input(
                run_id,
                prepared=result,
                now=self._clock.now(),
            )
            emit_approval_requested(
                result.candidates,
                emitter=emitter,
                parent_span_id=result.ingest_span_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed_run = self._ledger.mark_failed(
                run_id,
                code="processing_failed",
                stage="processing",
                message="材料处理失败，请通过 trace_id 查看详情",
                now=self._clock.now(),
            )
            public_failure = _public_failure_data(failed_run)
            emitter.emit(
                EventType.ERROR,
                payload={
                    "classification": public_failure["code"],
                    **public_failure,
                    "error_type": type(exc).__name__,
                },
            )
            emitter.emit(
                "acquisition.failed",
                payload={
                    "run_id": run_id,
                    **public_failure,
                    "status": "failed",
                },
            )
        finally:
            self._notify(run_id)

    def _new_emitter(self, trace_id: str) -> EventEmitter:
        return EventEmitter(self._event_sink(), self._clock, trace_id=trace_id)

    def _resume_emitter(self, trace_id: str) -> EventEmitter:
        events = self._trace_store.events(trace_id)
        next_seq = max((event.seq for event in events), default=-1) + 1
        span_numbers = [
            int(span_id.rsplit(":s", 1)[1])
            for event in events
            if (span_id := event.span_id) is not None
            and span_id.startswith(f"{trace_id}:s")
            and span_id.rsplit(":s", 1)[1].isdigit()
        ]
        return EventEmitter(
            self._event_sink(),
            self._clock,
            trace_id=trace_id,
            initial_seq=next_seq,
            initial_span_counter=max(span_numbers, default=-1) + 1,
        )

    def _event_sink(self) -> EventSink:
        sink = EventSink()
        sink.register_durable(self._trace_store)
        return sink

    def _open_ingest_span(self, trace_id: str) -> str | None:
        open_spans: set[str] = set()
        for event in self._trace_store.events(trace_id):
            if event.span_id is None:
                continue
            if event.type == "ingest.started":
                open_spans.add(event.span_id)
            elif event.type == "ingest.ended":
                open_spans.discard(event.span_id)
        return min(open_spans) if open_spans else None

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _verify_token(self, run: AcquisitionRun, token: str) -> None:
        self._ledger.verify_control_token(
            run.run_id,
            token_hash=self._token_hash(token),
            now=self._clock.now(),
        )

    def _notify(self, run_id: str) -> None:
        self._changed.setdefault(run_id, asyncio.Event()).set()

    def _view(self, run: AcquisitionRun) -> AcquisitionView:
        candidates: list[AcquisitionCandidateView] = []
        if run.prepared is not None:
            candidates = [
                AcquisitionCandidateView(
                    item_id=item.item_id,
                    concept=item.concept,
                    summary=item.summary,
                    confidence=item.confidence,
                    evidence=[evidence.quote[:240] for evidence in item.evidence[:2]],
                    classification=self._persistence.classifications.propose_item(item),
                )
                for item in run.prepared.candidates
            ]
        failure = _public_failure_data(run) if run.status == "failed" else None
        return AcquisitionView(
            run_id=run.run_id,
            trace_id=run.trace_id,
            kind=run.kind,
            display_name=run.display_name,
            status=run.status,
            candidates=candidates,
            resource_id=run.resource_id,
            error_code=(
                None if failure is None else cast("AcquisitionFailureCode", failure["code"])
            ),
            error_stage=(
                None if failure is None else cast("AcquisitionFailureStage", failure["stage"])
            ),
            error_message=None if failure is None else failure["reason"],
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @staticmethod
    def _project_events(run: AcquisitionRun, events: list[AgentEvent]) -> list[AcquisitionUiEvent]:
        projected: list[AcquisitionUiEvent] = []
        mapping: dict[str, AcquisitionUiEventType] = {
            "acquisition.queued": "acquisition.queued",
            "acquisition.started": "acquisition.started",
            LearningEvent.RESOURCE_READ: "acquisition.fetched",
            LearningEvent.ITEMS_EXTRACTED: "acquisition.candidates_ready",
            "approval.requested": "acquisition.needs_input",
            "acquisition.succeeded": "acquisition.succeeded",
            "acquisition.failed": "acquisition.failed",
            "acquisition.cancelled": "acquisition.cancelled",
        }
        for event in events:
            public_type = mapping.get(event.type)
            if public_type is None:
                continue
            data: dict[str, str | int | float | bool | None] = {}
            if event.type == LearningEvent.ITEMS_EXTRACTED:
                candidates = event.payload.get("candidates")
                data["candidate_count"] = (
                    len(cast("list[object]", candidates)) if isinstance(candidates, list) else 0
                )
            if public_type in {"acquisition.failed", "acquisition.succeeded"}:
                if public_type == "acquisition.failed":
                    data.update(_public_failure_data(run))
                else:
                    value = event.payload.get("resource_id")
                    if isinstance(value, str):
                        data["resource_id"] = value
            projected.append(
                AcquisitionUiEvent(
                    sequence=event.seq + 1,
                    type=public_type,
                    run_id=run.run_id,
                    trace_id=run.trace_id,
                    data=data,
                )
            )
        return projected

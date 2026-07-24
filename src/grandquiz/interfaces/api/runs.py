"""API 长操作的进程内 owner；执行证据仍以 trace.db 为权威。"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.grounded_answer import (
    GroundedAnswerRequest,
    GroundedAnswerResult,
    GroundedDocumentAnswer,
)
from grandquiz.domain.learning.store import Store
from grandquiz.interfaces.api.errors import ErrorResponse
from grandquiz.kernel.clock import SystemClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider

RunStatus = Literal["queued", "running", "needs_input", "succeeded", "failed", "cancelled"]

_RUN_QUEUED = "interface.api_run.queued"
_RUN_STARTED = "interface.api_run.started"
_RUN_ENDED = "interface.api_run.ended"


class QuestionRequest(BaseModel):
    query: str = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def query_is_not_blank(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query 不能为空")
        return query


class RunView(BaseModel):
    run_id: str
    trace_id: str
    status: RunStatus
    result: GroundedAnswerResult | None = None
    error: ErrorResponse | None = None


class UiEvent(BaseModel):
    sequence: int = Field(ge=1)
    type: str
    run_id: str
    trace_id: str
    data: dict[str, object] = Field(default_factory=dict)


def _empty_ui_events() -> list[UiEvent]:
    return []


@dataclass
class _RunRecord:
    run_id: str
    trace_id: str
    status: RunStatus = "queued"
    result: GroundedAnswerResult | None = None
    error: ErrorResponse | None = None
    task: asyncio.Task[None] | None = None
    events: list[UiEvent] = field(default_factory=_empty_ui_events)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    emitter: EventEmitter | None = None
    span_id: str | None = None

    def view(self) -> RunView:
        return RunView(
            run_id=self.run_id,
            trace_id=self.trace_id,
            status=self.status,
            result=self.result,
            error=self.error,
        )


class RunManager:
    """拥有后台 task，并在 FastAPI lifespan 结束时统一取消/收口。"""

    def __init__(
        self,
        *,
        store: Store,
        provider: Provider,
        trace_store: TraceStore,
    ) -> None:
        self._store = store
        self._provider = provider
        self._trace_store = trace_store
        self._records: dict[str, _RunRecord] = {}

    def start_grounded_answer(
        self,
        *,
        resource_id: str,
        request: QuestionRequest,
    ) -> RunView:
        record = _RunRecord(run_id=uuid.uuid4().hex, trace_id=uuid.uuid4().hex)
        self._records[record.run_id] = record
        sink = EventSink()
        sink.register_durable(self._trace_store)
        sink.subscribe(lambda event: self._project_event(record, event))
        record.emitter = EventEmitter(sink, SystemClock(), trace_id=record.trace_id)
        record.span_id = record.emitter.new_span_id()
        record.emitter.emit(
            _RUN_QUEUED,
            payload={"run_id": record.run_id},
        )
        snapshot = record.view()
        record.task = asyncio.create_task(
            self._execute_grounded_answer(
                record,
                GroundedAnswerRequest(
                    query=request.query,
                    resource_ids=[resource_id],
                ),
            ),
            name=f"grandquiz-api-run:{record.run_id}",
        )
        return snapshot

    def get(self, run_id: str) -> RunView | None:
        record = self._records.get(run_id)
        return None if record is None else record.view()

    async def cancel(self, run_id: str) -> RunView | None:
        record = self._records.get(run_id)
        if record is None:
            return None
        task = record.task
        if record.status not in {"succeeded", "failed", "cancelled"} and task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return record.view()

    async def iter_events(
        self,
        run_id: str,
        *,
        after: int = 0,
    ) -> AsyncIterator[UiEvent]:
        record = self._records[run_id]
        cursor = after
        while True:
            fresh = [event for event in record.events if event.sequence > cursor]
            for event in fresh:
                cursor = event.sequence
                yield event
            if record.status in {"succeeded", "failed", "cancelled"}:
                return
            record.changed.clear()
            if any(event.sequence > cursor for event in record.events):
                continue
            if record.status in {"succeeded", "failed", "cancelled"}:
                continue
            await record.changed.wait()

    async def _execute_grounded_answer(
        self,
        record: _RunRecord,
        request: GroundedAnswerRequest,
    ) -> None:
        emitter = record.emitter
        span_id = record.span_id
        if emitter is None or span_id is None:  # pragma: no cover - start_grounded_answer invariant
            raise RuntimeError("API run 缺少事件脊柱")
        record.status = "running"
        emitter.emit(
            _RUN_STARTED,
            span_id=span_id,
            payload={"run_id": record.run_id},
        )
        try:
            record.result = await GroundedDocumentAnswer(
                store=self._store,
                provider=self._provider,
            ).answer(request, emitter=emitter, parent_span_id=span_id)
        except asyncio.CancelledError:
            record.status = "cancelled"
            emitter.emit(
                _RUN_ENDED,
                span_id=span_id,
                payload={"status": "cancelled"},
            )
            raise
        except Exception as exc:
            record.status = "failed"
            record.error = ErrorResponse(
                code="run_failed",
                message="运行失败，请通过 trace_id 查看详情",
                retryable=True,
                trace_id=record.trace_id,
            )
            emitter.emit(
                EventType.ERROR,
                span_id=span_id,
                payload={
                    "classification": "api_run_failed",
                    "error_type": type(exc).__name__,
                },
            )
            emitter.emit(
                _RUN_ENDED,
                span_id=span_id,
                payload={"status": "failed", "code": "run_failed"},
            )
        else:
            record.status = "succeeded"
            emitter.emit(
                _RUN_ENDED,
                span_id=span_id,
                payload={"status": "succeeded"},
            )

    @staticmethod
    def _append_event(
        record: _RunRecord,
        event_type: str,
        data: Mapping[str, object] | None = None,
    ) -> None:
        record.events.append(
            UiEvent(
                sequence=len(record.events) + 1,
                type=event_type,
                run_id=record.run_id,
                trace_id=record.trace_id,
                data={} if data is None else dict(data),
            )
        )
        record.changed.set()

    def _project_event(self, record: _RunRecord, event: AgentEvent) -> None:
        payload = event.payload
        if event.type == _RUN_QUEUED:
            self._append_event(record, "run.queued")
        elif event.type == _RUN_STARTED:
            self._append_event(record, "run.started")
        elif event.type == _RUN_ENDED:
            status = payload.get("status")
            if isinstance(status, str):
                data = {"code": "run_failed"} if status == "failed" else None
                self._append_event(record, f"run.{status}", data)
        elif event.type == LearningEvent.DOCUMENT_NODES_SEARCHED:
            candidates = payload.get("candidate_node_ids")
            count = len(cast("list[object]", candidates)) if isinstance(candidates, list) else 0
            self._append_event(record, "search.completed", {"candidate_count": count})
        elif event.type == LearningEvent.DOCUMENT_NODE_READ:
            self._append_event(
                record,
                "node.read",
                self._selected_payload(
                    payload,
                    "resource_id",
                    "revision_id",
                    "node_id",
                    "chars",
                    "budget_used",
                    "budget_limit",
                ),
            )
        elif event.type == LearningEvent.CITATION_RESOLVED:
            self._append_event(
                record,
                "citation.resolved",
                self._selected_payload(
                    payload,
                    "revision_id",
                    "node_id",
                    "start_offset",
                    "end_offset",
                ),
            )
        elif event.type == LearningEvent.GROUNDED_ANSWER_ENDED:
            self._append_event(
                record,
                "answer.completed",
                self._selected_payload(payload, "status", "citation_count"),
            )

    @staticmethod
    def _selected_payload(payload: Mapping[str, Any], *keys: str) -> dict[str, object]:
        return {
            key: value
            for key in keys
            if (value := payload.get(key)) is None or isinstance(value, (str, int, float, bool))
        }

    async def aclose(self) -> None:
        pending = [
            record.task
            for record in self._records.values()
            if record.task is not None and not record.task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

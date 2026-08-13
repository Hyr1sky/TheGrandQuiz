"""AgentEvent 的 Web 安全投影；不暴露 raw payload、prompt 或正文。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel

from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.kernel.trace import Span, TraceStore, build_span_tree, summarize_token_usage

TraceStatus = Literal[
    "idle",
    "running",
    "waiting_input",
    "completed",
    "failed",
    "cancelled",
]
TraceUiType = Literal[
    "run",
    "model",
    "tool",
    "assessment",
    "approval",
    "recovery",
    "error",
    "runtime",
]


class TraceSummary(BaseModel):
    trace_id: str
    status: TraceStatus
    event_count: int
    model_calls: int
    tool_calls: int
    error_count: int
    recovery_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_context_tokens: int | None
    context_budget_tokens: int | None
    remaining_context_tokens: int | None
    context_estimation: Literal["heuristic"] | None
    started_at: float | None
    updated_at: float | None
    latency_ms: float | None


class TraceSpanView(BaseModel):
    span_id: str
    parent_span_id: str | None
    type: TraceUiType
    status: Literal["running", "completed", "failed"]
    start_sequence: int
    started_at: float
    ended_at: float | None
    latency_ms: float | None
    tokens: int | None
    tool_name: str | None


class TraceUiEvent(BaseModel):
    sequence: int
    type: TraceUiType
    timestamp: float
    span_id: str | None
    parent_span_id: str | None
    status: Literal["started", "completed", "failed", "event"]
    tokens: int | None = None
    latency_ms: float | None = None
    tool_name: str | None = None
    recovered: bool = False


class TraceSnapshot(BaseModel):
    summary: TraceSummary
    spans: list[TraceSpanView]
    events: list[TraceUiEvent]


class TraceObservatory:
    """TraceStore 上的 local-first 安全 projection owner。"""

    def __init__(self, trace_store: TraceStore) -> None:
        self._trace_store = trace_store
        self._known_traces: set[str] = set()
        self._changed: dict[str, asyncio.Event] = {}

    def register_trace(self, trace_id: str) -> None:
        self._known_traces.add(trace_id)
        self._changed.setdefault(trace_id, asyncio.Event())

    def on_event(self, event: AgentEvent) -> None:
        """Processor seam；durable TraceStore 已先落库，这里只唤醒安全 SSE 投影。"""
        self.register_trace(event.trace_id)
        self._changed[event.trace_id].set()

    def exists(self, trace_id: str) -> bool:
        return trace_id in self._known_traces or bool(self._trace_store.events(trace_id))

    def snapshot(self, trace_id: str) -> TraceSnapshot:
        events = self._trace_store.events(trace_id)
        projected_events = _project_events(events)
        spans = _project_spans(build_span_tree(events))
        usage = summarize_token_usage(events)
        context = _latest_context_status(events)
        if events:
            started_at = events[0].ts
            updated_at = events[-1].ts
            latency_ms = max(0.0, (updated_at - started_at) * 1000)
        else:
            started_at = None
            updated_at = None
            latency_ms = None
        return TraceSnapshot(
            summary=TraceSummary(
                trace_id=trace_id,
                status=_trace_status(events),
                event_count=len(events),
                model_calls=sum(event.type == EventType.MODEL_STARTED for event in events),
                tool_calls=sum(event.type == EventType.TOOL_CALL_STARTED for event in events),
                error_count=sum(event.type == EventType.ERROR for event in events),
                recovery_count=sum(event.type == EventType.RECOVERY_DECIDED for event in events),
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                estimated_context_tokens=None if context is None else context[0],
                context_budget_tokens=None if context is None else context[1],
                remaining_context_tokens=None if context is None else context[2],
                context_estimation=None if context is None else "heuristic",
                started_at=started_at,
                updated_at=updated_at,
                latency_ms=latency_ms,
            ),
            spans=spans,
            events=projected_events,
        )

    async def iter_events(
        self,
        trace_id: str,
        *,
        after: int = 0,
        follow: bool = True,
    ) -> AsyncIterator[TraceUiEvent]:
        cursor = after
        changed = self._changed.setdefault(trace_id, asyncio.Event())
        while True:
            fresh = [
                event
                for event in _project_events(self._trace_store.events(trace_id))
                if event.sequence > cursor
            ]
            for event in fresh:
                cursor = event.sequence
                yield event
            if not follow:
                return
            changed.clear()
            if any(event.seq + 1 > cursor for event in self._trace_store.events(trace_id)):
                continue
            await changed.wait()


def _usage_tokens(payload: Mapping[str, Any]) -> int | None:
    usage_obj = payload.get("usage")
    if not isinstance(usage_obj, Mapping):
        return None
    usage = cast("Mapping[str, Any]", usage_obj)
    total = usage.get("total_tokens")
    return total if isinstance(total, int) else None


def _latest_context_status(events: Iterable[AgentEvent]) -> tuple[int, int, int] | None:
    for event in reversed(list(events)):
        if event.type != EventType.CONTEXT_PREPARED:
            continue
        estimated = event.payload.get("estimated_tokens")
        budget = event.payload.get("budget_tokens")
        remaining = event.payload.get("remaining_tokens")
        if all(isinstance(value, int) for value in (estimated, budget, remaining)):
            return cast("tuple[int, int, int]", (estimated, budget, remaining))
    return None


def _event_failed(event: AgentEvent) -> bool:
    if event.type == EventType.ERROR:
        return True
    if event.payload.get("ok") is False:
        return True
    return event.payload.get("status") in {"failed", "cancelled"}


def _ui_type(internal_type: str) -> TraceUiType:
    if internal_type == EventType.ERROR:
        return "error"
    if internal_type.startswith("model"):
        return "model"
    if internal_type.startswith("tool_call"):
        return "tool"
    if internal_type.startswith(("learning.", "web.assessment")):
        return "assessment"
    if internal_type.startswith("approval"):
        return "approval"
    if internal_type.startswith("recovery"):
        return "recovery"
    if internal_type.startswith(("agent_turn", "turn")) or ".run." in internal_type:
        return "run"
    return "runtime"


def _project_events(events: Iterable[AgentEvent]) -> list[TraceUiEvent]:
    starts: dict[str, float] = {}
    projected: list[TraceUiEvent] = []
    for event in events:
        if event.span_id is not None and event.type.endswith(".started"):
            starts[event.span_id] = event.ts
        failed = _event_failed(event)
        if failed:
            status: Literal["started", "completed", "failed", "event"] = "failed"
        elif event.type.endswith(".started"):
            status = "started"
        elif event.type.endswith(".ended"):
            status = "completed"
        else:
            status = "event"
        start_ts = starts.get(event.span_id) if event.span_id is not None else None
        latency_ms = (
            max(0.0, (event.ts - start_ts) * 1000)
            if start_ts is not None and event.type.endswith(".ended")
            else None
        )
        tool_name_obj = (
            event.payload.get("tool_name") if event.type == EventType.TOOL_CALL_STARTED else None
        )
        projected.append(
            TraceUiEvent(
                sequence=event.seq + 1,
                type=_ui_type(event.type),
                timestamp=event.ts,
                span_id=event.span_id,
                parent_span_id=event.parent_span_id,
                status=status,
                tokens=_usage_tokens(event.payload),
                latency_ms=latency_ms,
                tool_name=tool_name_obj if isinstance(tool_name_obj, str) else None,
                recovered=event.payload.get("recovered") is True,
            )
        )
    return projected


def _project_spans(roots: Iterable[Span]) -> list[TraceSpanView]:
    projected: list[TraceSpanView] = []

    def visit(span: Span) -> None:
        tool_name_obj = span.input.get("tool_name") if span.type == "tool_call" else None
        if span.error is not None or (
            span.output is not None
            and (
                span.output.get("ok") is False
                or span.output.get("status") in {"failed", "cancelled"}
            )
        ):
            status: Literal["running", "completed", "failed"] = "failed"
        elif span.end_ts is None:
            status = "running"
        else:
            status = "completed"
        projected.append(
            TraceSpanView(
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                type=_ui_type(span.type),
                status=status,
                start_sequence=span.start_seq + 1,
                started_at=span.start_ts,
                ended_at=span.end_ts,
                latency_ms=span.latency * 1000 if span.latency is not None else None,
                tokens=span.tokens,
                tool_name=tool_name_obj if isinstance(tool_name_obj, str) else None,
            )
        )
        for child in span.children:
            visit(child)

    for root in roots:
        visit(root)
    return projected


def _trace_status(events: list[AgentEvent]) -> TraceStatus:
    if not events:
        return "idle"
    if events[-1].type in {
        "learning.question_asked",
        "learning.answer_judged",
        "approval.requested",
        "voice.reviewable",
    }:
        return "waiting_input"
    for event in reversed(events):
        status = event.payload.get("status")
        if status == "cancelled":
            return "cancelled"
        if status == "failed":
            return "failed"
        if status == "completed":
            return "completed"
        if status == "running":
            return "running"
        if event.type in {EventType.AGENT_TURN_ENDED, EventType.TURN_ENDED}:
            return "failed" if event.payload.get("ok") is False else "completed"
        if event.type.endswith("run.ended"):
            return "completed"
    return "running"

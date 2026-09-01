"""TraceStore 的 REST/SSE adapter；语义与脱敏由共享 projector 拥有。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence

from grandquiz.domain.learning.assessment.workflow import AssessmentWorkflowDescriptor
from grandquiz.interfaces.trace_projection import (
    SafeTraceEventV1,
    SafeTraceRunV1,
    TraceRunStatus,
    project_trace,
)
from grandquiz.kernel.events import AgentEvent
from grandquiz.kernel.trace import TraceStore


class TraceObservatory:
    """为本地 Web 提供持久 trace snapshot 与可恢复增量订阅。"""

    def __init__(
        self,
        trace_store: TraceStore,
        *,
        descriptor_resolver: (
            Callable[[Sequence[AgentEvent]], AssessmentWorkflowDescriptor | None] | None
        ) = None,
    ) -> None:
        self._trace_store = trace_store
        self._descriptor_resolver = descriptor_resolver
        self._known_traces: set[str] = set()
        self._changed: dict[str, asyncio.Event] = {}

    def register_trace(self, trace_id: str) -> None:
        self._known_traces.add(trace_id)
        self._changed.setdefault(trace_id, asyncio.Event())

    def on_event(self, event: AgentEvent) -> None:
        """Durable TraceStore 已先落库；此 adapter 只负责唤醒 SSE。"""
        self.register_trace(event.trace_id)
        self._changed[event.trace_id].set()

    def exists(self, trace_id: str) -> bool:
        return trace_id in self._known_traces or bool(self._trace_store.events(trace_id))

    def snapshot(self, trace_id: str) -> SafeTraceRunV1:
        events = self._trace_store.events(trace_id)
        descriptor = (
            self._descriptor_resolver(events) if self._descriptor_resolver is not None else None
        )
        return project_trace(events, trace_id=trace_id, descriptor=descriptor)

    def list_runs(
        self,
        *,
        status: TraceRunStatus | None = None,
        limit: int = 20,
    ) -> list[SafeTraceRunV1]:
        """返回有界安全历史；状态在共享 projector 之后于服务端筛选。"""
        if not 1 <= limit <= 50:
            raise ValueError("limit 必须在 1 到 50 之间")
        batch_size = 50
        offset = 0
        runs: list[SafeTraceRunV1] = []
        while len(runs) < limit:
            trace_ids = self._trace_store.recent_trace_ids(
                limit=min(batch_size, limit) if status is None else batch_size,
                offset=offset,
            )
            if not trace_ids:
                break
            for trace_id in trace_ids:
                run = self.snapshot(trace_id)
                if status is None or run.status == status:
                    runs.append(run)
                if len(runs) == limit:
                    break
            offset += len(trace_ids)
        return runs

    async def iter_events(
        self,
        trace_id: str,
        *,
        after: int = 0,
        follow: bool = True,
    ) -> AsyncIterator[SafeTraceEventV1]:
        cursor = after
        changed = self._changed.setdefault(trace_id, asyncio.Event())
        while True:
            raw_events = self._trace_store.events(trace_id)
            descriptor = (
                self._descriptor_resolver(raw_events)
                if self._descriptor_resolver is not None
                else None
            )
            fresh = [
                event
                for event in project_trace(
                    raw_events,
                    trace_id=trace_id,
                    descriptor=descriptor,
                ).events
                if event.sequence > cursor
            ]
            for event in fresh:
                cursor = event.sequence
                yield event
            if not follow:
                return
            changed.clear()
            if raw_events and raw_events[-1].seq + 1 > cursor:
                continue
            await changed.wait()

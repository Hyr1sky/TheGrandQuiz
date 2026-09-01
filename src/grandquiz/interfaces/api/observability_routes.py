"""Local Web runtime 的安全 trace snapshot 与增量事件入口。"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from grandquiz.interfaces.api.errors import ApiError
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.trace_projection import (
    SafeTraceEventV1,
    SafeTraceRunV1,
    TraceRunStatus,
)

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])


def observatory_from(request: Request) -> TraceObservatory:
    return cast("TraceObservatory", request.app.state.trace_observatory)


@router.get("/traces", response_model=list[SafeTraceRunV1])
async def list_trace_snapshots(
    request: Request,
    status: Annotated[TraceRunStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[SafeTraceRunV1]:
    return observatory_from(request).list_runs(status=status, limit=limit)


@router.get("/traces/{trace_id}", response_model=SafeTraceRunV1)
async def get_trace_snapshot(trace_id: str, request: Request) -> SafeTraceRunV1:
    observatory = observatory_from(request)
    if not observatory.exists(trace_id):
        raise ApiError(
            status_code=404,
            code="trace_not_found",
            message=f"trace 不存在：{trace_id}",
        )
    return observatory.snapshot(trace_id)


@router.get(
    "/traces/{trace_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "model": SafeTraceEventV1,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/SafeTraceEventV1"},
                }
            },
        }
    },
)
async def stream_trace_events(
    trace_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    follow: bool = Query(default=True),
) -> StreamingResponse:
    observatory = observatory_from(request)
    if not observatory.exists(trace_id):
        raise ApiError(
            status_code=404,
            code="trace_not_found",
            message=f"trace 不存在：{trace_id}",
        )

    async def stream() -> AsyncIterator[str]:
        async for event in observatory.iter_events(
            trace_id,
            after=after,
            follow=follow,
        ):
            payload = json.dumps(
                event.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.sequence}\nevent: trace.event\ndata: {payload}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

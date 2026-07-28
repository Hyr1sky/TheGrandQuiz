"""Local Web runtime 的安全 trace snapshot 与增量事件入口。"""

import json
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from grandquiz.interfaces.api.errors import ApiError
from grandquiz.interfaces.api.observability import (
    TraceObservatory,
    TraceSnapshot,
    TraceUiEvent,
)

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])


def observatory_from(request: Request) -> TraceObservatory:
    return cast("TraceObservatory", request.app.state.trace_observatory)


@router.get("/traces/{trace_id}", response_model=TraceSnapshot)
async def get_trace_snapshot(trace_id: str, request: Request) -> TraceSnapshot:
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
            "model": TraceUiEvent,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/TraceUiEvent"},
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

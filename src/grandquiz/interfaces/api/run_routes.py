"""长操作的查询、流式投影与控制入口。"""

import json
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from grandquiz.interfaces.api.errors import ApiError
from grandquiz.interfaces.api.runs import RunManager, RunView

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def run_manager_from(request: Request) -> RunManager:
    return cast("RunManager", request.app.state.run_manager)


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
)
async def stream_run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    manager = run_manager_from(request)
    if manager.get(run_id) is None:
        raise ApiError(
            status_code=404,
            code="run_not_found",
            message=f"运行不存在：{run_id}",
        )

    async def stream() -> AsyncIterator[str]:
        async for event in manager.iter_events(run_id, after=after):
            payload = json.dumps(
                event.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel", response_model=RunView)
async def cancel_run(run_id: str, request: Request) -> RunView:
    run = await run_manager_from(request).cancel(run_id)
    if run is None:
        raise ApiError(
            status_code=404,
            code="run_not_found",
            message=f"运行不存在：{run_id}",
        )
    return run


@router.get("/{run_id}", response_model=RunView)
async def get_run(run_id: str, request: Request) -> RunView:
    run = run_manager_from(request).get(run_id)
    if run is None:
        raise ApiError(
            status_code=404,
            code="run_not_found",
            message=f"运行不存在：{run_id}",
        )
    return run

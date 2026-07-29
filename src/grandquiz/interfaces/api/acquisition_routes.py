"""材料上传、URL 导入、可恢复审批与状态流。"""

import json
from collections.abc import AsyncIterator
from typing import Literal, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, model_validator

from grandquiz.domain.learning.acquisition import AcquisitionTransitionError
from grandquiz.interfaces.api.acquisitions import (
    AcquisitionCommitError,
    AcquisitionCreated,
    AcquisitionInputError,
    AcquisitionManager,
    AcquisitionUiEvent,
    AcquisitionView,
)
from grandquiz.interfaces.api.errors import ApiError

router = APIRouter(prefix="/api/v1/acquisitions", tags=["acquisitions"])


class CreateAcquisitionRequest(BaseModel):
    kind: Literal["upload", "url"]
    filename: str | None = None
    content: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "CreateAcquisitionRequest":
        if self.kind == "upload" and (not self.filename or self.content is None):
            raise ValueError("upload 需要 filename 与 content")
        if self.kind == "url" and not self.url:
            raise ValueError("url 导入需要 url")
        return self


class AcquisitionList(BaseModel):
    items: list[AcquisitionView]


class ApprovalRequest(BaseModel):
    resume_token: str
    approved_item_ids: list[str]


class CancelRequest(BaseModel):
    resume_token: str


def manager_from(request: Request) -> AcquisitionManager:
    return cast("AcquisitionManager", request.app.state.acquisition_manager)


@router.post("", response_model=AcquisitionCreated, status_code=201)
async def create_acquisition(
    body: CreateAcquisitionRequest, request: Request
) -> AcquisitionCreated:
    manager = manager_from(request)
    try:
        if body.kind == "upload":
            return manager.start_upload(
                filename=cast("str", body.filename),
                content=cast("str", body.content),
            )
        return manager.start_url(url=cast("str", body.url))
    except AcquisitionInputError as exc:
        raise ApiError(
            status_code=400,
            code=exc.code,
            message=str(exc),
        ) from exc


@router.get("", response_model=AcquisitionList)
async def list_acquisitions(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> AcquisitionList:
    return AcquisitionList(items=manager_from(request).recent(limit=limit))


@router.get("/{run_id}", response_model=AcquisitionView)
async def get_acquisition(run_id: str, request: Request) -> AcquisitionView:
    view = manager_from(request).get(run_id)
    if view is None:
        raise _not_found(run_id)
    return view


@router.post("/{run_id}/approval", response_model=AcquisitionView)
async def approve_acquisition(
    run_id: str, body: ApprovalRequest, request: Request
) -> AcquisitionView:
    try:
        return await manager_from(request).approve(
            run_id,
            resume_token=body.resume_token,
            approved_item_ids=body.approved_item_ids,
        )
    except KeyError as exc:
        raise _not_found(run_id) from exc
    except AcquisitionTransitionError as exc:
        raise ApiError(
            status_code=409,
            code="acquisition_conflict",
            message=str(exc),
        ) from exc
    except AcquisitionCommitError as exc:
        raise ApiError(
            status_code=500,
            code="acquisition_commit_failed",
            message="知识快照提交失败，请重试",
            retryable=True,
            trace_id=exc.trace_id,
        ) from exc


@router.post("/{run_id}/cancel", response_model=AcquisitionView)
async def cancel_acquisition(run_id: str, body: CancelRequest, request: Request) -> AcquisitionView:
    try:
        return await manager_from(request).cancel(
            run_id,
            resume_token=body.resume_token,
        )
    except KeyError as exc:
        raise _not_found(run_id) from exc
    except AcquisitionTransitionError as exc:
        raise ApiError(
            status_code=409,
            code="acquisition_conflict",
            message=str(exc),
        ) from exc


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "model": AcquisitionUiEvent,
            "content": {
                "text/event-stream": {"schema": {"$ref": "#/components/schemas/AcquisitionUiEvent"}}
            },
        }
    },
)
async def stream_acquisition_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    manager = manager_from(request)
    if manager.get(run_id) is None:
        raise _not_found(run_id)

    async def stream() -> AsyncIterator[str]:
        async for event in manager.iter_events(run_id, after=after):
            payload = json.dumps(
                event.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield (f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _not_found(run_id: str) -> ApiError:
    return ApiError(
        status_code=404,
        code="acquisition_not_found",
        message=f"材料导入运行不存在：{run_id}",
    )

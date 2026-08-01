"""FastAPI contracts for persistent material discovery and review."""

from typing import Literal, cast

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.discovery import (
    MaterialDiscoveryBatchV1,
    MaterialDiscoveryConflict,
    MaterialSourcePolicyV1,
)
from grandquiz.domain.learning.ingest.web_search import SearchError
from grandquiz.interfaces.api.discoveries import DiscoveryManager, MaterialReviewResult
from grandquiz.interfaces.api.errors import ApiError

router = APIRouter(prefix="/api/v1/discoveries", tags=["discoveries"])


class CreateDiscoveryRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    source_policy: MaterialSourcePolicyV1 = Field(default_factory=MaterialSourcePolicyV1)

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("topic must not be blank")
        return normalized


class DiscoveryList(BaseModel):
    items: list[MaterialDiscoveryBatchV1]


class MaterialReviewRequest(BaseModel):
    request_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    reason: str | None = None
    control_token: str | None = Field(default=None, min_length=24)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id must not be blank")
        return normalized

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        return None if value is None else value.strip() or None


def manager_from(request: Request) -> DiscoveryManager:
    return cast("DiscoveryManager", request.app.state.discovery_manager)


@router.post("", response_model=MaterialDiscoveryBatchV1, status_code=201)
async def create_discovery(
    body: CreateDiscoveryRequest,
    request: Request,
) -> MaterialDiscoveryBatchV1:
    try:
        return await manager_from(request).discover(
            body.topic,
            source_policy=body.source_policy,
        )
    except MaterialDiscoveryConflict as exc:
        raise ApiError(
            status_code=503,
            code="search_provider_unavailable",
            message="未配置 Web Search provider",
        ) from exc
    except SearchError as exc:
        raise ApiError(
            status_code=502,
            code=f"search_{exc.reason}",
            message=str(exc),
            retryable=True,
        ) from exc


@router.get("", response_model=DiscoveryList)
async def list_discoveries(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> DiscoveryList:
    return DiscoveryList(items=manager_from(request).recent(limit=limit))


@router.get("/{batch_id}", response_model=MaterialDiscoveryBatchV1)
async def get_discovery(batch_id: str, request: Request) -> MaterialDiscoveryBatchV1:
    batch = manager_from(request).get(batch_id)
    if batch is None:
        raise ApiError(status_code=404, code="discovery_not_found", message="材料发现批次不存在")
    return batch


@router.post("/candidates/{candidate_id}/review", response_model=MaterialReviewResult)
async def review_material_candidate(
    candidate_id: str,
    body: MaterialReviewRequest,
    request: Request,
) -> MaterialReviewResult:
    try:
        return manager_from(request).review(
            candidate_id,
            request_id=body.request_id,
            decision=body.decision,
            reason=body.reason,
            control_token=body.control_token,
        )
    except KeyError as exc:
        raise ApiError(
            status_code=404, code="material_candidate_not_found", message="候选不存在"
        ) from exc
    except MaterialDiscoveryConflict as exc:
        raise ApiError(status_code=409, code="material_review_conflict", message=str(exc)) from exc

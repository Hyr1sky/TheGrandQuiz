"""Local-only Eval inbox review and immutable dataset snapshot endpoints."""

from typing import Literal, cast

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.eval_inbox import (
    DatasetSnapshotV1,
    EvalInboxCandidateV1,
    EvalInboxConflict,
)
from grandquiz.domain.learning.grading_samples import GradingCalibrationSample
from grandquiz.interfaces.api.errors import ApiError
from grandquiz.interfaces.api.eval_management import EvalManagementService

router = APIRouter(prefix="/api/v1/eval", tags=["eval"])


class EvalCandidateList(BaseModel):
    items: list[EvalInboxCandidateV1]


class DatasetSnapshotList(BaseModel):
    items: list[DatasetSnapshotV1]


class BlindLabelImportRequest(BaseModel):
    request_id: str = Field(min_length=1)
    samples: list[GradingCalibrationSample] = Field(min_length=1, max_length=100)

    @field_validator("request_id")
    @classmethod
    def reject_blank_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id must not be blank")
        return normalized


class EvalReviewRequest(BaseModel):
    request_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1)

    @field_validator("request_id", "reason")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review fields must not be blank")
        return normalized


class DatasetSnapshotRequest(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=500)


def manager_from(request: Request) -> EvalManagementService:
    return cast("EvalManagementService", request.app.state.eval_management)


@router.get("/candidates", response_model=EvalCandidateList)
async def list_eval_candidates(request: Request) -> EvalCandidateList:
    return EvalCandidateList(items=manager_from(request).candidates())


@router.post("/candidates/sync", response_model=EvalCandidateList)
async def sync_eval_candidates(request: Request) -> EvalCandidateList:
    return EvalCandidateList(items=manager_from(request).sync_corrections())


@router.post("/candidates/blind-import", response_model=EvalCandidateList, status_code=201)
async def import_blind_labels(
    body: BlindLabelImportRequest,
    request: Request,
) -> EvalCandidateList:
    imported = manager_from(request).import_blind_labels(
        body.samples,
        request_id=body.request_id,
    )
    return EvalCandidateList(items=imported)


@router.post("/candidates/{candidate_id}/review", response_model=EvalInboxCandidateV1)
async def review_eval_candidate(
    candidate_id: str,
    body: EvalReviewRequest,
    request: Request,
) -> EvalInboxCandidateV1:
    try:
        return manager_from(request).review(
            candidate_id,
            request_id=body.request_id,
            decision=body.decision,
            reason=body.reason,
        )
    except KeyError as exc:
        raise ApiError(
            status_code=404, code="eval_candidate_not_found", message="Eval 候选不存在"
        ) from exc
    except EvalInboxConflict as exc:
        raise ApiError(status_code=409, code="eval_review_conflict", message=str(exc)) from exc


@router.post("/snapshots", response_model=DatasetSnapshotV1, status_code=201)
async def create_eval_snapshot(
    body: DatasetSnapshotRequest,
    request: Request,
) -> DatasetSnapshotV1:
    try:
        return manager_from(request).snapshot(body.candidate_ids)
    except KeyError as exc:
        raise ApiError(
            status_code=404, code="eval_candidate_not_found", message="Eval 候选不存在"
        ) from exc
    except EvalInboxConflict as exc:
        raise ApiError(status_code=409, code="eval_snapshot_conflict", message=str(exc)) from exc


@router.get("/snapshots", response_model=DatasetSnapshotList)
async def list_eval_snapshots(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> DatasetSnapshotList:
    return DatasetSnapshotList(items=manager_from(request).snapshots(limit=limit))


@router.get("/snapshots/{snapshot_id}", response_model=DatasetSnapshotV1)
async def get_eval_snapshot(snapshot_id: str, request: Request) -> DatasetSnapshotV1:
    try:
        return manager_from(request).get_snapshot(snapshot_id)
    except KeyError as exc:
        raise ApiError(
            status_code=404, code="eval_snapshot_not_found", message="Eval 快照不存在"
        ) from exc

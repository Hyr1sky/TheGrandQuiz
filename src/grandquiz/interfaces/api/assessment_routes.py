"""逐题考核的 HTTP commands 与安全状态投影。"""

from typing import cast

from fastapi import APIRouter, Request

from grandquiz.interfaces.api.assessment_runs import (
    AnswerSubmissionRequest,
    AssessmentAppealRequest,
    AssessmentCommandConflict,
    AssessmentManager,
    AssessmentStartRequest,
    AssessmentView,
    EvidenceRevealRequest,
    NextRoundRequest,
)
from grandquiz.interfaces.api.errors import ApiError

router = APIRouter(prefix="/api/v1/assessments", tags=["assessments"])


def assessment_manager_from(request: Request) -> AssessmentManager:
    return cast("AssessmentManager", request.app.state.assessment_manager)


@router.post("", response_model=AssessmentView, status_code=202)
async def start_assessment(
    command: AssessmentStartRequest,
    request: Request,
) -> AssessmentView:
    return assessment_manager_from(request).start(command)


@router.get("/{session_id}", response_model=AssessmentView)
async def get_assessment(session_id: str, request: Request) -> AssessmentView:
    assessment = assessment_manager_from(request).get(session_id)
    if assessment is None:
        raise ApiError(
            status_code=404,
            code="assessment_not_found",
            message=f"考核会话不存在：{session_id}",
        )
    return assessment


@router.delete("/{session_id}", response_model=AssessmentView)
async def cancel_assessment(session_id: str, request: Request) -> AssessmentView:
    assessment = await assessment_manager_from(request).cancel(session_id)
    if assessment is None:
        raise ApiError(
            status_code=404,
            code="assessment_not_found",
            message=f"考核会话不存在：{session_id}",
        )
    return assessment


@router.post(
    "/{session_id}/questions/{question_id}/answers",
    response_model=AssessmentView,
    status_code=202,
)
async def submit_assessment_answer(
    session_id: str,
    question_id: str,
    command: AnswerSubmissionRequest,
    request: Request,
) -> AssessmentView:
    try:
        assessment = assessment_manager_from(request).submit_answer(
            session_id,
            question_id,
            command,
        )
    except AssessmentCommandConflict as exc:
        raise ApiError(
            status_code=409,
            code="assessment_answer_conflict",
            message=str(exc),
        ) from exc
    if assessment is None:
        raise ApiError(
            status_code=404,
            code="assessment_question_not_found",
            message=f"当前考核题目不存在：{session_id}:{question_id}",
        )
    return assessment


@router.post(
    "/{session_id}/questions/{question_id}/appeals",
    response_model=AssessmentView,
    status_code=202,
)
async def submit_assessment_appeal(
    session_id: str,
    question_id: str,
    command: AssessmentAppealRequest,
    request: Request,
) -> AssessmentView:
    try:
        assessment = assessment_manager_from(request).submit_appeal(
            session_id,
            question_id,
            command,
        )
    except AssessmentCommandConflict as exc:
        raise ApiError(
            status_code=409,
            code="assessment_appeal_conflict",
            message=str(exc),
        ) from exc
    if assessment is None:
        raise ApiError(
            status_code=404,
            code="assessment_question_not_found",
            message=f"当前考核题目不存在：{session_id}:{question_id}",
        )
    return assessment


@router.post(
    "/{session_id}/next",
    response_model=AssessmentView,
    status_code=202,
)
async def start_next_assessment_round(
    session_id: str,
    command: NextRoundRequest,
    request: Request,
) -> AssessmentView:
    try:
        assessment = assessment_manager_from(request).next_round(session_id, command)
    except AssessmentCommandConflict as exc:
        raise ApiError(
            status_code=409,
            code="assessment_next_conflict",
            message=str(exc),
        ) from exc
    if assessment is None:
        raise ApiError(
            status_code=404,
            code="assessment_not_found",
            message=f"考核会话不存在：{session_id}",
        )
    return assessment


@router.post(
    "/{session_id}/retry",
    response_model=AssessmentView,
    status_code=202,
)
async def retry_assessment_round(
    session_id: str,
    command: NextRoundRequest,
    request: Request,
) -> AssessmentView:
    try:
        assessment = assessment_manager_from(request).retry_round(session_id, command)
    except AssessmentCommandConflict as exc:
        raise ApiError(
            status_code=409,
            code="assessment_retry_conflict",
            message=str(exc),
        ) from exc
    if assessment is None:
        raise ApiError(
            status_code=404,
            code="assessment_not_found",
            message=f"考核会话不存在：{session_id}",
        )
    return assessment


@router.post(
    "/{session_id}/questions/{question_id}/evidence/reveal",
    response_model=AssessmentView,
)
async def reveal_assessment_evidence(
    session_id: str,
    question_id: str,
    command: EvidenceRevealRequest,
    request: Request,
) -> AssessmentView:
    assessment = assessment_manager_from(request).reveal_evidence(
        session_id,
        question_id,
        command,
    )
    if assessment is None:
        raise ApiError(
            status_code=404,
            code="assessment_question_not_found",
            message=f"当前考核题目不存在：{session_id}:{question_id}",
        )
    return assessment

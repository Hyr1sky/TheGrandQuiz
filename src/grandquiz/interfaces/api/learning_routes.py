"""长期学习事实的 HTTP 查询，以及显式纠正、分类与词表命令。"""

from typing import Literal, cast

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.selection import apply_scope
from grandquiz.domain.learning.assessment_history import (
    DEMAND_VALIDATED,
    VERDICT_CORRECTED,
    AssessmentAttemptV1,
    CognitiveDemand,
    DemandValidationV1,
    LearnerProjectionV1,
    demand_validation_fact,
    project_assessment_attempts,
    project_demand_validations,
    project_learner,
    rebuild_learning_state,
    verdict_correction_fact,
)
from grandquiz.domain.learning.classification import (
    KnowledgeClassificationV1,
    KnowledgeKind,
    KnowledgeOrientation,
    ResourceRevisionClassificationV1,
    ReviewStatus,
    SourceGenre,
    TagAssignmentV1,
    TagCandidateV1,
    VocabularyTermView,
)
from grandquiz.domain.learning.classification_store import (
    ClassificationIdempotencyConflict,
)
from grandquiz.domain.learning.eval_candidates import (
    GradingEvalCandidateV1,
    project_grading_eval_candidates,
)
from grandquiz.domain.learning.knowledge_facets import (
    KnowledgeFacetInventoryV1,
    build_knowledge_facet_inventory,
)
from grandquiz.domain.learning.models import derive_id
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.errors import ApiError
from grandquiz.interfaces.learning_outbox import publish_pending_learning_facts
from grandquiz.kernel.clock import Clock
from grandquiz.kernel.trace import TraceStore

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])


class AssessmentAttemptList(BaseModel):
    items: list[AssessmentAttemptV1]


class GradingEvalCandidateList(BaseModel):
    items: list[GradingEvalCandidateV1]


class LearnerProjectionList(BaseModel):
    items: list[LearnerProjectionV1]


class LearningReportV1(BaseModel):
    schema_version: Literal["learning-report.v1"] = "learning-report.v1"
    attempt_count: int
    projections: list[LearnerProjectionV1]


class VerdictCorrectionRequest(BaseModel):
    request_id: str = Field(min_length=1)
    final_verdict: VerdictLabel
    reason: str = Field(min_length=1)


class KnowledgeClassificationRequest(BaseModel):
    request_id: str = Field(min_length=1)
    primary_kind: KnowledgeKind
    orientations: set[KnowledgeOrientation] = Field(min_length=1)
    review_status: ReviewStatus = "approved"


class KnowledgeClassificationHistory(BaseModel):
    active: KnowledgeClassificationV1 | None
    history: list[KnowledgeClassificationV1]


class ResourceRevisionClassificationRequest(BaseModel):
    request_id: str = Field(min_length=1)
    primary_source_genre: SourceGenre


class VocabularyReviewRequest(BaseModel):
    request_id: str = Field(min_length=1)
    review_status: Literal["proposed", "approved", "deprecated"]
    replacement_term_id: str | None = None


class TagAssignmentRequest(BaseModel):
    request_id: str = Field(min_length=1)
    term_id: str = Field(min_length=1)


class TagAssignmentList(BaseModel):
    items: list[TagAssignmentV1]


class ClassificationReviewRequest(BaseModel):
    request_id: str = Field(min_length=1)
    review_status: ReviewStatus


class TagCandidateRequest(BaseModel):
    request_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    raw_value: str = Field(min_length=1)


class DemandValidationRequest(BaseModel):
    request_id: str = Field(min_length=1)
    validated_demand: CognitiveDemand | None
    validator_kind: Literal["user"] = "user"
    rationale: str = Field(min_length=1)


def _persistence(request: Request) -> LearningPersistence:
    return cast("LearningPersistence", request.app.state.persistence)


def _clock(request: Request) -> Clock:
    return cast("Clock", request.app.state.clock)


def _publish_learning_outbox(request: Request) -> None:
    persistence = _persistence(request)
    publish_pending_learning_facts(
        persistence.learning_facts,
        cast("TraceStore", request.app.state.trace_store),
    )


def _idempotency_conflict(exc: ClassificationIdempotencyConflict) -> ApiError:
    return ApiError(
        status_code=409,
        code="idempotency_conflict",
        message=str(exc),
    )


@router.get("/attempts", response_model=AssessmentAttemptList)
async def list_assessment_attempts(
    request: Request,
    trace_id: str | None = Query(default=None),
) -> AssessmentAttemptList:
    facts = _persistence(request).learning_facts.facts()
    attempts = project_assessment_attempts(facts)
    if trace_id is not None:
        attempts = [attempt for attempt in attempts if attempt.trace_id == trace_id]
    return AssessmentAttemptList(items=attempts)


@router.get("/eval-candidates", response_model=GradingEvalCandidateList)
async def list_grading_eval_candidates(request: Request) -> GradingEvalCandidateList:
    return GradingEvalCandidateList(
        items=project_grading_eval_candidates(_persistence(request).learning_facts.facts())
    )


@router.get("/attempts/{attempt_id}", response_model=AssessmentAttemptV1)
async def get_assessment_attempt(attempt_id: str, request: Request) -> AssessmentAttemptV1:
    attempts = project_assessment_attempts(_persistence(request).learning_facts.facts())
    attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
    if attempt is None:
        raise ApiError(
            status_code=404,
            code="assessment_attempt_not_found",
            message=f"考核记录不存在：{attempt_id}",
        )
    return attempt


@router.get("/projections", response_model=LearnerProjectionList)
async def list_learner_projections(request: Request) -> LearnerProjectionList:
    facts = _persistence(request).learning_facts.facts()
    attempts = project_assessment_attempts(facts)
    validations = project_demand_validations(facts)
    item_ids = sorted({attempt.item_id for attempt in attempts})
    return LearnerProjectionList(
        items=[
            projection
            for item_id in item_ids
            if (
                projection := project_learner(
                    attempts,
                    item_id=item_id,
                    demand_validations=validations,
                )
            )
            is not None
        ]
    )


@router.get("/report", response_model=LearningReportV1)
async def get_learning_report(request: Request) -> LearningReportV1:
    facts = _persistence(request).learning_facts.facts()
    attempts = project_assessment_attempts(facts)
    validations = project_demand_validations(facts)
    projections = [
        projection
        for item_id in sorted({attempt.item_id for attempt in attempts})
        if (
            projection := project_learner(
                attempts,
                item_id=item_id,
                demand_validations=validations,
            )
        )
        is not None
    ]
    return LearningReportV1(attempt_count=len(attempts), projections=projections)


@router.get("/facets", response_model=KnowledgeFacetInventoryV1)
async def get_knowledge_facet_inventory(
    request: Request,
    resource_id: str | None = Query(default=None),
) -> KnowledgeFacetInventoryV1:
    """Return active, approved classification counts for explicit product filtering."""

    persistence = _persistence(request)
    items = apply_scope(
        persistence.store.all_items(),
        None if resource_id is None else [resource_id],
    )
    return build_knowledge_facet_inventory(
        items,
        classifications=persistence.classifications,
    )


@router.get(
    "/projections/{item_id}",
    response_model=LearnerProjectionV1,
)
async def get_learner_projection(
    item_id: str,
    request: Request,
) -> LearnerProjectionV1:
    facts = _persistence(request).learning_facts.facts()
    projection = project_learner(
        project_assessment_attempts(facts),
        item_id=item_id,
        demand_validations=project_demand_validations(facts),
    )
    if projection is None:
        raise ApiError(
            status_code=404,
            code="learning_projection_not_found",
            message=f"知识点尚无考核历史：{item_id}",
        )
    return projection


@router.post(
    "/attempts/{attempt_id}/verdict-corrections",
    response_model=AssessmentAttemptV1,
)
async def correct_attempt_verdict(
    attempt_id: str,
    command: VerdictCorrectionRequest,
    request: Request,
) -> AssessmentAttemptV1:
    persistence = _persistence(request)
    facts = persistence.learning_facts.facts()
    attempts = project_assessment_attempts(facts)
    attempt = next((item for item in attempts if item.attempt_id == attempt_id), None)
    if attempt is None:
        raise ApiError(
            status_code=404,
            code="assessment_attempt_not_found",
            message=f"考核记录不存在：{attempt_id}",
        )
    event_id = derive_id(attempt.attempt_id, VERDICT_CORRECTED, command.request_id)
    existing_fact = next((fact for fact in facts if fact.event_id == event_id), None)
    if existing_fact is not None:
        if (
            existing_fact.payload.get("final_verdict") != command.final_verdict
            or existing_fact.payload.get("reason") != command.reason
        ):
            raise ApiError(
                status_code=409,
                code="idempotency_conflict",
                message="相同 request_id 已用于不同的判卷纠正",
            )
        _publish_learning_outbox(request)
        return next(
            item for item in project_assessment_attempts(facts) if item.attempt_id == attempt_id
        )
    previous_corrections = sorted(
        (
            fact
            for fact in facts
            if fact.event_type == VERDICT_CORRECTED and fact.payload.get("attempt_id") == attempt_id
        ),
        key=lambda fact: int(fact.payload.get("revision", 1)),
    )
    revision = len(previous_corrections) + 1
    fact = verdict_correction_fact(
        attempt=attempt,
        request_id=command.request_id,
        final_verdict=command.final_verdict,
        reason=command.reason,
        source_event_ts=_clock(request).now(),
        revision=revision,
        supersedes_id=(None if not previous_corrections else previous_corrections[-1].event_id),
    )
    corrected_attempts = project_assessment_attempts([*facts, fact])
    memory_record, difficulty_progress = rebuild_learning_state(
        corrected_attempts,
        item_id=attempt.item_id,
    )
    reconciliation = {
        "item_id": attempt.item_id,
        "learning_memory_state": (
            "not_in_memory" if memory_record is None else memory_record.state
        ),
        "difficulty_tier": int(difficulty_progress.tier),
        "through_event_id": fact.event_id,
    }
    fact = fact.model_copy(update={"payload": {**fact.payload, "reconciliation": reconciliation}})
    with persistence.transaction_owner.transaction():
        persistence.learning_facts.append(fact)
        persistence.memory.replace_record(attempt.item_id, memory_record)
        persistence.difficulty.replace_progress(
            attempt.item_id,
            difficulty_progress,
        )
    _publish_learning_outbox(request)
    corrected = next(item for item in corrected_attempts if item.attempt_id == attempt_id)
    return corrected


@router.post(
    "/attempts/{attempt_id}/demand-validations",
    response_model=DemandValidationV1,
    status_code=201,
)
async def validate_attempt_demand(
    attempt_id: str,
    command: DemandValidationRequest,
    request: Request,
) -> DemandValidationV1:
    persistence = _persistence(request)
    facts = persistence.learning_facts.facts()
    attempt = next(
        (item for item in project_assessment_attempts(facts) if item.attempt_id == attempt_id),
        None,
    )
    if attempt is None:
        raise ApiError(
            status_code=404,
            code="assessment_attempt_not_found",
            message=f"考核记录不存在：{attempt_id}",
        )
    event_id = derive_id(attempt.attempt_id, DEMAND_VALIDATED, command.request_id)
    existing_fact = next((fact for fact in facts if fact.event_id == event_id), None)
    if existing_fact is not None:
        if (
            existing_fact.payload.get("validated_demand") != command.validated_demand
            or existing_fact.payload.get("rationale") != command.rationale
            or existing_fact.payload.get("validator_kind") != "user"
        ):
            raise ApiError(
                status_code=409,
                code="idempotency_conflict",
                message="相同 request_id 已用于不同的认知要求验证",
            )
        _publish_learning_outbox(request)
        return next(
            validation
            for validation in project_demand_validations(facts)
            if validation.validation_id == event_id
        )
    previous_validations = project_demand_validations(facts)
    fact, validation = demand_validation_fact(
        attempt=attempt,
        request_id=command.request_id,
        validated_demand=command.validated_demand,
        validator_kind="user",
        validator_version="manual.v1",
        calibration_version=None,
        rationale=command.rationale,
        source_event_ts=_clock(request).now(),
        previous=previous_validations,
    )
    persistence.learning_facts.append(fact)
    _publish_learning_outbox(request)
    return validation


@router.post(
    "/items/{item_id}/classifications",
    response_model=KnowledgeClassificationV1,
    status_code=201,
)
async def classify_knowledge_item(
    item_id: str,
    command: KnowledgeClassificationRequest,
    request: Request,
) -> KnowledgeClassificationV1:
    persistence = _persistence(request)
    if all(item.item_id != item_id for item in persistence.store.all_items()):
        raise ApiError(
            status_code=404,
            code="knowledge_item_not_found",
            message=f"知识点不存在：{item_id}",
        )
    trace_id = derive_id("classification", item_id, command.request_id)
    try:
        result = persistence.classifications.classify_item(
            item_id=item_id,
            request_id=command.request_id,
            primary_kind=command.primary_kind,
            orientations=command.orientations,
            trace_id=trace_id,
            review_status=command.review_status,
        )
    except ClassificationIdempotencyConflict as exc:
        raise _idempotency_conflict(exc) from exc
    _publish_learning_outbox(request)
    return result


@router.post(
    "/items/{item_id}/classifications/{classification_id}/review",
    response_model=KnowledgeClassificationV1,
)
async def review_knowledge_classification(
    item_id: str,
    classification_id: str,
    command: ClassificationReviewRequest,
    request: Request,
) -> KnowledgeClassificationV1:
    repository = _persistence(request).classifications
    if all(
        item.classification_id != classification_id for item in repository.history_for_item(item_id)
    ):
        raise ApiError(
            status_code=404,
            code="knowledge_classification_not_found",
            message=f"知识分类不存在：{classification_id}",
        )
    try:
        reviewed = repository.review_item_classification(
            classification_id,
            command.review_status,
            request_id=command.request_id,
        )
    except ValueError as exc:
        raise ApiError(
            status_code=409,
            code="idempotency_conflict",
            message=str(exc),
        ) from exc
    if reviewed is None:
        raise ApiError(
            status_code=404,
            code="knowledge_classification_not_found",
            message=f"知识分类不存在：{classification_id}",
        )
    _publish_learning_outbox(request)
    return reviewed


@router.get(
    "/items/{item_id}/classifications",
    response_model=KnowledgeClassificationHistory,
)
async def get_knowledge_item_classifications(
    item_id: str,
    request: Request,
) -> KnowledgeClassificationHistory:
    repository = _persistence(request).classifications
    return KnowledgeClassificationHistory(
        active=repository.active_for_item(item_id),
        history=repository.history_for_item(item_id),
    )


@router.post(
    "/revisions/{revision_id}/classifications",
    response_model=ResourceRevisionClassificationV1,
    status_code=201,
)
async def classify_resource_revision(
    revision_id: str,
    command: ResourceRevisionClassificationRequest,
    request: Request,
) -> ResourceRevisionClassificationV1:
    persistence = _persistence(request)
    if persistence.store.get_revision(revision_id) is None:
        raise ApiError(
            status_code=404,
            code="resource_revision_not_found",
            message=f"资源修订不存在：{revision_id}",
        )
    try:
        result = persistence.classifications.classify_revision(
            revision_id=revision_id,
            request_id=command.request_id,
            source_genre=command.primary_source_genre,
            trace_id=derive_id("revision-classification", revision_id, command.request_id),
        )
    except ClassificationIdempotencyConflict as exc:
        raise _idempotency_conflict(exc) from exc
    _publish_learning_outbox(request)
    return result


@router.post(
    "/vocabulary/terms/{term_id:path}/review",
    response_model=VocabularyTermView,
)
async def review_vocabulary_term(
    term_id: str,
    command: VocabularyReviewRequest,
    request: Request,
) -> VocabularyTermView:
    try:
        term = _persistence(request).classifications.review_term(
            term_id,
            command.review_status,
            request_id=command.request_id,
            trace_id=derive_id("vocabulary-term-review", term_id, command.request_id),
            replacement_term_id=command.replacement_term_id,
        )
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="replacement_vocabulary_term_not_found",
            message=f"替代词不存在：{command.replacement_term_id}",
        ) from exc
    except ClassificationIdempotencyConflict as exc:
        raise _idempotency_conflict(exc) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=409,
            code="idempotency_conflict",
            message=str(exc),
        ) from exc
    if term is None:
        raise ApiError(
            status_code=404,
            code="vocabulary_term_not_found",
            message=f"受控词不存在：{term_id}",
        )
    _publish_learning_outbox(request)
    return term


@router.post(
    "/vocabulary/tag-candidates",
    response_model=TagCandidateV1,
    status_code=201,
)
async def propose_tag_candidate(
    command: TagCandidateRequest,
    request: Request,
) -> TagCandidateV1:
    try:
        candidate = _persistence(request).classifications.propose_tag_candidate(
            request_id=command.request_id,
            namespace=command.namespace,
            raw_value=command.raw_value,
            trace_id=derive_id("tag-candidate", command.namespace, command.request_id),
        )
    except ClassificationIdempotencyConflict as exc:
        raise _idempotency_conflict(exc) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=409,
            code="managed_term_already_exists",
            message=str(exc),
        ) from exc
    _publish_learning_outbox(request)
    return candidate


@router.post(
    "/vocabulary/tag-candidates/{candidate_id}/review",
    response_model=TagCandidateV1,
)
async def review_tag_candidate(
    candidate_id: str,
    command: ClassificationReviewRequest,
    request: Request,
) -> TagCandidateV1:
    try:
        candidate = _persistence(request).classifications.review_tag_candidate(
            candidate_id,
            command.review_status,
            request_id=command.request_id,
        )
    except ValueError as exc:
        raise ApiError(
            status_code=409,
            code="idempotency_conflict",
            message=str(exc),
        ) from exc
    if candidate is None:
        raise ApiError(
            status_code=404,
            code="tag_candidate_not_found",
            message=f"标签候选不存在：{candidate_id}",
        )
    _publish_learning_outbox(request)
    return candidate


@router.post(
    "/items/{item_id}/tags",
    response_model=TagAssignmentV1,
    status_code=201,
)
async def assign_managed_tag(
    item_id: str,
    command: TagAssignmentRequest,
    request: Request,
) -> TagAssignmentV1:
    persistence = _persistence(request)
    if all(item.item_id != item_id for item in persistence.store.all_items()):
        raise ApiError(
            status_code=404,
            code="knowledge_item_not_found",
            message=f"知识点不存在：{item_id}",
        )
    try:
        assignment = persistence.classifications.assign_tag(
            item_id=item_id,
            term_id=command.term_id,
            request_id=command.request_id,
            trace_id=derive_id("tag-assignment", item_id, command.request_id),
        )
        _publish_learning_outbox(request)
        return assignment
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="vocabulary_term_not_found",
            message=f"受控词不存在：{command.term_id}",
        ) from exc
    except ClassificationIdempotencyConflict as exc:
        raise _idempotency_conflict(exc) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=409,
            code="vocabulary_term_not_approved",
            message=str(exc),
        ) from exc


@router.get("/items/{item_id}/tags", response_model=TagAssignmentList)
async def list_managed_tags(
    item_id: str,
    request: Request,
) -> TagAssignmentList:
    return TagAssignmentList(items=_persistence(request).classifications.tags_for_item(item_id))

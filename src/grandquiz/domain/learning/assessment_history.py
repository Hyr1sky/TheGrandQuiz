"""Assessment fact builders and deterministic long-term learning projections."""

from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.difficulty import (
    DifficultyProgress,
    DirectCorrectEvidence,
    DischargeEvidence,
    MasterySignals,
    ResetEvidence,
    evolve_difficulty,
)
from grandquiz.domain.learning.learning_facts import (
    TAXONOMY_VERSION,
    LearningFactEnvelope,
)
from grandquiz.domain.learning.memory import ConceptRecord, apply_verdict
from grandquiz.domain.learning.models import derive_id
from grandquiz.kernel.events import AgentEvent

ASSESSMENT_JUDGEMENT_COMMITTED = "learning.assessment_judgement_committed"
VERDICT_CORRECTED = "learning.verdict_corrected"
DEMAND_VALIDATED = "learning.demand_validated"
ASSESSMENT_FACT_SCHEMA_VERSION = "assessment-judgement-committed.v1"
ASSESSMENT_ATTEMPT_SCHEMA_VERSION = "assessment-attempt.v1"


class SourceEventCursor(BaseModel):
    first_seq: int
    last_seq: int


class QuestionRoute(BaseModel):
    format: Literal["multiple_choice", "open_response"]
    strategy: Literal["standard", "probe"]


class GenerationProvenance(BaseModel):
    kind: Literal["rule", "model"]
    version: str


class GradingProvenance(BaseModel):
    kind: Literal["deterministic", "model"]
    version: str


CognitiveDemand = Literal[
    "recall",
    "explain",
    "compare",
    "apply",
    "diagnose",
    "evaluate",
    "design",
]


class DemandValidationV1(BaseModel):
    schema_version: Literal["demand-validation.v1"] = "demand-validation.v1"
    validation_id: str
    attempt_id: str
    revision: int = 1
    supersedes_id: str | None = None
    validated_demand: CognitiveDemand | None
    validator_kind: Literal["rule", "calibrated_judge", "user"]
    validator_version: str
    calibration_version: str | None = None
    rationale: str
    review_status: Literal["proposed", "approved", "rejected"]
    lifecycle_status: Literal["active", "superseded", "retracted"] = "active"
    trace_id: str


class AssessmentAttemptV1(BaseModel):
    """Minimum durable attempt projection; later slices add behavioral fidelity."""

    schema_version: Literal["assessment-attempt.v1"] = ASSESSMENT_ATTEMPT_SCHEMA_VERSION
    taxonomy_version: str
    attempt_id: str
    trace_id: str
    assessment_span_id: str
    item_id: str
    question_text: str
    answer_text: str
    initial_verdict: VerdictLabel
    final_verdict: VerdictLabel
    concept_state: Literal["薄弱", "观察中"] | None = None
    adaptive_route: QuestionRoute
    effective_route: QuestionRoute
    routing_source: Literal["adaptive", "user_override"]
    input_modality: Literal["text", "voice"]
    answer_format: Literal["choice", "natural_language", "code"]
    evidence_revealed_before_answer: bool = False
    elapsed_ms: int | None = None
    question_generation: GenerationProvenance
    grading: GradingProvenance
    difficulty_tier: int | None = None
    appeal_status: Literal["none", "pending", "upheld", "overturned"] = "none"
    supplemental_answer: str | None = None
    active_demand_validation_id: str | None = None
    source_event_cursor: SourceEventCursor


class LearnerProjectionV1(BaseModel):
    """Explainable item-level analytics rebuilt from durable attempts."""

    schema_version: Literal["learner-projection.v1"] = "learner-projection.v1"
    taxonomy_version: str = TAXONOMY_VERSION
    item_id: str
    attempt_count: int
    closed_book_attempt_count: int
    verdict_counts: dict[VerdictLabel, int]
    learning_memory_state: Literal["薄弱", "观察中", "not_in_memory"]
    difficulty_tier: int
    validated_demand_states: dict[CognitiveDemand, Literal["passed", "needs_work"]]


def assessment_fact(
    *,
    question_event: AgentEvent,
    judgement_event: AgentEvent,
    item_id: str,
    question_text: str,
    answer_text: str,
    verdict: VerdictLabel,
    adaptive_question_type: str,
    effective_question_type: str,
    routing_source: Literal["adaptive", "user_override"],
    input_modality: Literal["text", "voice"],
    answer_format: Literal["choice", "natural_language", "code"],
    evidence_revealed_before_answer: bool,
    elapsed_ms: int,
    question_generation_version: str,
    grading_kind: Literal["deterministic", "model"],
    grading_version: str,
) -> LearningFactEnvelope:
    """Create the redacted fact that crosses the judgement commit boundary."""

    assessment_span_id = question_event.parent_span_id
    if assessment_span_id is None:
        raise ValueError("question event must belong to an assessment span")
    attempt_id = f"{question_event.trace_id}:{assessment_span_id}"
    event_id = derive_id(
        question_event.trace_id,
        assessment_span_id,
        ASSESSMENT_JUDGEMENT_COMMITTED,
    )
    return LearningFactEnvelope(
        event_id=event_id,
        event_type=ASSESSMENT_JUDGEMENT_COMMITTED,
        entity_id=attempt_id,
        trace_id=question_event.trace_id,
        source_event_seq=judgement_event.seq,
        source_event_ts=judgement_event.ts,
        payload_schema_version=ASSESSMENT_FACT_SCHEMA_VERSION,
        payload={
            "attempt_id": attempt_id,
            "assessment_span_id": assessment_span_id,
            "question_event_seq": question_event.seq,
            "item_id": item_id,
            "question_text": question_text,
            "answer_text": answer_text,
            "initial_verdict": verdict,
            "adaptive_route": _project_question_route(adaptive_question_type),
            "effective_route": _project_question_route(effective_question_type),
            "routing_source": routing_source,
            "input_modality": input_modality,
            "answer_format": answer_format,
            "evidence_revealed_before_answer": evidence_revealed_before_answer,
            "elapsed_ms": elapsed_ms,
            "question_generation": {
                "kind": "model",
                "version": question_generation_version,
            },
            "grading": {
                "kind": grading_kind,
                "version": grading_version,
            },
        },
    )


def with_committed_state(
    fact: LearningFactEnvelope,
    *,
    concept_state: str | None,
    difficulty_tier: int | None,
) -> LearningFactEnvelope:
    """Return the immutable fact enriched with the state produced in its transaction."""

    return fact.model_copy(
        update={
            "payload": {
                **fact.payload,
                "concept_state": concept_state,
                "difficulty_tier": difficulty_tier,
            }
        }
    )


def project_assessment_attempts(
    facts: list[LearningFactEnvelope],
) -> list[AssessmentAttemptV1]:
    """Project committed judgement facts without consulting operational traces."""

    attempts: list[AssessmentAttemptV1] = []
    for fact in facts:
        if fact.event_type != ASSESSMENT_JUDGEMENT_COMMITTED:
            continue
        payload = fact.payload
        attempts.append(
            AssessmentAttemptV1(
                taxonomy_version=fact.taxonomy_version,
                attempt_id=str(payload["attempt_id"]),
                trace_id=fact.trace_id,
                assessment_span_id=str(payload["assessment_span_id"]),
                item_id=str(payload["item_id"]),
                question_text=str(payload["question_text"]),
                answer_text=str(payload["answer_text"]),
                initial_verdict=cast("VerdictLabel", payload["initial_verdict"]),
                final_verdict=cast("VerdictLabel", payload["initial_verdict"]),
                concept_state=cast(
                    "Literal['薄弱', '观察中'] | None",
                    payload.get("concept_state"),
                ),
                adaptive_route=QuestionRoute.model_validate(payload["adaptive_route"]),
                effective_route=QuestionRoute.model_validate(payload["effective_route"]),
                routing_source=cast(
                    "Literal['adaptive', 'user_override']", payload["routing_source"]
                ),
                input_modality=cast("Literal['text', 'voice']", payload["input_modality"]),
                answer_format=cast(
                    "Literal['choice', 'natural_language', 'code']",
                    payload["answer_format"],
                ),
                evidence_revealed_before_answer=bool(payload["evidence_revealed_before_answer"]),
                elapsed_ms=int(payload["elapsed_ms"]),
                question_generation=GenerationProvenance.model_validate(
                    payload["question_generation"]
                ),
                grading=GradingProvenance.model_validate(payload["grading"]),
                difficulty_tier=(
                    None
                    if payload.get("difficulty_tier") is None
                    else int(payload["difficulty_tier"])
                ),
                source_event_cursor=SourceEventCursor(
                    first_seq=int(payload.get("question_event_seq", fact.source_event_seq)),
                    last_seq=fact.source_event_seq,
                ),
            )
        )
    by_id = {attempt.attempt_id: attempt for attempt in attempts}
    corrections = sorted(
        (fact for fact in facts if fact.event_type == VERDICT_CORRECTED),
        key=lambda fact: (
            str(fact.payload.get("attempt_id")),
            int(fact.payload.get("revision", 1)),
            fact.event_id,
        ),
    )
    for fact in corrections:
        attempt = by_id.get(str(fact.payload.get("attempt_id")))
        if attempt is None:
            continue
        final_verdict = cast("VerdictLabel", fact.payload["final_verdict"])
        reconciliation = fact.payload.get("reconciliation")
        reconciled_state: Literal["薄弱", "观察中"] | None = attempt.concept_state
        reconciled_tier: int | None = attempt.difficulty_tier
        if isinstance(reconciliation, Mapping):
            projected = cast("Mapping[str, object]", reconciliation)
            state = projected.get("learning_memory_state")
            if state == "not_in_memory":
                reconciled_state = None
            elif state in {"薄弱", "观察中"}:
                reconciled_state = cast("Literal['薄弱', '观察中']", state)
            tier = projected.get("difficulty_tier")
            if isinstance(tier, int):
                reconciled_tier = tier
        updated = attempt.model_copy(
            update={
                "final_verdict": final_verdict,
                "appeal_status": (
                    "overturned" if final_verdict != attempt.initial_verdict else "upheld"
                ),
                "supplemental_answer": fact.payload.get("supplemental_answer"),
                "concept_state": reconciled_state,
                "difficulty_tier": reconciled_tier,
            }
        )
        by_id[attempt.attempt_id] = updated
    for validation in project_demand_validations(facts):
        if validation.review_status != "approved" or validation.lifecycle_status != "active":
            continue
        attempt = by_id.get(validation.attempt_id)
        if attempt is not None:
            by_id[attempt.attempt_id] = attempt.model_copy(
                update={"active_demand_validation_id": validation.validation_id}
            )
    attempts = [by_id[attempt.attempt_id] for attempt in attempts]
    return attempts


def verdict_correction_fact(
    *,
    attempt: AssessmentAttemptV1,
    request_id: str,
    final_verdict: VerdictLabel,
    reason: str,
    source_event_ts: float,
    revision: int = 1,
    supersedes_id: str | None = None,
    reconciliation: Mapping[str, Any] | None = None,
    supplemental_answer: str | None = None,
) -> LearningFactEnvelope:
    """Build an idempotent append-only correction fact."""

    event_id = derive_id(attempt.attempt_id, VERDICT_CORRECTED, request_id)
    return LearningFactEnvelope(
        event_id=event_id,
        event_type=VERDICT_CORRECTED,
        entity_id=attempt.attempt_id,
        trace_id=derive_id("verdict-correction", attempt.attempt_id, request_id),
        source_event_seq=revision,
        source_event_ts=source_event_ts,
        payload_schema_version="verdict-correction.v1",
        payload={
            "attempt_id": attempt.attempt_id,
            "item_id": attempt.item_id,
            "revision": revision,
            "supersedes_id": supersedes_id,
            "from_verdict": attempt.final_verdict,
            "final_verdict": final_verdict,
            "reason": reason,
            "request_id": request_id,
            "reconciliation": reconciliation,
            "supplemental_answer": supplemental_answer,
        },
    )


def demand_validation_fact(
    *,
    attempt: AssessmentAttemptV1,
    request_id: str,
    validated_demand: CognitiveDemand | None,
    validator_kind: Literal["rule", "calibrated_judge", "user"],
    validator_version: str,
    calibration_version: str | None,
    rationale: str,
    source_event_ts: float,
    previous: list[DemandValidationV1] | None = None,
) -> tuple[LearningFactEnvelope, DemandValidationV1]:
    """Build a revisioned validation; uncalibrated judges remain proposed."""

    active = max(
        (
            validation
            for validation in (previous or [])
            if validation.attempt_id == attempt.attempt_id
            and validation.lifecycle_status == "active"
        ),
        key=lambda validation: validation.revision,
        default=None,
    )
    revision = 1 if active is None else active.revision + 1
    validation_id = derive_id(attempt.attempt_id, DEMAND_VALIDATED, request_id)
    review_status: Literal["proposed", "approved", "rejected"] = (
        "approved"
        if validator_kind == "user"
        or (validator_kind == "calibrated_judge" and calibration_version is not None)
        else "proposed"
    )
    trace_id = derive_id("demand-validation", attempt.attempt_id, request_id)
    validation = DemandValidationV1(
        validation_id=validation_id,
        attempt_id=attempt.attempt_id,
        validated_demand=validated_demand,
        validator_kind=validator_kind,
        validator_version=validator_version,
        calibration_version=calibration_version,
        rationale=rationale,
        review_status=review_status,
        revision=revision,
        supersedes_id=None if active is None else active.validation_id,
        trace_id=trace_id,
    )
    fact = LearningFactEnvelope(
        event_id=validation_id,
        event_type=DEMAND_VALIDATED,
        entity_id=attempt.attempt_id,
        trace_id=trace_id,
        source_event_seq=revision,
        source_event_ts=source_event_ts,
        payload_schema_version="demand-validation.v1",
        payload=validation.model_dump(mode="json"),
    )
    return fact, validation


def project_demand_validations(
    facts: list[LearningFactEnvelope],
) -> list[DemandValidationV1]:
    validations = [
        DemandValidationV1.model_validate(fact.payload)
        for fact in facts
        if fact.event_type == DEMAND_VALIDATED
    ]
    by_attempt: dict[str, list[DemandValidationV1]] = {}
    for validation in validations:
        by_attempt.setdefault(validation.attempt_id, []).append(validation)
    projected: list[DemandValidationV1] = []
    for attempt_validations in by_attempt.values():
        ordered = sorted(
            attempt_validations,
            key=lambda validation: (validation.revision, validation.validation_id),
        )
        latest = ordered[-1]
        latest_approved = next(
            (
                validation
                for validation in reversed(ordered)
                if validation.review_status == "approved"
            ),
            None,
        )
        for validation in ordered:
            if validation.review_status == "rejected":
                lifecycle_status: Literal["active", "superseded", "retracted"] = "retracted"
            elif validation.review_status == "approved":
                lifecycle_status = (
                    "active"
                    if latest_approved is not None
                    and validation.validation_id == latest_approved.validation_id
                    else "superseded"
                )
            else:
                lifecycle_status = (
                    "active" if validation.validation_id == latest.validation_id else "superseded"
                )
            projected.append(validation.model_copy(update={"lifecycle_status": lifecycle_status}))
    return projected


def rebuild_learning_state(
    attempts: list[AssessmentAttemptV1],
    *,
    item_id: str,
) -> tuple[ConceptRecord | None, DifficultyProgress]:
    """Replay final verdicts from empty state using the production transition rules."""

    record: ConceptRecord | None = None
    progress = DifficultyProgress()
    for attempt in (item for item in attempts if item.item_id == item_id):
        before = record
        record = apply_verdict(before, attempt.final_verdict, item_id=item_id)
        if before is not None and record is None and attempt.final_verdict == "对":
            evidence = DischargeEvidence(
                signals=MasterySignals(
                    rounds_to_discharge=len(before.verdict_history),
                    elapsed_ms=attempt.elapsed_ms,
                    had_struggle="勉强" in before.verdict_history,
                )
            )
        elif attempt.final_verdict == "对" and before is None:
            evidence = DirectCorrectEvidence()
        else:
            evidence = ResetEvidence()
        progress = evolve_difficulty(progress, evidence)
    return record, progress


def project_learner(
    attempts: list[AssessmentAttemptV1],
    *,
    item_id: str,
    demand_validations: list[DemandValidationV1] | None = None,
) -> LearnerProjectionV1 | None:
    """Build one item projection; no attempts means the item has no learning history."""

    selected = [attempt for attempt in attempts if attempt.item_id == item_id]
    if not selected:
        return None
    counts: dict[VerdictLabel, int] = {"对": 0, "勉强": 0, "错": 0}
    for attempt in selected:
        counts[attempt.final_verdict] += 1
    record, progress = rebuild_learning_state(selected, item_id=item_id)
    state = record.state if record is not None else "not_in_memory"
    validations_by_id = {
        validation.validation_id: validation for validation in demand_validations or []
    }
    demand_states: dict[CognitiveDemand, Literal["passed", "needs_work"]] = {}
    for attempt in selected:
        validation = validations_by_id.get(attempt.active_demand_validation_id or "")
        if (
            validation is None
            or validation.review_status != "approved"
            or validation.validated_demand is None
        ):
            continue
        demand_states[validation.validated_demand] = (
            "passed" if attempt.final_verdict == "对" else "needs_work"
        )
    return LearnerProjectionV1(
        item_id=item_id,
        attempt_count=len(selected),
        closed_book_attempt_count=sum(
            not attempt.evidence_revealed_before_answer for attempt in selected
        ),
        verdict_counts=counts,
        learning_memory_state=state,
        difficulty_tier=progress.tier,
        validated_demand_states=demand_states,
    )


def _project_question_route(question_type: str) -> dict[str, str]:
    if question_type == "选择题":
        return {"format": "multiple_choice", "strategy": "standard"}
    if question_type == "追问":
        return {"format": "open_response", "strategy": "probe"}
    return {"format": "open_response", "strategy": "standard"}

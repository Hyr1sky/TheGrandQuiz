"""Deterministic local Eval candidates projected from explicit verdict corrections."""

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment_history import (
    ASSESSMENT_JUDGEMENT_COMMITTED,
    VERDICT_CORRECTED,
    project_assessment_attempts,
)
from grandquiz.domain.learning.learning_facts import (
    DEFAULT_REDACTION_PROFILE,
    LearningFactEnvelope,
)


class GradingEvalCandidateV1(BaseModel):
    """Minimal local supervision record; promotion to a repo fixture is always manual."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["grading-eval-candidate.v1"] = "grading-eval-candidate.v1"
    candidate_id: str
    attempt_id: str
    item_id: str
    source_trace_id: str
    correction_trace_id: str
    question_text: str
    answer_text: str
    question_format: Literal["multiple_choice", "open_response"]
    grading_version: str
    model_verdict: VerdictLabel
    human_verdict: VerdictLabel
    correction_reason: str
    label_kind: Literal["upheld", "overturned"]
    blind_to_model_output: Literal[False] = False
    release_gate_eligible: Literal[False] = False
    privacy_review_required: Literal[True] = True
    redaction_profile: str = DEFAULT_REDACTION_PROFILE


def project_grading_eval_candidates(
    facts: list[LearningFactEnvelope],
) -> list[GradingEvalCandidateV1]:
    """Project the latest correction for each attempt; never mutate or duplicate journal truth."""

    base_facts = [fact for fact in facts if fact.event_type == ASSESSMENT_JUDGEMENT_COMMITTED]
    attempts = {attempt.attempt_id: attempt for attempt in project_assessment_attempts(base_facts)}
    latest: dict[str, LearningFactEnvelope] = {}
    corrections = sorted(
        (fact for fact in facts if fact.event_type == VERDICT_CORRECTED),
        key=lambda fact: (
            str(fact.payload.get("attempt_id")),
            int(fact.payload.get("revision", 1)),
            fact.event_id,
        ),
    )
    for correction in corrections:
        latest[str(correction.payload.get("attempt_id"))] = correction

    candidates: list[GradingEvalCandidateV1] = []
    for attempt_id in sorted(latest):
        attempt = attempts.get(attempt_id)
        if attempt is None:
            continue
        correction = latest[attempt_id]
        human_verdict = cast("VerdictLabel", correction.payload["final_verdict"])
        candidates.append(
            GradingEvalCandidateV1(
                candidate_id=correction.event_id,
                attempt_id=attempt.attempt_id,
                item_id=attempt.item_id,
                source_trace_id=attempt.trace_id,
                correction_trace_id=correction.trace_id,
                question_text=attempt.question_text,
                answer_text=attempt.answer_text,
                question_format=attempt.effective_route.format,
                grading_version=attempt.grading.version,
                model_verdict=attempt.initial_verdict,
                human_verdict=human_verdict,
                correction_reason=str(correction.payload["reason"]),
                label_kind=("upheld" if human_verdict == attempt.initial_verdict else "overturned"),
            )
        )
    return candidates

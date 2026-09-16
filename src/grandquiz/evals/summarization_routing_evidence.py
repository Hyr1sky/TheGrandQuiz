"""Turn approved summarization comparisons into provider-neutral routing evidence.

This boundary is deliberately offline.  It accepts frozen generation and judging
artifacts, verifies their provenance, and exposes only pre-call request facts to
the generic routing evaluator.
"""

from __future__ import annotations

import hashlib
import json

from grandquiz.evals.routing import (
    RoutingCandidateOutcome,
    RoutingCase,
    RoutingDataset,
    RoutingRequest,
)
from grandquiz.evals.summarization_pairwise import (
    SummarizationCriterionScores,
    SummarizationJudgeCase,
    SummarizationJudgedCase,
    SummarizationJudgementCollection,
    SummarizationJudgePlan,
    SummarizationReviewApproval,
    SummarizationReviewPack,
    build_summarization_review_pack,
)
from grandquiz.evals.summarization_routing import (
    SummarizationPilotCollection,
    SummarizationPilotMessage,
    SummarizationPilotOutcome,
    SummarizationPilotPlan,
)


class SummarizationRoutingEvidenceError(ValueError):
    """Frozen artifacts cannot form trustworthy paired routing evidence."""


class SummarizationRoutingEvidenceApprovalRequired(PermissionError):
    """The exact blind review pack has not received an explicit Yes decision."""


def materialize_summarization_routing_dataset(
    pilot: SummarizationPilotPlan,
    collection: SummarizationPilotCollection,
    judge_plan: SummarizationJudgePlan,
    judgements: SummarizationJudgementCollection,
    review_pack: SummarizationReviewPack,
    *,
    approval: SummarizationReviewApproval | None,
) -> RoutingDataset:
    """Build development-only routing evidence after verifying the full chain."""

    approved_review = _require_approval(review_pack, approval)
    _verify_artifact_chain(pilot, collection, judge_plan, judgements, review_pack)

    pilot_by_case = {case.case_id: case for case in pilot.cases}
    collection_by_case = {case.case_id: case for case in collection.cases}
    judged_by_case = {case.case_id: case for case in judgements.cases}
    candidate_ids = tuple(sorted(pilot.candidate_profile_ids))
    cases: list[RoutingCase] = []
    for judged_plan_case in judge_plan.cases:
        pilot_case = pilot_by_case[judged_plan_case.case_id]
        collected_case = collection_by_case[judged_plan_case.case_id]
        judged_case = judged_by_case[judged_plan_case.case_id]
        scores = _quality_scores(judged_plan_case, judged_case)
        collected_outcomes = {
            outcome.candidate_profile_id: outcome for outcome in collected_case.outcomes
        }
        outcomes = tuple(
            _routing_outcome(collected_outcomes[candidate_id], scores[candidate_id])
            for candidate_id in candidate_ids
        )
        cases.append(
            RoutingCase(
                request=RoutingRequest(
                    case_id=pilot_case.case_id,
                    source_group_id=pilot_case.source_group_id,
                    partition="development",
                    input_text="\n".join(
                        f"{message.role}：{message.content}" for message in pilot_case.messages
                    ),
                    features=_request_features(pilot_case.messages),
                ),
                outcomes=outcomes,
            )
        )

    approval_revision = _digest(approved_review.model_dump(mode="json"))
    return RoutingDataset(
        source_kind="summarization-paired-pilot.v1",
        source_revisions=tuple(
            sorted(
                {
                    pilot.content_sha256,
                    collection.content_sha256,
                    judge_plan.content_sha256,
                    judgements.content_sha256,
                    review_pack.content_sha256,
                    approval_revision,
                }
            )
        ),
        cost_unit="unknown",
        candidate_ids=candidate_ids,
        cases=tuple(sorted(cases, key=lambda case: case.request.case_id)),
    )


def _require_approval(
    review_pack: SummarizationReviewPack,
    approval: SummarizationReviewApproval | None,
) -> SummarizationReviewApproval:
    if (
        approval is None
        or approval.approved is not True
        or approval.review_pack_content_sha256 != review_pack.content_sha256
    ):
        raise SummarizationRoutingEvidenceApprovalRequired(
            "the exact summarization review pack requires an explicit Yes approval"
        )
    return approval


def _verify_artifact_chain(
    pilot: SummarizationPilotPlan,
    collection: SummarizationPilotCollection,
    judge_plan: SummarizationJudgePlan,
    judgements: SummarizationJudgementCollection,
    review_pack: SummarizationReviewPack,
) -> None:
    if collection.plan_content_sha256 != pilot.content_sha256:
        raise SummarizationRoutingEvidenceError("collection does not belong to the pilot")
    if (
        judge_plan.pilot_plan_content_sha256 != pilot.content_sha256
        or judge_plan.collection_content_sha256 != collection.content_sha256
    ):
        raise SummarizationRoutingEvidenceError("judge plan does not belong to the pilot")
    if _judge_plan_digest(judge_plan) != judge_plan.content_sha256:
        raise SummarizationRoutingEvidenceError("judge plan content digest does not match")
    expected_development_ids = {
        case.case_id for case in pilot.cases if case.partition == "development"
    }
    if {case.case_id for case in judge_plan.cases} != expected_development_ids:
        raise SummarizationRoutingEvidenceError(
            "judge plan does not cover exactly the development partition"
        )
    if judgements.plan_content_sha256 != judge_plan.content_sha256:
        raise SummarizationRoutingEvidenceError("judgements do not belong to the judge plan")
    if _judgements_digest(judgements) != judgements.content_sha256:
        raise SummarizationRoutingEvidenceError("judgement content digest does not match")
    if (
        review_pack.judge_plan_content_sha256 != judge_plan.content_sha256
        or review_pack.judgements_content_sha256 != judgements.content_sha256
    ):
        raise SummarizationRoutingEvidenceError("review pack does not belong to the judgements")
    expected_pack = build_summarization_review_pack(
        judge_plan,
        judgements,
        audit_sample_size=review_pack.audit_count,
    )
    if expected_pack != review_pack:
        raise SummarizationRoutingEvidenceError("review pack content is not reproducible")


def _quality_scores(
    plan_case: SummarizationJudgeCase,
    judged_case: SummarizationJudgedCase,
) -> dict[str, float]:
    assignments = plan_case.assignments
    candidate_ids = tuple(output.candidate_profile_id for output in plan_case.outputs)
    totals = {candidate_id: 0 for candidate_id in candidate_ids}
    for assignment, judgement in zip(assignments, judged_case.judgements, strict=True):
        if (
            judgement.execution_status != "completed"
            or judgement.a_scores is None
            or judgement.b_scores is None
        ):
            raise SummarizationRoutingEvidenceError(
                "routing quality requires two valid structured judgements"
            )
        totals[assignment.candidate_a_profile_id] += _score_total(judgement.a_scores)
        totals[assignment.candidate_b_profile_id] += _score_total(judgement.b_scores)
    return {candidate_id: round((total - 8) / 24, 12) for candidate_id, total in totals.items()}


def _routing_outcome(
    outcome: SummarizationPilotOutcome,
    quality_score: float,
) -> RoutingCandidateOutcome:
    if outcome.execution_status != "completed":
        raise SummarizationRoutingEvidenceError(
            "judged routing evidence must reference completed paired outcomes"
        )
    return RoutingCandidateOutcome(
        candidate_id=outcome.candidate_profile_id,
        execution_status="completed",
        quality_score=quality_score,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        latency_ms=outcome.latency_ms,
    )


def _score_total(scores: SummarizationCriterionScores) -> int:
    return (
        scores.factual_fidelity
        + scores.useful_retention
        + scores.compression_quality
        + scores.continuation_usefulness
    )


def _request_features(
    messages: tuple[SummarizationPilotMessage, ...],
) -> tuple[tuple[str, int], ...]:
    return (
        (
            "assistant_chars",
            sum(len(message.content) for message in messages if message.role == "assistant"),
        ),
        ("message_count", len(messages)),
        ("source_utf8_bytes", sum(len(message.content.encode("utf-8")) for message in messages)),
        ("turn_count", sum(message.role == "user" for message in messages)),
        ("user_chars", sum(len(message.content) for message in messages if message.role == "user")),
    )


def _judgements_digest(judgements: SummarizationJudgementCollection) -> str:
    return _digest(
        {
            "schema_version": judgements.schema_version,
            "plan_content_sha256": judgements.plan_content_sha256,
            "known_actual_tokens": judgements.known_actual_tokens,
            "unknown_usage_count": judgements.unknown_usage_count,
            "cases": [case.model_dump(mode="json") for case in judgements.cases],
        }
    )


def _judge_plan_digest(plan: SummarizationJudgePlan) -> str:
    return _digest(
        {
            "schema_version": plan.schema_version,
            "consumer": plan.consumer,
            "partition": plan.partition,
            "pilot_plan_content_sha256": plan.pilot_plan_content_sha256,
            "collection_content_sha256": plan.collection_content_sha256,
            "judges": [judge.model_dump(mode="json") for judge in plan.judges],
            "experiment_token_cap": plan.experiment_token_cap,
            "prior_actual_tokens": plan.prior_actual_tokens,
            "reserved_tokens": plan.reserved_tokens,
            "max_attempts_per_judge": plan.max_attempts_per_judge,
            "prompt_version": plan.prompt_version,
            "rubric_version": plan.rubric_version,
            "cases": [case.model_dump(mode="json") for case in plan.cases],
        }
    )


def _digest(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

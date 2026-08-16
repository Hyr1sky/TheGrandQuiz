"""Pre-registered Development Gold gate for a narrow grading candidate."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.evals.grading_calibration import (
    GradingCalibrationReport,
    GradingCalibrationResult,
)
from grandquiz.evals.subject import EvalSubjectSnapshot, SubjectEvaluation

AllowedSubjectChange = Literal["prompts", "policies"]
CandidateStatus = Literal["eligible_for_holdout", "rejected"]


class GradingEntailmentPlan(BaseModel):
    """Frozen intent and success gates written before candidate execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["grading-entailment-plan.v1"] = "grading-entailment-plan.v1"
    candidate_id: str = Field(min_length=1)
    evidence_class: Literal["development_gold"] = "development_gold"
    baseline_subject_id: str = Field(min_length=64, max_length=64)
    candidate_subject_id: str = Field(min_length=64, max_length=64)
    dataset_snapshot_id: str = Field(min_length=1)
    dataset_content_sha256: str = Field(min_length=1)
    metric_version: str = Field(min_length=1)
    allowed_changes: tuple[AllowedSubjectChange, ...] = Field(min_length=1)
    failure_sample_ids: tuple[str, ...] = Field(min_length=1)
    protected_control_sample_ids: tuple[str, ...] = Field(min_length=1)
    min_repaired_false_negatives: int = Field(default=4, ge=1)
    max_new_point_errors: int = Field(default=0, ge=0)
    max_new_false_positives: int = Field(default=0, ge=0)
    min_valid_output_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    max_token_ratio: float = Field(default=1.15, ge=1.0)


class GradingEntailmentDecision(BaseModel):
    """Development evidence only; this artifact can never activate production."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["grading-entailment-decision.v1"] = "grading-entailment-decision.v1"
    candidate_id: str
    status: CandidateStatus
    evidence_class: Literal["development_gold"] = "development_gold"
    repaired_false_negatives: int = Field(ge=0)
    remaining_false_negatives: int = Field(ge=0)
    new_point_errors: int = Field(ge=0)
    new_false_positives: int = Field(ge=0)
    valid_output_rate: float = Field(ge=0.0, le=1.0)
    token_ratio: float = Field(ge=0.0)
    promotion_eligible: Literal[False] = False


def _subject_changes(
    baseline: EvalSubjectSnapshot,
    candidate: EvalSubjectSnapshot,
) -> set[AllowedSubjectChange]:
    if baseline.providers != candidate.providers:
        raise ValueError("grading candidate must keep Provider/model/thinking fixed")
    if baseline.tool_schemas != candidate.tool_schemas:
        raise ValueError("grading candidate must keep tool schemas fixed")
    changes: set[AllowedSubjectChange] = set()
    if baseline.prompts != candidate.prompts:
        changes.add("prompts")
    if baseline.policies != candidate.policies:
        changes.add("policies")
    return changes


def _indexed_results(
    report: GradingCalibrationReport,
) -> dict[str, GradingCalibrationResult]:
    results = {result.sample_id: result for result in report.results}
    if len(results) != len(report.results):
        raise ValueError("grading candidate reports require unique sample ids")
    return results


def _point_errors(result: GradingCalibrationResult) -> set[str]:
    human_matched = set(result.human_matched_points)
    model_matched = set(result.model_matched_points)
    point_ids = human_matched | set(result.human_missing_points)
    return {
        point_id
        for point_id in point_ids
        if (point_id in human_matched) != (point_id in model_matched)
    }


def _point_false_positives(result: GradingCalibrationResult) -> set[str]:
    return set(result.human_missing_points) & set(result.model_matched_points)


def evaluate_grading_entailment_candidate(
    plan: GradingEntailmentPlan,
    *,
    baseline: SubjectEvaluation[GradingCalibrationReport],
    candidate: SubjectEvaluation[GradingCalibrationReport],
) -> GradingEntailmentDecision:
    """Compare one fixed candidate without mutating prompts, Providers, or datasets."""

    if baseline.subject.subject_id != plan.baseline_subject_id:
        raise ValueError("baseline subject does not match pre-registration")
    if candidate.subject.subject_id != plan.candidate_subject_id:
        raise ValueError("candidate subject does not match pre-registration")
    changes = _subject_changes(baseline.subject, candidate.subject)
    if not changes or not changes.issubset(set(plan.allowed_changes)):
        raise ValueError("candidate subject changes exceed pre-registered scope")

    baseline_run = baseline.report.run_manifest
    candidate_run = candidate.report.run_manifest
    if (
        dict(baseline.subject.prompts).get("answer_grade") != baseline_run.prompt_version
        or dict(candidate.subject.prompts).get("answer_grade") != candidate_run.prompt_version
    ):
        raise ValueError("report prompt version does not match subject")
    cohort = (
        plan.dataset_snapshot_id,
        plan.dataset_content_sha256,
        baseline_run.sample_ids,
    )
    if (
        baseline_run.dataset_snapshot_id,
        baseline_run.dataset_content_sha256,
        baseline_run.sample_ids,
    ) != cohort or (
        candidate_run.dataset_snapshot_id,
        candidate_run.dataset_content_sha256,
        candidate_run.sample_ids,
    ) != cohort:
        raise ValueError("baseline and candidate must use the pre-registered fixed cohort")
    if (
        baseline_run.provider,
        baseline_run.model,
        baseline_run.thinking_mode,
        baseline_run.reasoning_effort,
    ) != (
        candidate_run.provider,
        candidate_run.model,
        candidate_run.thinking_mode,
        candidate_run.reasoning_effort,
    ):
        raise ValueError("baseline and candidate must keep Provider/model/thinking fixed")
    if (
        dict(baseline.subject.policies).get("grading-metric") != plan.metric_version
        or dict(candidate.subject.policies).get("grading-metric") != plan.metric_version
    ):
        raise ValueError("subject metric version does not match pre-registration")

    baseline_results = _indexed_results(baseline.report)
    candidate_results = _indexed_results(candidate.report)
    if set(baseline_results) != set(candidate_results):
        raise ValueError("baseline and candidate report samples must be paired exactly")
    failure_ids = set(plan.failure_sample_ids)
    control_ids = set(plan.protected_control_sample_ids)
    if failure_ids & control_ids:
        raise ValueError("failure and protected-control slices must be disjoint")
    if not failure_ids | control_ids <= set(baseline_results):
        raise ValueError("pre-registered slices reference missing report samples")
    if any(baseline_results[sample_id].verdict_agreed for sample_id in failure_ids):
        raise ValueError("failure slice must contain baseline verdict disagreements")

    repaired = sum(candidate_results[sample_id].verdict_agreed for sample_id in failure_ids)
    remaining = len(failure_ids) - repaired
    new_point_errors = sum(
        len(
            _point_errors(candidate_results[sample_id]) - _point_errors(baseline_results[sample_id])
        )
        for sample_id in control_ids
    )
    new_false_positives = sum(
        len(
            _point_false_positives(candidate_results[sample_id])
            - _point_false_positives(baseline_results[sample_id])
        )
        for sample_id in control_ids
    )
    eligible_results = [result for result in candidate.report.results if result.eligible]
    valid_output_rate = (
        sum(result.output_valid is True for result in eligible_results) / len(eligible_results)
        if eligible_results
        else 0.0
    )
    baseline_tokens = baseline.report.eligible_total_tokens
    token_ratio = (
        candidate.report.eligible_total_tokens / baseline_tokens
        if baseline_tokens > 0
        else float("inf")
    )
    passed = (
        repaired >= plan.min_repaired_false_negatives
        and new_point_errors <= plan.max_new_point_errors
        and new_false_positives <= plan.max_new_false_positives
        and valid_output_rate >= plan.min_valid_output_rate
        and token_ratio <= plan.max_token_ratio
    )
    return GradingEntailmentDecision(
        candidate_id=plan.candidate_id,
        status="eligible_for_holdout" if passed else "rejected",
        repaired_false_negatives=repaired,
        remaining_false_negatives=remaining,
        new_point_errors=new_point_errors,
        new_false_positives=new_false_positives,
        valid_output_rate=valid_output_rate,
        token_ratio=token_ratio,
    )

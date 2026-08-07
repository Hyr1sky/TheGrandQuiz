"""Fixed-cohort comparison for production-grader calibration reports.

The report remains the audit source of truth.  This module only projects comparable
quality, cost, and latency metrics and fails closed if any dataset or prompt identity
drifts between conditions.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.evals.grading_calibration import GradingCalibrationReport


class GradingExperimentCondition(BaseModel):
    model_config = ConfigDict(frozen=True)

    condition_id: str
    provider: str
    model: str
    thinking_mode: str
    reasoning_effort: str | None
    derived_verdict_agreement: float = Field(ge=0.0, le=1.0)
    raw_model_verdict_agreement: float = Field(ge=0.0, le=1.0)
    point_accuracy: float = Field(ge=0.0, le=1.0)
    point_partition_exact_rate: float = Field(ge=0.0, le=1.0)
    valid_output_rate: float = Field(ge=0.0, le=1.0)
    serious_false_negative_count: int = Field(ge=0)
    serious_false_positive_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    total_prompt_tokens: int = Field(ge=0)
    total_completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    average_latency_ms: float = Field(ge=0.0)


class GradingExperimentComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["grading-calibration-comparison.v1"] = (
        "grading-calibration-comparison.v1"
    )
    dataset_snapshot_id: str
    dataset_content_sha256: str
    sample_ids: tuple[str, ...]
    prompt_version: str
    conditions: list[GradingExperimentCondition]


def _cohort_identity(report: GradingCalibrationReport) -> tuple[object, ...]:
    manifest = report.run_manifest
    return (
        manifest.dataset_snapshot_id,
        manifest.dataset_content_sha256,
        manifest.sample_ids,
        manifest.prompt_version,
    )


def compare_grading_reports(
    reports: list[GradingCalibrationReport],
) -> GradingExperimentComparison:
    """Compare reports only when snapshot, sample set, and prompt are identical."""

    if not reports:
        raise ValueError("at least one grading report is required")
    for report in reports:
        result_ids = tuple(sorted(result.sample_id for result in report.results))
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("grading report result sample IDs must be unique")
        if result_ids != report.run_manifest.sample_ids:
            raise ValueError("grading report results must exactly match manifest sample IDs")
    reference = reports[0]
    identity = _cohort_identity(reference)
    if any(_cohort_identity(report) != identity for report in reports[1:]):
        raise ValueError("all grading reports must use the same fixed cohort and prompt")
    manifest = reference.run_manifest
    if (
        manifest.dataset_snapshot_id is None
        or manifest.dataset_content_sha256 is None
        or not manifest.sample_ids
    ):
        raise ValueError("comparison requires an identified snapshot and non-empty sample set")

    conditions: list[GradingExperimentCondition] = []
    seen_ids: set[str] = set()
    for report in reports:
        run = report.run_manifest
        condition_id = (
            f"{run.provider}:{run.model}|thinking={run.thinking_mode}|"
            f"effort={run.reasoning_effort or 'none'}"
        )
        if condition_id in seen_ids:
            raise ValueError(f"duplicate experiment condition: {condition_id}")
        seen_ids.add(condition_id)
        sample_count = len(report.results)
        raw_agreement = (
            sum(result.model_verdict == result.human_verdict for result in report.results)
            / sample_count
            if sample_count
            else 0.0
        )
        exact_rate = (
            sum(
                result.error is None and result.point_correct_count == result.point_count
                for result in report.results
            )
            / sample_count
            if sample_count
            else 0.0
        )
        average_latency = (
            sum(result.latency_ms for result in report.results) / sample_count
            if sample_count
            else 0.0
        )
        conditions.append(
            GradingExperimentCondition(
                condition_id=condition_id,
                provider=run.provider,
                model=run.model,
                thinking_mode=run.thinking_mode,
                reasoning_effort=run.reasoning_effort,
                derived_verdict_agreement=report.verdict_agreement,
                raw_model_verdict_agreement=raw_agreement,
                point_accuracy=report.point_accuracy,
                point_partition_exact_rate=exact_rate,
                valid_output_rate=(
                    report.eligible_valid_output_rate
                    if report.eligible_valid_output_rate is not None
                    else (
                        sum(result.error is None for result in report.results) / sample_count
                        if sample_count
                        else 0.0
                    )
                ),
                serious_false_negative_count=report.serious_false_negative_count,
                serious_false_positive_count=report.serious_false_positive_count,
                error_count=sum(result.error is not None for result in report.results),
                retry_count=sum(result.retries for result in report.results),
                total_prompt_tokens=report.total_prompt_tokens,
                total_completion_tokens=report.total_completion_tokens,
                total_tokens=report.total_tokens,
                average_latency_ms=average_latency,
            )
        )
    return GradingExperimentComparison(
        dataset_snapshot_id=manifest.dataset_snapshot_id,
        dataset_content_sha256=manifest.dataset_content_sha256,
        sample_ids=manifest.sample_ids,
        prompt_version=manifest.prompt_version,
        conditions=conditions,
    )

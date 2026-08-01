"""Human-labelled calibration gate for the production open-answer grader.

This module deliberately calls :func:`grade_answer` instead of maintaining an Eval-only
prompt or parser.  Scripted tests can verify the accounting, but only samples that were
labelled by a human without seeing the model output are eligible to open the quality gate.
"""

from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.assessment.grading import VerdictLabel, grade_answer
from grandquiz.domain.learning.grading_samples import GradingCalibrationSample
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Provider

CalibrationStatus = Literal["passed", "failed", "insufficient_evidence"]


def load_grading_calibration_samples(path: str | Path) -> list["GradingCalibrationSample"]:
    """Load explicit labels from a user-owned YAML file; no repository fixture is assumed."""

    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("grading calibration YAML must contain a top-level list")
    return [GradingCalibrationSample.model_validate(item) for item in cast("list[Any]", raw)]


class GradingCalibrationPolicy(BaseModel):
    """Versioned numerical gate; cost is reported rather than silently optimized away."""

    model_config = ConfigDict(frozen=True)

    policy_version: Literal["grading-calibration-policy.v1"] = "grading-calibration-policy.v1"
    min_eligible_samples: int = Field(default=10, ge=1)
    min_verdict_agreement: float = Field(default=0.85, ge=0.0, le=1.0)
    min_point_accuracy: float = Field(default=0.90, ge=0.0, le=1.0)
    max_serious_false_negatives: int = Field(default=0, ge=0)
    max_serious_false_positives: int = Field(default=0, ge=0)


class GradingCalibrationResult(BaseModel):
    sample_id: str
    eligible: bool
    human_verdict: VerdictLabel
    model_verdict: VerdictLabel | None
    verdict_agreed: bool
    point_correct_count: int = Field(ge=0)
    point_count: int = Field(ge=0)
    serious_false_negative: bool
    serious_false_positive: bool
    attempts: int = Field(ge=0)
    retries: int = Field(ge=0)
    tokens: int = Field(ge=0)
    error: str | None = None


class GradingCalibrationReport(BaseModel):
    schema_version: Literal["grading-calibration-report.v1"] = "grading-calibration-report.v1"
    policy: GradingCalibrationPolicy
    status: CalibrationStatus
    sample_count: int = Field(ge=0)
    eligible_sample_count: int = Field(ge=0)
    exploratory_sample_count: int = Field(ge=0)
    verdict_agreement: float = Field(ge=0.0, le=1.0)
    point_accuracy: float = Field(ge=0.0, le=1.0)
    serious_false_negative_count: int = Field(ge=0)
    serious_false_positive_count: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    eligible_total_tokens: int = Field(ge=0)
    exploratory_total_tokens: int = Field(ge=0)
    eligible_average_tokens: float = Field(ge=0.0)
    results: list[GradingCalibrationResult]


def _model_usage(events: list[AgentEvent]) -> tuple[int, int]:
    ended = [event for event in events if event.type == EventType.MODEL_ENDED]
    tokens = 0
    for event in ended:
        usage_value = event.payload.get("usage")
        if isinstance(usage_value, dict):
            usage = cast("dict[str, object]", usage_value)
            value = usage.get("total_tokens")
            if isinstance(value, int):
                tokens += value
    return len(ended), tokens


async def run_grading_calibration(
    samples: list[GradingCalibrationSample],
    *,
    provider: Provider,
    policy: GradingCalibrationPolicy | None = None,
    max_attempts: int = 3,
) -> GradingCalibrationReport:
    """Run the production grader and fail closed when human evidence is insufficient."""

    effective_policy = policy or GradingCalibrationPolicy()
    results: list[GradingCalibrationResult] = []
    all_tokens = 0
    for index, sample in enumerate(samples):
        events: list[AgentEvent] = []
        sink = EventSink()
        sink.subscribe(events.append)
        emitter = EventEmitter(
            sink,
            ManualClock(),
            trace_id=f"grading-calibration:{index}:{sample.sample_id}",
        )
        model_verdict: VerdictLabel | None = None
        model_matched: set[str] = set()
        error: str | None = None
        try:
            verdict = await grade_answer(
                sample.question,
                sample.learner_answer,
                provider=provider,
                emitter=emitter,
                parent_span_id=None,
                max_attempts=max_attempts,
            )
            model_verdict = verdict.verdict
            model_matched = set(verdict.matched_points)
        except Exception as exc:  # the report records unusable production outputs as failures
            error = f"{type(exc).__name__}: {exc}"
        attempts, tokens = _model_usage(events)
        all_tokens += tokens
        expected_ids = {point.point_id for point in sample.question.expected_points}
        human_matched = set(sample.human_matched_points)
        point_correct = (
            sum(
                (point_id in model_matched) == (point_id in human_matched)
                for point_id in expected_ids
            )
            if model_verdict is not None
            else 0
        )
        false_negative = sample.human_verdict == "对" and model_verdict == "错"
        false_positive = sample.human_verdict == "错" and model_verdict == "对"
        results.append(
            GradingCalibrationResult(
                sample_id=sample.sample_id,
                eligible=sample.eligible,
                human_verdict=sample.human_verdict,
                model_verdict=model_verdict,
                verdict_agreed=model_verdict == sample.human_verdict,
                point_correct_count=point_correct,
                point_count=len(expected_ids),
                serious_false_negative=false_negative,
                serious_false_positive=false_positive,
                attempts=attempts,
                retries=max(attempts - 1, 0),
                tokens=tokens,
                error=error,
            )
        )

    eligible = [result for result in results if result.eligible]
    exploratory = [result for result in results if not result.eligible]
    eligible_points = sum(result.point_count for result in eligible)
    agreement = (
        sum(result.verdict_agreed for result in eligible) / len(eligible) if eligible else 0.0
    )
    point_accuracy = (
        sum(result.point_correct_count for result in eligible) / eligible_points
        if eligible_points
        else 0.0
    )
    false_negatives = sum(result.serious_false_negative for result in eligible)
    false_positives = sum(result.serious_false_positive for result in eligible)
    eligible_tokens = sum(result.tokens for result in eligible)
    exploratory_tokens = sum(result.tokens for result in exploratory)
    eligible_average_tokens = eligible_tokens / len(eligible) if eligible else 0.0
    if len(eligible) < effective_policy.min_eligible_samples:
        status: CalibrationStatus = "insufficient_evidence"
    elif (
        agreement >= effective_policy.min_verdict_agreement
        and point_accuracy >= effective_policy.min_point_accuracy
        and false_negatives <= effective_policy.max_serious_false_negatives
        and false_positives <= effective_policy.max_serious_false_positives
        and all(result.error is None for result in eligible)
    ):
        status = "passed"
    else:
        status = "failed"
    return GradingCalibrationReport(
        policy=effective_policy,
        status=status,
        sample_count=len(samples),
        eligible_sample_count=len(eligible),
        exploratory_sample_count=len(exploratory),
        verdict_agreement=agreement,
        point_accuracy=point_accuracy,
        serious_false_negative_count=false_negatives,
        serious_false_positive_count=false_positives,
        total_tokens=all_tokens,
        eligible_total_tokens=eligible_tokens,
        exploratory_total_tokens=exploratory_tokens,
        eligible_average_tokens=eligible_average_tokens,
        results=results,
    )

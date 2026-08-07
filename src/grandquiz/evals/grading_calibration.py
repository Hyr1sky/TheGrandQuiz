"""Human-labelled calibration gate for the production open-answer grader.

This module deliberately calls :func:`grade_answer` instead of maintaining an Eval-only
prompt or parser.  Scripted tests can verify the accounting, but only samples that were
labelled by a human without seeing the model output are eligible to open the quality gate.
"""

from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.assessment.grading import (
    GradingError,
    OpenAnswerDiagnosisKind,
    PointAssessment,
    VerdictLabel,
    grade_answer,
    grading_prompt_version,
)
from grandquiz.domain.learning.eval_inbox import DatasetSnapshotV1, eligible_grading_samples
from grandquiz.domain.learning.grading_samples import (
    AnswerProvenance,
    GradingCalibrationSample,
)
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Provider

CalibrationStatus = Literal["passed", "failed", "insufficient_evidence"]
ThinkingMode = Literal["enabled", "disabled", "provider_default", "unknown"]
CalibrationFailureKind = Literal["grading_contract", "provider_or_runtime"]


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


class CalibrationRunManifest(BaseModel):
    """Non-secret execution identity required to compare calibration runs."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["grading-calibration-run.v1"] = "grading-calibration-run.v1"
    provider: str = Field(min_length=1)
    endpoint_host: str | None = None
    model: str = Field(min_length=1)
    thinking_mode: ThinkingMode = "unknown"
    reasoning_effort: Literal["high", "max"] | None = None
    dataset_snapshot_id: str | None = None
    dataset_content_sha256: str | None = None
    sample_ids: tuple[str, ...] = ()
    prompt_version: str = "unresolved"


class GradingCalibrationResult(BaseModel):
    sample_id: str
    eligible: bool
    answer_provenance: AnswerProvenance = "unassisted_human"
    respondent_model: str | None = None
    human_verdict: VerdictLabel
    human_matched_points: list[str]
    human_missing_points: list[str]
    model_verdict: VerdictLabel | None
    derived_verdict: VerdictLabel | None
    model_matched_points: list[str]
    model_missing_points: list[str]
    model_diagnosis: OpenAnswerDiagnosisKind | None
    model_reason: str | None
    model_cited_evidence: list[str]
    model_point_assessments: list[PointAssessment] = Field(default_factory=list[PointAssessment])
    verdict_agreed: bool
    point_correct_count: int = Field(ge=0)
    point_count: int = Field(ge=0)
    serious_false_negative: bool
    serious_false_positive: bool
    attempts: int = Field(ge=0)
    retries: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    error: str | None = None
    output_valid: bool | None = None
    failure_kind: CalibrationFailureKind | None = None


class GradingCalibrationReport(BaseModel):
    schema_version: Literal[
        "grading-calibration-report.v2",
        "grading-calibration-report.v3",
        "grading-calibration-report.v4",
    ] = "grading-calibration-report.v4"
    run_manifest: CalibrationRunManifest
    policy: GradingCalibrationPolicy
    status: CalibrationStatus
    sample_count: int = Field(ge=0)
    eligible_sample_count: int = Field(ge=0)
    exploratory_sample_count: int = Field(ge=0)
    verdict_agreement: float = Field(ge=0.0, le=1.0)
    point_accuracy: float = Field(ge=0.0, le=1.0)
    serious_false_negative_count: int = Field(ge=0)
    serious_false_positive_count: int = Field(ge=0)
    total_prompt_tokens: int = Field(ge=0)
    total_completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    eligible_total_tokens: int = Field(ge=0)
    exploratory_total_tokens: int = Field(ge=0)
    eligible_average_tokens: float = Field(ge=0.0)
    eligible_valid_output_count: int | None = Field(default=None, ge=0)
    eligible_invalid_output_count: int | None = Field(default=None, ge=0)
    eligible_valid_output_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    results: list[GradingCalibrationResult]


def _model_usage(events: list[AgentEvent]) -> tuple[int, int, int, int]:
    ended = [event for event in events if event.type == EventType.MODEL_ENDED]
    prompt_tokens = 0
    completion_tokens = 0
    for event in ended:
        usage_value = event.payload.get("usage")
        if isinstance(usage_value, dict):
            usage = cast("dict[str, object]", usage_value)
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if isinstance(prompt, int):
                prompt_tokens += prompt
            if isinstance(completion, int):
                completion_tokens += completion
    return len(ended), prompt_tokens, completion_tokens, prompt_tokens + completion_tokens


async def run_grading_calibration(
    samples: list[GradingCalibrationSample],
    *,
    provider: Provider,
    policy: GradingCalibrationPolicy | None = None,
    max_attempts: int = 3,
    run_manifest: CalibrationRunManifest | None = None,
) -> GradingCalibrationReport:
    """Run the production grader and fail closed when human evidence is insufficient."""

    effective_policy = policy or GradingCalibrationPolicy()
    prompt_versions = sorted({grading_prompt_version(sample.question) for sample in samples})
    effective_prompt_version = (
        prompt_versions[0]
        if len(prompt_versions) == 1
        else f"mixed[{','.join(prompt_versions)}]"
        if prompt_versions
        else load_prompt("answer_grade").version
    )
    effective_manifest = (
        run_manifest
        or CalibrationRunManifest(provider="unspecified", model=type(provider).__name__)
    ).model_copy(update={"prompt_version": effective_prompt_version})
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
        model_missing: list[str] = []
        model_diagnosis: OpenAnswerDiagnosisKind | None = None
        model_reason: str | None = None
        model_cited_evidence: list[str] = []
        model_point_assessments: list[PointAssessment] = []
        error: str | None = None
        failure_kind: CalibrationFailureKind | None = None
        started = perf_counter()
        try:
            verdict = await grade_answer(
                sample.question,
                sample.learner_answer,
                provider=provider,
                emitter=emitter,
                parent_span_id=None,
                max_attempts=max_attempts,
            )
            model_verdict = verdict.model_verdict
            derived_verdict: VerdictLabel | None = verdict.verdict
            model_matched = set(verdict.matched_points)
            model_missing = list(verdict.missing_points)
            model_diagnosis = verdict.diagnosis
            model_reason = verdict.reason
            model_cited_evidence = list(verdict.cited_evidence)
            model_point_assessments = list(verdict.point_assessments)
        except Exception as exc:  # the report records unusable production outputs as failures
            error = f"{type(exc).__name__}: {exc}"
            failure_kind = (
                "grading_contract" if isinstance(exc, GradingError) else "provider_or_runtime"
            )
            derived_verdict = None
        latency_ms = (perf_counter() - started) * 1000
        attempts, prompt_tokens, completion_tokens, tokens = _model_usage(events)
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
        results.append(
            GradingCalibrationResult(
                sample_id=sample.sample_id,
                eligible=sample.eligible,
                answer_provenance=sample.answer_provenance,
                respondent_model=sample.respondent_model,
                human_verdict=sample.human_verdict,
                human_matched_points=sorted(sample.human_matched_points),
                human_missing_points=sorted(sample.human_missing_points),
                model_verdict=model_verdict,
                derived_verdict=derived_verdict,
                model_matched_points=sorted(model_matched),
                model_missing_points=sorted(model_missing),
                model_diagnosis=model_diagnosis,
                model_reason=model_reason,
                model_cited_evidence=model_cited_evidence,
                model_point_assessments=model_point_assessments,
                verdict_agreed=derived_verdict == sample.human_verdict,
                point_correct_count=point_correct,
                point_count=len(expected_ids),
                serious_false_negative=(sample.human_verdict == "对" and derived_verdict == "错"),
                serious_false_positive=(sample.human_verdict == "错" and derived_verdict == "对"),
                attempts=attempts,
                retries=max(attempts - 1, 0),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tokens=tokens,
                latency_ms=latency_ms,
                error=error,
                output_valid=error is None,
                failure_kind=failure_kind,
            )
        )

    eligible = [result for result in results if result.eligible]
    exploratory = [result for result in results if not result.eligible]
    valid_eligible = [result for result in eligible if result.output_valid is True]
    eligible_points = sum(result.point_count for result in valid_eligible)
    agreement = (
        sum(result.verdict_agreed for result in valid_eligible) / len(valid_eligible)
        if valid_eligible
        else 0.0
    )
    point_accuracy = (
        sum(result.point_correct_count for result in valid_eligible) / eligible_points
        if eligible_points
        else 0.0
    )
    false_negatives = sum(result.serious_false_negative for result in eligible)
    false_positives = sum(result.serious_false_positive for result in eligible)
    eligible_tokens = sum(result.tokens for result in eligible)
    exploratory_tokens = sum(result.tokens for result in exploratory)
    eligible_average_tokens = eligible_tokens / len(eligible) if eligible else 0.0
    eligible_valid_output_count = len(valid_eligible)
    eligible_invalid_output_count = len(eligible) - eligible_valid_output_count
    eligible_valid_output_rate = eligible_valid_output_count / len(eligible) if eligible else 0.0
    if len(eligible) < effective_policy.min_eligible_samples:
        status: CalibrationStatus = "insufficient_evidence"
    elif (
        agreement >= effective_policy.min_verdict_agreement
        and point_accuracy >= effective_policy.min_point_accuracy
        and false_negatives <= effective_policy.max_serious_false_negatives
        and false_positives <= effective_policy.max_serious_false_positives
        and eligible_invalid_output_count == 0
    ):
        status = "passed"
    else:
        status = "failed"
    return GradingCalibrationReport(
        run_manifest=effective_manifest,
        policy=effective_policy,
        status=status,
        sample_count=len(samples),
        eligible_sample_count=len(eligible),
        exploratory_sample_count=len(exploratory),
        verdict_agreement=agreement,
        point_accuracy=point_accuracy,
        serious_false_negative_count=false_negatives,
        serious_false_positive_count=false_positives,
        total_prompt_tokens=sum(result.prompt_tokens for result in results),
        total_completion_tokens=sum(result.completion_tokens for result in results),
        total_tokens=all_tokens,
        eligible_total_tokens=eligible_tokens,
        exploratory_total_tokens=exploratory_tokens,
        eligible_average_tokens=eligible_average_tokens,
        eligible_valid_output_count=eligible_valid_output_count,
        eligible_invalid_output_count=eligible_invalid_output_count,
        eligible_valid_output_rate=eligible_valid_output_rate,
        results=results,
    )


async def run_snapshot_grading_calibration(
    snapshot: DatasetSnapshotV1,
    *,
    provider: Provider,
    policy: GradingCalibrationPolicy | None = None,
    max_attempts: int = 3,
    run_manifest: CalibrationRunManifest | None = None,
    sample_ids: list[str] | None = None,
) -> GradingCalibrationReport:
    """Run the existing production gate from one reviewed immutable snapshot."""

    eligible = eligible_grading_samples(snapshot)
    if sample_ids is not None:
        requested = set(sample_ids)
        if len(requested) != len(sample_ids):
            raise ValueError("pilot sample_ids must not contain duplicates")
        available = {sample.sample_id for sample in eligible}
        unknown = requested - available
        if unknown:
            raise ValueError(
                f"pilot sample_ids are not eligible in this snapshot: {sorted(unknown)}"
            )
        eligible = [sample for sample in eligible if sample.sample_id in requested]
    selected_ids = tuple(sorted(sample.sample_id for sample in eligible))
    effective_manifest = (
        run_manifest
        or CalibrationRunManifest(provider="unspecified", model=type(provider).__name__)
    ).model_copy(
        update={
            "dataset_snapshot_id": snapshot.snapshot_id,
            "dataset_content_sha256": snapshot.content_sha256,
            "sample_ids": selected_ids,
        }
    )
    return await run_grading_calibration(
        eligible,
        provider=provider,
        policy=policy,
        max_attempts=max_attempts,
        run_manifest=effective_manifest,
    )

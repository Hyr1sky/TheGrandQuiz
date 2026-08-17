"""Fail-closed pairing of immutable Eval subject evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from grandquiz.evals.case import EvalSurface
from grandquiz.evals.subject import EvalSubjectSnapshot, SubjectEvaluation

ExecutionStatus = Literal["completed", "provider_error", "runtime_error"]


class EvalSuiteInputs(BaseModel):
    """Immutable dataset, policy, slice, and metric identity shared by both subjects."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-suite-inputs.v1"] = "eval-suite-inputs.v1"
    dataset_snapshot_id: str = Field(min_length=1)
    dataset_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    suite_policy_version: str = Field(min_length=1)
    slice_manifest_version: str = Field(min_length=1)
    metric_versions: tuple[tuple[str, str], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _canonical_metrics(self) -> Self:
        if any(not name or not version for name, version in self.metric_versions):
            raise ValueError("metric identities cannot be empty")
        if len({name for name, _ in self.metric_versions}) != len(self.metric_versions):
            raise ValueError("metric identities must be unique")
        canonical = tuple(sorted(self.metric_versions))
        if canonical != self.metric_versions:
            raise ValueError("metric identities must use canonical name order")
        return self


class EvalSampleEvidence(BaseModel):
    """One sample's separate execution, rule, semantic, validity, and cost dimensions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str = Field(min_length=1)
    surface: EvalSurface
    slice_id: str = Field(min_length=1)
    execution_status: ExecutionStatus
    rule_passed: bool | None = None
    semantic_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    output_valid: bool | None = None
    execution_tokens: int = Field(ge=0)
    judge_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    retry_count: int = Field(ge=0)
    stability_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _separate_execution_failure(self) -> Self:
        if self.execution_status != "completed" and any(
            value is not None
            for value in (self.rule_passed, self.semantic_quality, self.output_valid)
        ):
            raise ValueError("failed execution cannot carry semantic evidence or verdicts")
        return self


class EvalRunEvidence(BaseModel):
    """One subject's result on a frozen suite, before baseline/candidate comparison."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-run-evidence.v1"] = "eval-run-evidence.v1"
    suite: EvalSuiteInputs
    samples: tuple[EvalSampleEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_samples(self) -> Self:
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("Eval run sample ids must be unique")
        return self


class PairedSampleEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str
    surface: EvalSurface
    slice_id: str
    baseline: EvalSampleEvidence
    candidate: EvalSampleEvidence


class PairedEvalExperiment(BaseModel):
    """Typed comparison input; it contains evidence but no promotion verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-paired-experiment.v1"] = "eval-paired-experiment.v1"
    experiment_id: str = Field(min_length=64, max_length=64)
    suite: EvalSuiteInputs
    baseline_subject: EvalSubjectSnapshot
    candidate_subject: EvalSubjectSnapshot
    samples: tuple[PairedSampleEvidence, ...]


def pair_subject_evaluations(
    *,
    baseline: SubjectEvaluation[EvalRunEvidence],
    candidate: SubjectEvaluation[EvalRunEvidence],
) -> PairedEvalExperiment:
    """Pair identical immutable suite inputs without collapsing evidence dimensions."""

    if baseline.subject.subject_id == candidate.subject.subject_id:
        raise ValueError("baseline and candidate subjects must differ")
    if baseline.report.suite != candidate.report.suite:
        raise ValueError("paired subjects require identical immutable suite inputs")

    baseline_by_id = {sample.sample_id: sample for sample in baseline.report.samples}
    candidate_by_id = {sample.sample_id: sample for sample in candidate.report.samples}
    if set(baseline_by_id) != set(candidate_by_id):
        raise ValueError("baseline and candidate samples must pair exactly")

    paired_samples: list[PairedSampleEvidence] = []
    for sample_id in sorted(baseline_by_id):
        baseline_sample = baseline_by_id[sample_id]
        candidate_sample = candidate_by_id[sample_id]
        if (
            baseline_sample.surface,
            baseline_sample.slice_id,
        ) != (
            candidate_sample.surface,
            candidate_sample.slice_id,
        ):
            raise ValueError("paired sample surface and slice identities must match")
        paired_samples.append(
            PairedSampleEvidence(
                sample_id=sample_id,
                surface=baseline_sample.surface,
                slice_id=baseline_sample.slice_id,
                baseline=baseline_sample,
                candidate=candidate_sample,
            )
        )

    canonical = json.dumps(
        {
            "schema_version": "eval-paired-experiment.v1",
            "baseline_subject_id": baseline.subject.subject_id,
            "candidate_subject_id": candidate.subject.subject_id,
            "suite": baseline.report.suite.model_dump(mode="json"),
            "sample_ids": [sample.sample_id for sample in paired_samples],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return PairedEvalExperiment(
        experiment_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        suite=baseline.report.suite,
        baseline_subject=baseline.subject,
        candidate_subject=candidate.subject,
        samples=tuple(paired_samples),
    )

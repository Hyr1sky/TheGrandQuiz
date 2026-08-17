"""Human-review evidence projection for paired Eval experiments."""

from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.evals.case import EvalSurface
from grandquiz.evals.experiment import PairedEvalExperiment, PairedSampleEvidence

EvidenceState = Literal[
    "rejected",
    "ambiguous",
    "insufficient_evidence",
    "eligible_for_review",
]


class EvalExperimentPolicy(BaseModel):
    """Pre-registered gates for interpreting, never promoting, paired evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-experiment-policy.v1"] = "eval-experiment-policy.v1"
    policy_id: str = Field(min_length=1)
    blocking_slice_ids: tuple[str, ...]
    min_semantic_gain: float = Field(gt=0.0, le=1.0)
    max_execution_token_ratio: float = Field(ge=1.0)
    max_judge_token_ratio: float = Field(ge=1.0)
    max_latency_ratio: float = Field(ge=1.0)
    max_retry_increase: int = Field(ge=0)
    min_stability_rate: float = Field(ge=0.0, le=1.0)


class SampleComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str
    surface: EvalSurface
    slice_id: str
    baseline_execution_status: str
    candidate_execution_status: str
    rule_change: str
    validity_change: str
    semantic_delta: float | None
    execution_token_delta: int
    judge_token_delta: int
    latency_delta_ms: float
    retry_delta: int
    stability_delta: float | None
    failure_taxonomy: tuple[str, ...]


class EvidenceSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_count: int = Field(ge=0)
    semantic_gain_count: int = Field(ge=0)
    semantic_regression_count: int = Field(ge=0)
    execution_failure_count: int = Field(ge=0)
    rule_regression_count: int = Field(ge=0)
    validity_regression_count: int = Field(ge=0)


class SurfaceSummary(EvidenceSummary):
    surface: EvalSurface


class SliceSummary(EvidenceSummary):
    slice_id: str
    blocking: bool


class EvalExperimentReport(BaseModel):
    """Auditable development evidence that can only request human review."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["eval-experiment-report.v1"] = "eval-experiment-report.v1"
    experiment_id: str
    policy_id: str
    evidence_state: EvidenceState
    promotion_eligible: Literal[False] = False
    review_reasons: tuple[str, ...]
    execution_token_ratio: float | None
    judge_token_ratio: float | None
    average_latency_ratio: float | None
    retry_increase: int
    samples: tuple[SampleComparison, ...]
    surface_summaries: tuple[SurfaceSummary, ...]
    slice_summaries: tuple[SliceSummary, ...]


def _change(before: bool | None, after: bool | None) -> str:
    if before is None or after is None:
        return "not_comparable"
    if before == after:
        return "unchanged_pass" if before else "unchanged_fail"
    return "improved" if after else "regressed"


def _ratio(candidate: float, baseline: float) -> float | None:
    if baseline == 0:
        return 1.0 if candidate == 0 else None
    return round(candidate / baseline, 6)


def _sample_comparison(
    sample: PairedSampleEvidence,
    *,
    policy: EvalExperimentPolicy,
) -> SampleComparison:
    baseline = sample.baseline
    candidate = sample.candidate
    taxonomy: set[str] = set()
    if baseline.execution_status != "completed":
        taxonomy.add("baseline_execution_failure")
    if candidate.execution_status != "completed":
        taxonomy.add("candidate_execution_failure")

    rule_change = _change(baseline.rule_passed, candidate.rule_passed)
    validity_change = _change(baseline.output_valid, candidate.output_valid)
    if rule_change == "regressed":
        taxonomy.add("rule_regression")
    if validity_change == "regressed":
        taxonomy.add("validity_regression")
    if candidate.rule_passed is False:
        taxonomy.add("candidate_rule_failed")
    if candidate.output_valid is False:
        taxonomy.add("candidate_output_invalid")

    semantic_delta: float | None = None
    if baseline.semantic_quality is None or candidate.semantic_quality is None:
        taxonomy.add("semantic_evidence_missing")
    else:
        semantic_delta = round(candidate.semantic_quality - baseline.semantic_quality, 6)
        if semantic_delta < 0:
            taxonomy.add("semantic_regression")

    stability_delta: float | None = None
    if baseline.stability_rate is not None and candidate.stability_rate is not None:
        stability_delta = round(candidate.stability_rate - baseline.stability_rate, 6)
    if (
        candidate.stability_rate is not None
        and candidate.stability_rate < policy.min_stability_rate
    ):
        taxonomy.add("stability_regression")

    regressions = {
        "candidate_execution_failure",
        "rule_regression",
        "validity_regression",
        "semantic_regression",
        "stability_regression",
    }
    if sample.slice_id in policy.blocking_slice_ids and taxonomy & regressions:
        taxonomy.add("blocking_slice_regression")

    return SampleComparison(
        sample_id=sample.sample_id,
        surface=sample.surface,
        slice_id=sample.slice_id,
        baseline_execution_status=baseline.execution_status,
        candidate_execution_status=candidate.execution_status,
        rule_change=rule_change,
        validity_change=validity_change,
        semantic_delta=semantic_delta,
        execution_token_delta=candidate.execution_tokens - baseline.execution_tokens,
        judge_token_delta=candidate.judge_tokens - baseline.judge_tokens,
        latency_delta_ms=round(candidate.latency_ms - baseline.latency_ms, 6),
        retry_delta=candidate.retry_count - baseline.retry_count,
        stability_delta=stability_delta,
        failure_taxonomy=tuple(sorted(taxonomy)),
    )


def _summarize(items: list[SampleComparison]) -> dict[str, int]:
    return {
        "sample_count": len(items),
        "semantic_gain_count": sum(
            item.semantic_delta is not None and item.semantic_delta > 0 for item in items
        ),
        "semantic_regression_count": sum(
            "semantic_regression" in item.failure_taxonomy for item in items
        ),
        "execution_failure_count": sum(
            "candidate_execution_failure" in item.failure_taxonomy for item in items
        ),
        "rule_regression_count": sum("rule_regression" in item.failure_taxonomy for item in items),
        "validity_regression_count": sum(
            "validity_regression" in item.failure_taxonomy for item in items
        ),
    }


def build_experiment_report(
    experiment: PairedEvalExperiment,
    *,
    policy: EvalExperimentPolicy,
) -> EvalExperimentReport:
    """Project paired evidence without collapsing dimensions or authorizing promotion."""

    samples = tuple(_sample_comparison(sample, policy=policy) for sample in experiment.samples)
    baseline_execution_tokens = sum(
        sample.baseline.execution_tokens for sample in experiment.samples
    )
    candidate_execution_tokens = sum(
        sample.candidate.execution_tokens for sample in experiment.samples
    )
    baseline_judge_tokens = sum(sample.baseline.judge_tokens for sample in experiment.samples)
    candidate_judge_tokens = sum(sample.candidate.judge_tokens for sample in experiment.samples)
    baseline_latency = sum(sample.baseline.latency_ms for sample in experiment.samples)
    candidate_latency = sum(sample.candidate.latency_ms for sample in experiment.samples)
    retry_increase = sum(sample.retry_delta for sample in samples)

    execution_token_ratio = _ratio(candidate_execution_tokens, baseline_execution_tokens)
    judge_token_ratio = _ratio(candidate_judge_tokens, baseline_judge_tokens)
    latency_ratio = _ratio(candidate_latency, baseline_latency)

    reasons: set[str] = set()
    taxonomies = {item for sample in samples for item in sample.failure_taxonomy}
    hard_failures = {
        "candidate_execution_failure",
        "candidate_output_invalid",
        "candidate_rule_failed",
        "rule_regression",
        "validity_regression",
        "blocking_slice_regression",
    }
    if taxonomies & hard_failures:
        evidence_state: EvidenceState = "rejected"
        reasons.update(taxonomies & hard_failures)
    elif "semantic_evidence_missing" in taxonomies or "baseline_execution_failure" in taxonomies:
        evidence_state = "insufficient_evidence"
        reasons.update(taxonomies & {"semantic_evidence_missing", "baseline_execution_failure"})
    else:
        if "semantic_regression" in taxonomies:
            reasons.add("nonblocking_semantic_regression")
        if "stability_regression" in taxonomies:
            reasons.add("stability_regression")
        if execution_token_ratio is None:
            reasons.add("execution_token_baseline_zero")
        elif execution_token_ratio > policy.max_execution_token_ratio:
            reasons.add("execution_token_regression")
        if judge_token_ratio is None:
            reasons.add("judge_token_baseline_zero")
        elif judge_token_ratio > policy.max_judge_token_ratio:
            reasons.add("judge_token_regression")
        if latency_ratio is None:
            reasons.add("latency_baseline_zero")
        elif latency_ratio > policy.max_latency_ratio:
            reasons.add("latency_regression")
        if retry_increase > policy.max_retry_increase:
            reasons.add("retry_regression")
        gains = [
            sample.semantic_delta
            for sample in samples
            if sample.semantic_delta is not None
            and sample.semantic_delta >= policy.min_semantic_gain
        ]
        if reasons:
            evidence_state = "ambiguous"
        elif not gains:
            evidence_state = "insufficient_evidence"
            reasons.add("pre_registered_gain_missing")
        else:
            evidence_state = "eligible_for_review"

    by_surface: dict[EvalSurface, list[SampleComparison]] = defaultdict(list)
    by_slice: dict[str, list[SampleComparison]] = defaultdict(list)
    for sample in samples:
        by_surface[sample.surface].append(sample)
        by_slice[sample.slice_id].append(sample)
    surface_summaries = tuple(
        SurfaceSummary(surface=surface, **_summarize(by_surface[surface]))
        for surface in sorted(by_surface)
    )
    slice_summaries = tuple(
        SliceSummary(
            slice_id=slice_id,
            blocking=slice_id in policy.blocking_slice_ids,
            **_summarize(by_slice[slice_id]),
        )
        for slice_id in sorted(by_slice)
    )
    return EvalExperimentReport(
        experiment_id=experiment.experiment_id,
        policy_id=policy.policy_id,
        evidence_state=evidence_state,
        review_reasons=tuple(sorted(reasons)),
        execution_token_ratio=execution_token_ratio,
        judge_token_ratio=judge_token_ratio,
        average_latency_ratio=latency_ratio,
        retry_increase=retry_increase,
        samples=samples,
        surface_summaries=surface_summaries,
        slice_summaries=slice_summaries,
    )


def render_experiment_html(report: EvalExperimentReport) -> str:
    """Render a self-contained safe summary without prompts, cassettes, or raw traces."""

    rows = "".join(
        "<tr>"
        f"<td>{escape(sample.sample_id)}</td>"
        f"<td>{escape(sample.surface)}</td>"
        f"<td>{escape(sample.slice_id)}</td>"
        f"<td>{'N/A' if sample.semantic_delta is None else sample.semantic_delta:+.3f}</td>"
        f"<td>{sample.execution_token_delta:+d}</td>"
        f"<td>{sample.judge_token_delta:+d}</td>"
        f"<td>{escape(', '.join(sample.failure_taxonomy) or 'none')}</td>"
        "</tr>"
        for sample in report.samples
    )
    reasons = escape(", ".join(report.review_reasons) or "none")
    return (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Eval Experiment</title><style>"
        "body{font:15px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#18201d}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccd4d0;padding:8px}"
        "th{text-align:left;background:#eef2f0}code{overflow-wrap:anywhere}"
        "</style></head><body>"
        "<h1>Eval Experiment</h1>"
        f"<p><strong>状态：</strong>{escape(report.evidence_state)}</p>"
        f"<p><strong>Experiment：</strong><code>{escape(report.experiment_id)}</code></p>"
        f"<p><strong>复核原因：</strong>{reasons}</p>"
        "<p><strong>边界：</strong>仅供人类审查；本报告不能自动晋升或修改生产配置。</p>"
        "<table><thead><tr><th>Sample</th><th>Surface</th><th>Slice</th>"
        "<th>Semantic Δ</th><th>Execution tokens Δ</th><th>Judge tokens Δ</th>"
        "<th>Taxonomy</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></body></html>"
    )

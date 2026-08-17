"""Human-review evidence from paired Eval experiments."""

from grandquiz.evals.experiment import (
    EvalRunEvidence,
    EvalSampleEvidence,
    EvalSuiteInputs,
    pair_subject_evaluations,
)
from grandquiz.evals.experiment_report import (
    EvalExperimentPolicy,
    build_experiment_report,
    render_experiment_html,
)
from grandquiz.evals.subject import ProviderIdentity, SubjectEvaluation, snapshot_subject


def _subject(prompt: str):
    return snapshot_subject(
        prompts={"quality": prompt},
        providers=(
            ProviderIdentity(
                role="basic",
                provider="openai-compatible",
                model="deepseek-v4-flash",
                thinking="disabled",
            ),
        ),
        tool_schemas={},
        policies={"quality": "quality-policy@v1"},
    )


def _suite() -> EvalSuiteInputs:
    return EvalSuiteInputs(
        dataset_snapshot_id="dataset-01",
        dataset_content_sha256="a" * 64,
        suite_policy_version="quality-suite@v1",
        slice_manifest_version="quality-slices@v1",
        metric_versions=(
            ("quality", "quality-metric@v1"),
            ("stability", "stability-metric@v1"),
        ),
    )


def _sample(
    sample_id: str,
    *,
    slice_id: str,
    quality: float | None,
    tokens: int = 100,
    latency_ms: float = 100.0,
    retries: int = 0,
    stability: float = 1.0,
    rule_passed: bool = True,
    output_valid: bool = True,
) -> EvalSampleEvidence:
    return EvalSampleEvidence(
        sample_id=sample_id,
        surface="grounded_answer",
        slice_id=slice_id,
        execution_status="completed",
        rule_passed=rule_passed,
        semantic_quality=quality,
        output_valid=output_valid,
        execution_tokens=tokens,
        judge_tokens=20,
        latency_ms=latency_ms,
        retry_count=retries,
        stability_rate=stability,
    )


def _paired(candidate_samples: tuple[EvalSampleEvidence, ...]):
    baseline_samples = (
        _sample("critical", slice_id="critical", quality=0.6),
        _sample("ordinary", slice_id="ordinary", quality=0.6),
    )
    return pair_subject_evaluations(
        baseline=SubjectEvaluation(
            subject=_subject("quality@baseline"),
            report=EvalRunEvidence(suite=_suite(), samples=baseline_samples),
        ),
        candidate=SubjectEvaluation(
            subject=_subject("quality@candidate"),
            report=EvalRunEvidence(suite=_suite(), samples=candidate_samples),
        ),
    )


def _policy() -> EvalExperimentPolicy:
    return EvalExperimentPolicy(
        policy_id="quality-comparison@v1",
        blocking_slice_ids=("critical",),
        min_semantic_gain=0.1,
        max_execution_token_ratio=1.15,
        max_judge_token_ratio=1.15,
        max_latency_ratio=1.25,
        max_retry_increase=0,
        min_stability_rate=0.95,
    )


def test_worse_candidate_is_rejected_even_when_aggregate_quality_rises() -> None:
    paired = _paired(
        (
            _sample("critical", slice_id="critical", quality=0.4),
            _sample("ordinary", slice_id="ordinary", quality=0.9),
        )
    )

    report = build_experiment_report(paired, policy=_policy())

    assert report.evidence_state == "rejected"
    assert report.promotion_eligible is False
    critical = next(sample for sample in report.samples if sample.sample_id == "critical")
    assert critical.semantic_delta == -0.2
    assert "semantic_regression" in critical.failure_taxonomy
    assert "blocking_slice_regression" in critical.failure_taxonomy
    assert report.surface_summaries[0].semantic_regression_count == 1
    assert next(item for item in report.slice_summaries if item.slice_id == "critical").blocking


def test_mixed_cost_tradeoff_is_ambiguous_not_promoted() -> None:
    paired = _paired(
        (
            _sample("critical", slice_id="critical", quality=0.8, tokens=200),
            _sample("ordinary", slice_id="ordinary", quality=0.8, tokens=200),
        )
    )

    report = build_experiment_report(paired, policy=_policy())

    assert report.evidence_state == "ambiguous"
    assert report.promotion_eligible is False
    assert report.execution_token_ratio == 2.0
    assert report.judge_token_ratio == 1.0
    assert "execution_token_regression" in report.review_reasons


def test_better_candidate_becomes_eligible_for_human_review_only() -> None:
    paired = _paired(
        (
            _sample("critical", slice_id="critical", quality=0.8, tokens=105),
            _sample("ordinary", slice_id="ordinary", quality=0.8, tokens=105),
        )
    )

    report = build_experiment_report(paired, policy=_policy())

    assert report.schema_version == "eval-experiment-report.v1"
    assert report.evidence_state == "eligible_for_review"
    assert report.promotion_eligible is False
    assert report.execution_token_ratio == 1.05
    assert report.judge_token_ratio == 1.0
    assert report.average_latency_ratio == 1.0
    assert report.review_reasons == ()


def test_missing_semantic_evidence_is_explicitly_insufficient() -> None:
    paired = _paired(
        (
            _sample("critical", slice_id="critical", quality=None),
            _sample("ordinary", slice_id="ordinary", quality=0.8),
        )
    )

    report = build_experiment_report(paired, policy=_policy())

    assert report.evidence_state == "insufficient_evidence"
    assert "semantic_evidence_missing" in report.review_reasons


def test_candidate_that_still_fails_the_rule_gate_is_rejected() -> None:
    paired = _paired(
        (
            _sample(
                "critical",
                slice_id="critical",
                quality=0.8,
                rule_passed=False,
            ),
            _sample("ordinary", slice_id="ordinary", quality=0.8),
        )
    )

    report = build_experiment_report(paired, policy=_policy())

    assert report.evidence_state == "rejected"
    assert "candidate_rule_failed" in report.review_reasons


def test_html_report_is_self_contained_escaped_and_omits_subject_payloads() -> None:
    unsafe = "<script>alert('x')</script>"
    baseline_sample = _sample(unsafe, slice_id="critical", quality=0.6)
    candidate_sample = _sample(unsafe, slice_id="critical", quality=0.8)
    paired = pair_subject_evaluations(
        baseline=SubjectEvaluation(
            subject=_subject("private-prompt@baseline"),
            report=EvalRunEvidence(suite=_suite(), samples=(baseline_sample,)),
        ),
        candidate=SubjectEvaluation(
            subject=_subject("private-prompt@candidate"),
            report=EvalRunEvidence(suite=_suite(), samples=(candidate_sample,)),
        ),
    )

    html = render_experiment_html(build_experiment_report(paired, policy=_policy()))

    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert unsafe not in html
    assert "private-prompt" not in html
    assert "cassette" not in html
    assert "<script src=" not in html
    assert "http://" not in html and "https://" not in html
    assert "仅供人类审查" in html

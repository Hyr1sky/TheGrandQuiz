"""Paired Eval Experiment public contract."""

import pytest
from pydantic import ValidationError

from grandquiz.evals.experiment import (
    EvalRunEvidence,
    EvalSampleEvidence,
    EvalSuiteInputs,
    pair_subject_evaluations,
)
from grandquiz.evals.subject import (
    EvalSubjectSnapshot,
    ProviderIdentity,
    ReplayEvidence,
    SubjectEvaluation,
    snapshot_subject,
)


def _subject(prompt_version: str, cassette: str) -> EvalSubjectSnapshot:
    return snapshot_subject(
        prompts={"quality": prompt_version},
        providers=(
            ProviderIdentity(
                role="basic",
                provider="openai-compatible",
                model="deepseek-v4-flash",
                thinking="disabled",
            ),
        ),
        tool_schemas={"grounded_answer": "sha256:tool-v1"},
        policies={"budget": "budget-v2"},
        replay_evidence=(
            ReplayEvidence(
                owner="quality-suite",
                cassette=cassette,
                sha256="a" * 64,
            ),
        ),
    )


def _suite() -> EvalSuiteInputs:
    return EvalSuiteInputs(
        dataset_snapshot_id="dataset-01",
        dataset_content_sha256="b" * 64,
        suite_policy_version="quality-suite@v1",
        slice_manifest_version="quality-slices@v1",
        metric_versions=(
            ("quality", "quality-metric@v1"),
            ("stability", "stability-metric@v1"),
        ),
    )


def _sample(sample_id: str, *, semantic_quality: float) -> EvalSampleEvidence:
    return EvalSampleEvidence(
        sample_id=sample_id,
        surface="grounded_answer",
        slice_id="supported" if sample_id == "a" else "refusal",
        execution_status="completed",
        rule_passed=True,
        semantic_quality=semantic_quality,
        output_valid=True,
        execution_tokens=100,
        judge_tokens=20,
        latency_ms=250.0,
        retry_count=0,
        stability_rate=1.0,
    )


def test_pairing_is_order_independent_and_keeps_dimensions_separate() -> None:
    baseline_subject = _subject("quality@baseline", "baseline.json")
    candidate_subject = _subject("quality@candidate", "candidate.json")
    baseline = SubjectEvaluation(
        subject=baseline_subject,
        report=EvalRunEvidence(
            suite=_suite(),
            samples=(_sample("b", semantic_quality=0.5), _sample("a", semantic_quality=0.75)),
        ),
    )
    candidate = SubjectEvaluation(
        subject=candidate_subject,
        report=EvalRunEvidence(
            suite=_suite(),
            samples=(_sample("a", semantic_quality=1.0), _sample("b", semantic_quality=0.75)),
        ),
    )

    paired = pair_subject_evaluations(baseline=baseline, candidate=candidate)
    reordered = pair_subject_evaluations(
        baseline=SubjectEvaluation(
            subject=baseline_subject,
            report=baseline.report.model_copy(
                update={"samples": tuple(reversed(baseline.report.samples))}
            ),
        ),
        candidate=SubjectEvaluation(
            subject=candidate_subject,
            report=candidate.report.model_copy(
                update={"samples": tuple(reversed(candidate.report.samples))}
            ),
        ),
    )

    assert paired.schema_version == "eval-paired-experiment.v1"
    assert paired.experiment_id == reordered.experiment_id
    assert tuple(sample.sample_id for sample in paired.samples) == ("a", "b")
    assert paired.baseline_subject.replay_evidence[0].cassette == "baseline.json"
    assert paired.candidate_subject.replay_evidence[0].cassette == "candidate.json"
    assert paired.samples[0].baseline.semantic_quality == 0.75
    assert paired.samples[0].candidate.semantic_quality == 1.0
    assert paired.samples[0].candidate.execution_tokens == 100
    assert paired.samples[0].candidate.judge_tokens == 20
    assert paired.samples[0].candidate.latency_ms == 250.0
    assert paired.samples[0].candidate.retry_count == 0
    assert paired.samples[0].candidate.stability_rate == 1.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_snapshot_id", "different-dataset"),
        ("suite_policy_version", "quality-suite@v2"),
        ("slice_manifest_version", "quality-slices@v2"),
        ("metric_versions", (("quality", "quality-metric@v2"),)),
    ],
)
def test_pairing_rejects_any_suite_input_drift(field: str, value: object) -> None:
    baseline = SubjectEvaluation(
        subject=_subject("quality@baseline", "baseline.json"),
        report=EvalRunEvidence(suite=_suite(), samples=(_sample("a", semantic_quality=0.5),)),
    )
    drifted_suite = _suite().model_copy(update={field: value})
    candidate = SubjectEvaluation(
        subject=_subject("quality@candidate", "candidate.json"),
        report=EvalRunEvidence(
            suite=drifted_suite,
            samples=(_sample("a", semantic_quality=1.0),),
        ),
    )

    with pytest.raises(ValueError, match="identical immutable suite inputs"):
        pair_subject_evaluations(baseline=baseline, candidate=candidate)


def test_execution_failure_cannot_be_encoded_as_semantic_loss_or_pass() -> None:
    with pytest.raises(ValidationError, match="failed execution cannot carry semantic evidence"):
        EvalSampleEvidence(
            sample_id="provider-failure",
            surface="grounded_answer",
            slice_id="supported",
            execution_status="provider_error",
            rule_passed=False,
            semantic_quality=0.0,
            output_valid=False,
            execution_tokens=0,
            judge_tokens=0,
            latency_ms=10.0,
            retry_count=1,
            stability_rate=0.0,
        )


def test_execution_failure_remains_an_explicit_paired_sample() -> None:
    failed = EvalSampleEvidence(
        sample_id="a",
        surface="grounded_answer",
        slice_id="supported",
        execution_status="provider_error",
        execution_tokens=0,
        judge_tokens=0,
        latency_ms=10.0,
        retry_count=1,
    )
    baseline = SubjectEvaluation(
        subject=_subject("quality@baseline", "baseline.json"),
        report=EvalRunEvidence(suite=_suite(), samples=(_sample("a", semantic_quality=0.5),)),
    )
    candidate = SubjectEvaluation(
        subject=_subject("quality@candidate", "candidate.json"),
        report=EvalRunEvidence(suite=_suite(), samples=(failed,)),
    )

    paired = pair_subject_evaluations(baseline=baseline, candidate=candidate)

    assert len(paired.samples) == 1
    assert paired.samples[0].candidate.execution_status == "provider_error"
    assert paired.samples[0].candidate.semantic_quality is None
    assert paired.samples[0].candidate.rule_passed is None
    assert paired.samples[0].candidate.output_valid is None

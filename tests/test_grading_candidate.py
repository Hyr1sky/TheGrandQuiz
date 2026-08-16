"""Pre-registered grading-entailment candidate contract."""

import pytest

from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.evals.grading_calibration import (
    CalibrationRunManifest,
    GradingCalibrationPolicy,
    GradingCalibrationReport,
    GradingCalibrationResult,
)
from grandquiz.evals.grading_candidate import (
    GradingEntailmentPlan,
    evaluate_grading_entailment_candidate,
)
from grandquiz.evals.subject import (
    EvalSubjectSnapshot,
    ProviderIdentity,
    SubjectEvaluation,
    snapshot_subject,
)


def _report(*, candidate: bool) -> GradingCalibrationReport:
    results: list[GradingCalibrationResult] = []
    for index in range(1, 6):
        repaired = candidate
        results.append(
            GradingCalibrationResult(
                sample_id=f"fn-{index}",
                eligible=True,
                human_verdict="对",
                human_matched_points=["p1"],
                human_missing_points=[],
                model_verdict="对" if repaired else "错",
                derived_verdict="对" if repaired else "错",
                model_matched_points=["p1"] if repaired else [],
                model_missing_points=[] if repaired else ["p1"],
                model_diagnosis="complete" if repaired else "missing_key_point",
                model_reason="recorded",
                model_cited_evidence=["e1"] if repaired else [],
                verdict_agreed=repaired,
                point_correct_count=int(repaired),
                point_count=1,
                serious_false_negative=False,
                serious_false_positive=False,
                attempts=1,
                retries=0,
                prompt_tokens=90 if candidate else 82,
                completion_tokens=20 if candidate else 18,
                tokens=110 if candidate else 100,
                latency_ms=100.0,
                output_valid=True,
            )
        )
    for index in range(1, 3):
        results.append(
            GradingCalibrationResult(
                sample_id=f"control-{index}",
                eligible=True,
                human_verdict="错",
                human_matched_points=[],
                human_missing_points=["p1"],
                model_verdict="错",
                derived_verdict="错",
                model_matched_points=[],
                model_missing_points=["p1"],
                model_diagnosis="missing_key_point",
                model_reason="recorded",
                model_cited_evidence=[],
                verdict_agreed=True,
                point_correct_count=1,
                point_count=1,
                serious_false_negative=False,
                serious_false_positive=False,
                attempts=1,
                retries=0,
                prompt_tokens=90 if candidate else 82,
                completion_tokens=20 if candidate else 18,
                tokens=110 if candidate else 100,
                latency_ms=100.0,
                output_valid=True,
            )
        )
    total_tokens = sum(result.tokens for result in results)
    return GradingCalibrationReport(
        run_manifest=CalibrationRunManifest(
            provider="deepseek",
            model="deepseek-chat",
            thinking_mode="disabled",
            dataset_snapshot_id="holdout-03-development-gold",
            dataset_content_sha256="frozen-content",
            sample_ids=tuple(sorted(result.sample_id for result in results)),
            prompt_version="answer-grade@candidate" if candidate else "answer-grade@baseline",
        ),
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
        status="failed",
        sample_count=len(results),
        eligible_sample_count=len(results),
        exploratory_sample_count=0,
        verdict_agreement=sum(result.verdict_agreed for result in results) / len(results),
        point_accuracy=sum(result.point_correct_count for result in results) / len(results),
        serious_false_negative_count=sum(result.serious_false_negative for result in results),
        serious_false_positive_count=0,
        total_prompt_tokens=sum(result.prompt_tokens for result in results),
        total_completion_tokens=sum(result.completion_tokens for result in results),
        total_tokens=total_tokens,
        eligible_total_tokens=total_tokens,
        exploratory_total_tokens=0,
        eligible_average_tokens=total_tokens / len(results),
        eligible_valid_output_count=len(results),
        eligible_invalid_output_count=0,
        eligible_valid_output_rate=1.0,
        results=results,
    )


def _subject(prompt_version: str) -> EvalSubjectSnapshot:
    return snapshot_subject(
        prompts={"answer_grade": prompt_version},
        providers=(
            ProviderIdentity(
                role="basic",
                provider="deepseek",
                model="deepseek-chat",
                thinking="disabled",
            ),
        ),
        tool_schemas={},
        policies={"grading-metric": "grading-metric.v1"},
    )


def test_grading_candidate_can_only_become_eligible_for_a_new_holdout() -> None:
    baseline_subject = _subject("answer-grade@baseline")
    candidate_subject = _subject("answer-grade@candidate")
    plan = GradingEntailmentPlan(
        candidate_id="entailment-01",
        baseline_subject_id=baseline_subject.subject_id,
        candidate_subject_id=candidate_subject.subject_id,
        dataset_snapshot_id="holdout-03-development-gold",
        dataset_content_sha256="frozen-content",
        metric_version="grading-metric.v1",
        allowed_changes=("prompts",),
        failure_sample_ids=("fn-1", "fn-2", "fn-3", "fn-4", "fn-5"),
        protected_control_sample_ids=("control-1", "control-2"),
    )

    decision = evaluate_grading_entailment_candidate(
        plan,
        baseline=SubjectEvaluation(subject=baseline_subject, report=_report(candidate=False)),
        candidate=SubjectEvaluation(subject=candidate_subject, report=_report(candidate=True)),
    )

    assert decision.status == "eligible_for_holdout"
    assert decision.repaired_false_negatives == 5
    assert decision.new_point_errors == 0
    assert decision.new_false_positives == 0
    assert decision.valid_output_rate == 1.0
    assert decision.token_ratio == 1.1
    assert decision.evidence_class == "development_gold"
    assert decision.promotion_eligible is False


def test_grading_candidate_rejects_report_subject_prompt_drift() -> None:
    baseline_subject = _subject("answer-grade@baseline")
    candidate_subject = _subject("answer-grade@candidate")
    plan = GradingEntailmentPlan(
        candidate_id="entailment-01",
        baseline_subject_id=baseline_subject.subject_id,
        candidate_subject_id=candidate_subject.subject_id,
        dataset_snapshot_id="holdout-03-development-gold",
        dataset_content_sha256="frozen-content",
        metric_version="grading-metric.v1",
        allowed_changes=("prompts",),
        failure_sample_ids=("fn-1", "fn-2", "fn-3", "fn-4", "fn-5"),
        protected_control_sample_ids=("control-1", "control-2"),
    )
    drifted = _report(candidate=True)
    drifted = drifted.model_copy(
        update={
            "run_manifest": drifted.run_manifest.model_copy(
                update={"prompt_version": "answer-grade@unregistered"}
            )
        }
    )

    with pytest.raises(ValueError, match="prompt version does not match subject"):
        evaluate_grading_entailment_candidate(
            plan,
            baseline=SubjectEvaluation(
                subject=baseline_subject,
                report=_report(candidate=False),
            ),
            candidate=SubjectEvaluation(subject=candidate_subject, report=drifted),
        )


def test_grading_candidate_rejects_a_new_protected_false_positive() -> None:
    baseline_subject = _subject("answer-grade@baseline")
    candidate_subject = _subject("answer-grade@candidate")
    candidate_report = _report(candidate=True)
    candidate_report.results[-1] = candidate_report.results[-1].model_copy(
        update={
            "model_verdict": "对",
            "derived_verdict": "对",
            "model_matched_points": ["p1"],
            "model_missing_points": [],
            "point_correct_count": 0,
            "serious_false_positive": True,
            "verdict_agreed": False,
        }
    )
    plan = GradingEntailmentPlan(
        candidate_id="unsafe-entailment",
        baseline_subject_id=baseline_subject.subject_id,
        candidate_subject_id=candidate_subject.subject_id,
        dataset_snapshot_id="holdout-03-development-gold",
        dataset_content_sha256="frozen-content",
        metric_version="grading-metric.v1",
        allowed_changes=("prompts",),
        failure_sample_ids=("fn-1", "fn-2", "fn-3", "fn-4", "fn-5"),
        protected_control_sample_ids=("control-1", "control-2"),
    )

    decision = evaluate_grading_entailment_candidate(
        plan,
        baseline=SubjectEvaluation(subject=baseline_subject, report=_report(candidate=False)),
        candidate=SubjectEvaluation(subject=candidate_subject, report=candidate_report),
    )

    assert decision.status == "rejected"
    assert decision.new_point_errors == 1
    assert decision.new_false_positives == 1
    assert decision.promotion_eligible is False


def test_rejected_grading_experiment_does_not_modify_the_active_prompt() -> None:
    active_before = load_prompt("answer_grade")
    baseline_subject = _subject("answer-grade@baseline")
    candidate_subject = _subject("answer-grade@candidate")
    plan = GradingEntailmentPlan(
        candidate_id="losing-entailment",
        baseline_subject_id=baseline_subject.subject_id,
        candidate_subject_id=candidate_subject.subject_id,
        dataset_snapshot_id="holdout-03-development-gold",
        dataset_content_sha256="frozen-content",
        metric_version="grading-metric.v1",
        allowed_changes=("prompts",),
        failure_sample_ids=("fn-1", "fn-2", "fn-3", "fn-4", "fn-5"),
        protected_control_sample_ids=("control-1", "control-2"),
    )
    losing_report = _report(candidate=False)
    losing_report = losing_report.model_copy(
        update={
            "run_manifest": losing_report.run_manifest.model_copy(
                update={"prompt_version": "answer-grade@candidate"}
            )
        }
    )

    decision = evaluate_grading_entailment_candidate(
        plan,
        baseline=SubjectEvaluation(subject=baseline_subject, report=_report(candidate=False)),
        candidate=SubjectEvaluation(subject=candidate_subject, report=losing_report),
    )

    assert decision.status == "rejected"
    assert load_prompt("answer_grade") == active_before

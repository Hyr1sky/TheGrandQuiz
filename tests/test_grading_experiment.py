"""Comparable summaries for the fixed-cohort grading pilot."""

from grandquiz.evals.grading_calibration import (
    CalibrationRunManifest,
    GradingCalibrationPolicy,
    GradingCalibrationReport,
    GradingCalibrationResult,
    ThinkingMode,
)
from grandquiz.evals.grading_experiment import compare_grading_reports


def _report(
    *, model: str, thinking: ThinkingMode, correct: int, tokens: int
) -> GradingCalibrationReport:
    return GradingCalibrationReport(
        run_manifest=CalibrationRunManifest(
            provider="deepseek",
            endpoint_host="api.deepseek.com",
            model=model,
            thinking_mode=thinking,
            dataset_snapshot_id="snapshot-1",
            dataset_content_sha256="content-1",
            sample_ids=("sample-1",),
            prompt_version="answer_grade@v4",
        ),
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
        status="failed",
        sample_count=1,
        eligible_sample_count=1,
        exploratory_sample_count=0,
        verdict_agreement=float(correct == 2),
        point_accuracy=correct / 2,
        serious_false_negative_count=0,
        serious_false_positive_count=0,
        total_prompt_tokens=tokens - 10,
        total_completion_tokens=10,
        total_tokens=tokens,
        eligible_total_tokens=tokens,
        exploratory_total_tokens=0,
        eligible_average_tokens=float(tokens),
        results=[
            GradingCalibrationResult(
                sample_id="sample-1",
                eligible=True,
                human_verdict="对",
                human_matched_points=["a", "b"],
                human_missing_points=[],
                model_verdict="对",
                derived_verdict="对" if correct == 2 else "勉强",
                model_matched_points=["a", "b"] if correct == 2 else ["a"],
                model_missing_points=[] if correct == 2 else ["b"],
                model_diagnosis="complete" if correct == 2 else "missing_key_point",
                model_reason="reason",
                model_cited_evidence=[],
                verdict_agreed=correct == 2,
                point_correct_count=correct,
                point_count=2,
                serious_false_negative=False,
                serious_false_positive=False,
                attempts=1,
                retries=0,
                prompt_tokens=tokens - 10,
                completion_tokens=10,
                tokens=tokens,
                latency_ms=125.0,
            )
        ],
    )


def test_compare_grading_reports_summarizes_same_fixed_cohort() -> None:
    comparison = compare_grading_reports(
        [
            _report(model="deepseek-v4-flash", thinking="disabled", correct=1, tokens=100),
            _report(model="deepseek-v4-pro", thinking="enabled", correct=2, tokens=200),
        ]
    )

    assert comparison.schema_version == "grading-calibration-comparison.v1"
    assert comparison.dataset_snapshot_id == "snapshot-1"
    assert comparison.sample_ids == ("sample-1",)
    assert comparison.conditions[0].point_partition_exact_rate == 0.0
    assert comparison.conditions[1].point_partition_exact_rate == 1.0
    assert comparison.conditions[1].raw_model_verdict_agreement == 1.0
    assert comparison.conditions[1].valid_output_rate == 1.0
    assert comparison.conditions[1].average_latency_ms == 125.0
    assert comparison.conditions[1].total_tokens == 200


def test_compare_grading_reports_rejects_cohort_drift() -> None:
    first = _report(model="deepseek-v4-flash", thinking="disabled", correct=1, tokens=100)
    second = _report(model="deepseek-v4-pro", thinking="enabled", correct=2, tokens=200)
    second = second.model_copy(
        update={
            "run_manifest": second.run_manifest.model_copy(
                update={"dataset_content_sha256": "different"}
            )
        }
    )

    try:
        compare_grading_reports([first, second])
    except ValueError as exc:
        assert "fixed cohort" in str(exc)
    else:
        raise AssertionError("cohort drift must fail closed")


def test_compare_grading_reports_rejects_manifest_result_drift() -> None:
    report = _report(model="deepseek-v4-flash", thinking="disabled", correct=1, tokens=100)
    report = report.model_copy(
        update={
            "run_manifest": report.run_manifest.model_copy(update={"sample_ids": ("different",)})
        }
    )

    try:
        compare_grading_reports([report])
    except ValueError as exc:
        assert "manifest sample IDs" in str(exc)
    else:
        raise AssertionError("manifest/result drift must fail closed")

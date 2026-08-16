"""Human-approval boundary for semantic quality calibration packs."""

import pytest

from grandquiz.evals.quality_dataset import (
    QualityCalibrationPackError,
    compile_quality_calibration_pack,
)

_CRITERIA = (
    "evidence_support",
    "demand_alignment",
    "answer_leakage",
    "response_design",
    "learning_usefulness",
)


def _adjudicated_pack() -> dict[str, object]:
    boundaries = (
        ("good", "multiple_choice"),
        ("partial", "open_response"),
        ("leaked", "multiple_choice"),
        ("unsupported", "open_response"),
        ("misleading", "multiple_choice"),
    )
    return {
        "schema_version": "quality-calibration-pack.v1",
        "pack_id": "question-quality-development-gold-01",
        "rubric_id": "question_quality",
        "evidence_class": "development_gold",
        "label_status": "human_adjudicated",
        "annotator": "owner",
        "adjudicated_at": "2026-08-17",
        "blind_to_judge_output": True,
        "samples": [
            {
                "sample_id": f"{boundary}-{question_format}",
                "boundary": boundary,
                "question_format": question_format,
                "question": f"Review the {boundary} question.",
                "candidate": f"Candidate QuestionSpec for {boundary}.",
                "reference": "AgentEvent contains a type, metadata and opaque payload.",
                "expected_scores": {criterion: {"min": 3, "max": 4} for criterion in _CRITERIA},
            }
            for boundary, question_format in boundaries
        ],
    }


def test_proposed_question_quality_labels_cannot_enter_calibration() -> None:
    proposed: dict[str, object] = {
        "schema_version": "quality-calibration-pack.v1",
        "pack_id": "question-quality-development-gold-01",
        "rubric_id": "question_quality",
        "evidence_class": "development_gold",
        "label_status": "proposed",
        "annotator": "owner",
        "adjudicated_at": "2026-08-17",
        "blind_to_judge_output": True,
        "samples": [],
    }

    with pytest.raises(QualityCalibrationPackError, match="human-adjudicated"):
        compile_quality_calibration_pack(proposed)


def test_owner_adjudicated_question_boundaries_compile_deterministically() -> None:
    compiled = compile_quality_calibration_pack(_adjudicated_pack())

    assert compiled.schema_version == "compiled-quality-calibration.v1"
    assert compiled.evidence_class == "development_gold"
    assert compiled.boundaries == (
        "good",
        "partial",
        "leaked",
        "unsupported",
        "misleading",
    )
    assert tuple(sample.rubric_id for sample in compiled.samples) == ("question_quality",) * 5
    assert len(compiled.content_sha256) == 64


def test_question_quality_pack_requires_all_preregistered_boundaries_and_formats() -> None:
    incomplete = _adjudicated_pack()
    samples = incomplete["samples"]
    assert isinstance(samples, list)
    samples.pop()

    with pytest.raises(QualityCalibrationPackError, match="all five boundary categories"):
        compile_quality_calibration_pack(incomplete)

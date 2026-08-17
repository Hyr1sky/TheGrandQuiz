"""Human-approval boundary for semantic quality calibration packs."""

import pytest

from grandquiz.evals.quality_dataset import (
    QualityCalibrationPackError,
    compile_quality_calibration_pack,
    load_grounded_answer_development_gold,
    load_question_quality_development_gold,
    load_reader_fidelity_development_gold,
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

    with pytest.raises(QualityCalibrationPackError, match="registered boundary categories"):
        compile_quality_calibration_pack(incomplete)


def test_repository_question_quality_development_gold_is_frozen() -> None:
    compiled = load_question_quality_development_gold()

    assert compiled.pack_id == "question-quality-development-gold-01"
    assert compiled.evidence_class == "development_gold"
    assert compiled.annotator == "owner"
    assert compiled.adjudicated_at == "2026-08-17"
    assert compiled.boundaries == (
        "good",
        "partial",
        "leaked",
        "unsupported",
        "misleading",
    )
    assert tuple(sample.sample_id for sample in compiled.samples) == (
        "good-mc",
        "partial-open",
        "leaked-mc",
        "unsupported-open",
        "misleading-mc",
    )
    assert compiled.content_sha256 == (
        "75255afba51b1a841b36315fdd1cadbb09c66c14727530384649f5a434c4a2cc"
    )


def test_reader_fidelity_pack_uses_the_same_fail_closed_compiler() -> None:
    criteria = (
        "source_fidelity",
        "key_concept_coverage",
        "concept_separation",
        "evidence_locality",
        "learning_usefulness",
    )
    boundaries = (
        "supported_item",
        "missing_key_concept",
        "duplicate_concept",
        "pseudo_item",
        "cross_node_evidence",
    )
    raw = {
        "schema_version": "quality-calibration-pack.v1",
        "pack_id": "reader-fidelity-development-gold-01",
        "rubric_id": "reader_fidelity",
        "evidence_class": "development_gold",
        "label_status": "human_adjudicated",
        "annotator": "owner",
        "adjudicated_at": "2026-08-17",
        "blind_to_judge_output": True,
        "samples": [
            {
                "sample_id": boundary,
                "boundary": boundary,
                "sample_kind": "knowledge_item",
                "question": "Review this extracted KnowledgeItem.",
                "candidate": f"KnowledgeItem: {boundary}",
                "reference": "AgentEvent is an event envelope.",
                "expected_scores": {
                    criterion_id: {"min": 3, "max": 4} for criterion_id in criteria
                },
            }
            for boundary in boundaries
        ],
    }

    compiled = compile_quality_calibration_pack(raw)

    assert compiled.rubric_id == "reader_fidelity"
    assert compiled.boundaries == boundaries
    assert compiled.sample_kinds == ("knowledge_item",) * 5


def test_repository_reader_and_answer_development_gold_are_frozen() -> None:
    reader = load_reader_fidelity_development_gold()
    answer = load_grounded_answer_development_gold()

    assert reader.pack_id == "reader-fidelity-development-gold-01"
    assert reader.rubric_id == "reader_fidelity"
    assert reader.evidence_class == "development_gold"
    assert reader.boundaries == (
        "supported_item",
        "missing_key_concept",
        "duplicate_concept",
        "pseudo_item",
        "cross_node_evidence",
    )
    assert reader.content_sha256 == (
        "a63f6d96f4edf3d1bb4278072a6060d5266a7992d56c3c06182a69392a102aad"
    )

    assert answer.pack_id == "grounded-answer-development-gold-02"
    assert answer.rubric_id == "grounded_answer"
    assert answer.evidence_class == "development_gold"
    assert answer.boundaries == (
        "multi_material_scope",
        "justified_refusal",
        "conflicting_evidence",
        "bilingual_wording",
        "incomplete_supported",
    )
    assert answer.content_sha256 == (
        "387ec84a10e89d3f1399ea96132733c26b0315284690fc20ef5951718998e415"
    )

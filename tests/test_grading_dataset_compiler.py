"""Frozen curator artifacts become an approved, immutable calibration dataset."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.evals.grading_dataset import (
    GradingDatasetConflict,
    compile_grading_dataset,
    promote_grading_dataset,
)
from grandquiz.interfaces.cli.commands.calibration import prepare_grading_calibration_cli


def _dump(path: Path, value: object) -> str:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_pack(root: Path) -> Path:
    root.mkdir()
    question_path = root / "questions.yaml"
    question_hash = _dump(
        question_path,
        {
            "schema_version": "grading-question-pack.v1",
            "pack_id": "pack-1",
            "spec_status": "candidate_locked_for_blind_response",
            "grader_has_run": False,
            "questions": [
                {
                    "sample_id": "eligible",
                    "question": {
                        "question": "什么是有界循环？",
                        "expected_points": [
                            {
                                "point_id": "stop",
                                "description": "存在停止条件",
                                "cited_evidence": "循环需要停止条件。",
                            }
                        ],
                        "reference_answer": "循环必须有停止条件。",
                        "cited_evidence": ["循环需要停止条件。"],
                    },
                },
                {
                    "sample_id": "excluded",
                    "question": {
                        "question": "如何实现检索？",
                        "expected_points": [
                            {
                                "point_id": "specific_stack",
                                "description": "必须使用某一套组件",
                                "cited_evidence": "可采用多种检索实现。",
                            }
                        ],
                        "reference_answer": "按约束选择检索实现。",
                        "cited_evidence": ["可采用多种检索实现。"],
                    },
                },
            ],
        },
    )
    response_path = root / "responses.yaml"
    response_hash = _dump(
        response_path,
        {
            "schema_version": "grading-responses.v1",
            "respondent_id": "human-1",
            "source_pack": "pack-1@commit",
            "question_spec_sha256": question_hash,
            "closed_book": True,
            "rubric_seen_before_submission": False,
            "submitted_at": "2026-08-02T00:00:00+08:00",
            "responses": [
                {"sample_id": "eligible", "answer": "设置最大轮次后停止。"},
                {"sample_id": "excluded", "answer": "根据约束选稀疏或稠密检索。"},
            ],
        },
    )
    annotation_path = root / "annotations.yaml"
    annotation_hash = _dump(
        annotation_path,
        {
            "schema_version": "grading-annotations.v1",
            "question_spec_sha256": question_hash,
            "annotator": "owner",
            "blind_to_model_output": True,
            "grader_has_run": False,
            "label_status": "human_adjudicated",
            "adjudicated_at": "2026-08-03T00:00:00+08:00",
            "annotations": [
                {
                    "sample_id": "eligible",
                    "respondent_id": "human-1",
                    "human_verdict": "对",
                    "point_labels": {"stop": "matched"},
                    "annotation_reason": "命中停止条件。",
                    "exclude_reason": None,
                },
                {
                    "sample_id": "excluded",
                    "respondent_id": "human-1",
                    "human_verdict": "勉强",
                    "point_labels": {"specific_stack": "missing"},
                    "annotation_reason": "答案合理但不匹配过窄 rubric。",
                    "exclude_reason": "rubric overconstraint",
                },
            ],
        },
    )
    _dump(
        root / "calibration-manifest.yaml",
        {
            "schema_version": "grading-source-manifest.v1",
            "pack_id": "pack-1",
            "question_specs": {"file": question_path.name, "sha256": question_hash},
            "responses": [
                {
                    "respondent_id": "human-1",
                    "file": response_path.name,
                    "sha256": response_hash,
                }
            ],
            "annotations": {"file": annotation_path.name, "sha256": annotation_hash},
        },
    )
    return root


def test_compiler_validates_frozen_artifacts_and_excludes_disputed_rubric(
    tmp_path: Path,
) -> None:
    compilation = compile_grading_dataset(_write_pack(tmp_path / "pack"))

    assert compilation.pack_id == "pack-1"
    assert [sample.sample_id for sample in compilation.samples] == ["eligible"]
    assert compilation.samples[0].human_matched_points == ["stop"]
    assert compilation.samples[0].human_missing_points == []
    assert compilation.samples[0].resolved_question_id == "eligible"
    assert "question_id" not in compilation.samples[0].model_dump(mode="json")
    assert compilation.excluded_sample_count == 1
    assert compilation.exclusions[0].sample_id == "excluded"
    assert compilation.content_sha256


def test_compiler_preserves_model_answer_provenance_as_exploratory(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "pack")
    response_path = pack / "responses.yaml"
    responses = yaml.safe_load(response_path.read_text(encoding="utf-8"))
    responses["answer_provenance"] = "model"
    responses["respondent_model"] = "deepseek-v4-pro"
    response_hash = _dump(response_path, responses)
    manifest_path = pack / "calibration-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["responses"][0]["sha256"] = response_hash
    _dump(manifest_path, manifest)

    compilation = compile_grading_dataset(pack)

    assert compilation.samples[0].answer_provenance == "model"
    assert compilation.samples[0].respondent_model == "deepseek-v4-pro"
    assert compilation.samples[0].eligible is False


def test_compiler_keeps_provenance_attached_to_each_response_source(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "pack")
    human_path = pack / "responses.yaml"
    human = yaml.safe_load(human_path.read_text(encoding="utf-8"))
    model = dict(human)
    human["responses"] = [human["responses"][0]]
    human_hash = _dump(human_path, human)
    model["respondent_id"] = "model-1"
    model["answer_provenance"] = "model"
    model["respondent_model"] = "deepseek-v4-pro"
    model["responses"] = [model["responses"][1]]
    model_path = pack / "responses-model.yaml"
    model_hash = _dump(model_path, model)

    annotation_path = pack / "annotations.yaml"
    annotations = yaml.safe_load(annotation_path.read_text(encoding="utf-8"))
    annotations["annotations"][1]["respondent_id"] = "model-1"
    annotations["annotations"][1]["exclude_reason"] = None
    annotation_hash = _dump(annotation_path, annotations)
    manifest_path = pack / "calibration-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["responses"] = [
        {"respondent_id": "human-1", "file": human_path.name, "sha256": human_hash},
        {"respondent_id": "model-1", "file": model_path.name, "sha256": model_hash},
    ]
    manifest["annotations"]["sha256"] = annotation_hash
    _dump(manifest_path, manifest)

    compilation = compile_grading_dataset(pack)
    samples = {sample.sample_id: sample for sample in compilation.samples}

    assert samples["eligible"].answer_provenance == "unassisted_human"
    assert samples["eligible"].eligible is True
    assert samples["excluded"].answer_provenance == "model"
    assert samples["excluded"].respondent_model == "deepseek-v4-pro"
    assert samples["excluded"].eligible is False


def test_compiler_separates_question_identity_from_independent_answer_identity(
    tmp_path: Path,
) -> None:
    pack = _write_pack(tmp_path / "pack")
    first_path = pack / "responses.yaml"
    first = yaml.safe_load(first_path.read_text(encoding="utf-8"))
    first["responses"] = [
        {
            "sample_id": "answer-a",
            "question_id": "eligible",
            "answer": "设置最大轮次后停止。",
        },
        {
            "sample_id": "answer-c",
            "question_id": "excluded",
            "answer": "根据约束选稀疏或稠密检索。",
        },
    ]
    first_hash = _dump(first_path, first)

    second = dict(first)
    second["respondent_id"] = "human-2"
    second["responses"] = [
        {
            "sample_id": "answer-b",
            "question_id": "eligible",
            "answer": "达到预先设定的停止条件就退出。",
        }
    ]
    second_path = pack / "responses-human-2.yaml"
    second_hash = _dump(second_path, second)

    annotation_path = pack / "annotations.yaml"
    annotations = yaml.safe_load(annotation_path.read_text(encoding="utf-8"))
    annotations["annotations"] = [
        {
            "sample_id": "answer-a",
            "respondent_id": "human-1",
            "human_verdict": "对",
            "point_labels": {"stop": "matched"},
            "annotation_reason": "命中停止条件。",
            "exclude_reason": None,
        },
        {
            "sample_id": "answer-b",
            "respondent_id": "human-2",
            "human_verdict": "对",
            "point_labels": {"stop": "matched"},
            "annotation_reason": "命中停止条件。",
            "exclude_reason": None,
        },
        {
            "sample_id": "answer-c",
            "respondent_id": "human-1",
            "human_verdict": "勉强",
            "point_labels": {"specific_stack": "missing"},
            "annotation_reason": "答案合理但不匹配过窄 rubric。",
            "exclude_reason": "rubric overconstraint",
        },
    ]
    annotation_hash = _dump(annotation_path, annotations)

    manifest_path = pack / "calibration-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["responses"] = [
        {"respondent_id": "human-1", "file": first_path.name, "sha256": first_hash},
        {
            "respondent_id": "human-2",
            "file": second_path.name,
            "sha256": second_hash,
        },
    ]
    manifest["annotations"]["sha256"] = annotation_hash
    _dump(manifest_path, manifest)

    compilation = compile_grading_dataset(pack)

    assert [sample.sample_id for sample in compilation.samples] == ["answer-a", "answer-b"]
    assert [sample.question_id for sample in compilation.samples] == ["eligible", "eligible"]
    assert compilation.exclusions[0].sample_id == "answer-c"
    assert compilation.exclusions[0].question_id == "excluded"


def test_compiler_fails_closed_when_a_frozen_source_hash_changes(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "pack")
    response_path = pack / "responses.yaml"
    response_path.write_text(response_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(GradingDatasetConflict, match="SHA-256"):
        compile_grading_dataset(pack)


def test_compiler_requires_final_human_adjudication(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "pack")
    annotation_path = pack / "annotations.yaml"
    raw = yaml.safe_load(annotation_path.read_text(encoding="utf-8"))
    raw["label_status"] = "assistant_prefilled_pending_human_review"
    annotation_hash = _dump(annotation_path, raw)
    manifest_path = pack / "calibration-manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["annotations"]["sha256"] = annotation_hash
    _dump(manifest_path, manifest)

    with pytest.raises(GradingDatasetConflict, match="human_adjudicated"):
        compile_grading_dataset(pack)


def test_promotion_imports_reviews_and_freezes_only_eligible_samples(tmp_path: Path) -> None:
    compilation = compile_grading_dataset(_write_pack(tmp_path / "pack"))
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        snapshot = promote_grading_dataset(
            compilation,
            inbox=persistence.eval_inbox,
            request_id="promotion-1",
            reviewer="owner",
            review_reason="已确认样本仅含本轮答题内容，无密钥或个人身份信息。",
            now=100.0,
        )
        replayed = promote_grading_dataset(
            compilation,
            inbox=persistence.eval_inbox,
            request_id="promotion-1",
            reviewer="owner",
            review_reason="已确认样本仅含本轮答题内容，无密钥或个人身份信息。",
            now=200.0,
        )

    assert snapshot == replayed
    assert snapshot.candidate_count == 1
    assert snapshot.eligible_blind_count == 1
    assert snapshot.exploratory_count == 0
    assert snapshot.items[0].review_reason.startswith("owner: ")


def test_prepare_command_exports_compilation_samples_and_snapshot(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "pack")
    out = tmp_path / "out"

    snapshot = prepare_grading_calibration_cli(
        pack_dir=pack,
        db_path=tmp_path / "learning.db",
        out_dir=out,
        reviewer="owner",
        review_reason="local privacy review complete",
        request_id=None,
        now=123.0,
    )

    assert (out / "compilation.json").is_file()
    assert (out / "grading-samples.yaml").is_file()
    assert (out / "dataset-snapshot.json").is_file()
    assert snapshot.eligible_blind_count == 1

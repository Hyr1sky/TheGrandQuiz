"""Compile frozen human-curation artifacts into governed grading samples.

The curator-owned YAML files are evidence inputs, not another runtime store.  This
module validates their integrity and translates them into the existing
``GradingCalibrationSample`` and Eval Inbox contracts without invoking a model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.question import QuestionSpec
from grandquiz.domain.learning.eval_inbox import DatasetSnapshotV1, EvalInboxLedger
from grandquiz.domain.learning.grading_samples import (
    AnswerProvenance,
    GradingCalibrationSample,
)

PointLabel = Literal["matched", "missing"]


class GradingDatasetConflict(ValueError):
    """Frozen artifacts disagree or have not crossed the human-approval boundary."""


class _HasSampleId(Protocol):
    sample_id: str


class _SourceFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ResponseSource(_SourceFile):
    respondent_id: str = Field(min_length=1)


class _SourceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["grading-source-manifest.v1"]
    pack_id: str = Field(min_length=1)
    question_specs: _SourceFile
    responses: tuple[_ResponseSource, ...] = Field(min_length=1)
    annotations: _SourceFile


class _QuestionEntry(BaseModel):
    sample_id: str = Field(min_length=1)
    question: QuestionSpec


class _QuestionPack(BaseModel):
    schema_version: Literal["grading-question-pack.v1"]
    pack_id: str = Field(min_length=1)
    spec_status: Literal["candidate_locked_for_blind_response"]
    grader_has_run: Literal[False]
    questions: tuple[_QuestionEntry, ...] = Field(min_length=1)


class _ResponseEntry(BaseModel):
    sample_id: str = Field(min_length=1)
    question_id: str | None = Field(default=None, min_length=1)
    answer: str = Field(min_length=1)


class _ResponsePack(BaseModel):
    schema_version: Literal["grading-responses.v1"]
    respondent_id: str = Field(min_length=1)
    source_pack: str = Field(min_length=1)
    question_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    closed_book: Literal[True]
    rubric_seen_before_submission: Literal[False]
    answer_provenance: AnswerProvenance = "unassisted_human"
    respondent_model: str | None = None
    responses: tuple[_ResponseEntry, ...] = Field(min_length=1)


class _AnnotationEntry(BaseModel):
    sample_id: str = Field(min_length=1)
    respondent_id: str = Field(min_length=1)
    human_verdict: VerdictLabel
    point_labels: dict[str, PointLabel]
    annotation_reason: str = Field(min_length=1)
    exclude_reason: str | None


class _AnnotationPack(BaseModel):
    schema_version: Literal["grading-annotations.v1"]
    question_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    annotator: str = Field(min_length=1)
    blind_to_model_output: Literal[True]
    grader_has_run: Literal[False]
    label_status: Literal["human_adjudicated"]
    adjudicated_at: str = Field(min_length=1)
    annotations: tuple[_AnnotationEntry, ...] = Field(min_length=1)


class RubricExclusionV1(BaseModel):
    """A human-adjudicated sample withheld because its rubric is not trustworthy."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["rubric-exclusion.v1"] = "rubric-exclusion.v1"
    sample_id: str
    question_id: str | None = Field(
        default=None,
        min_length=1,
        exclude_if=lambda value: value is None,
    )
    respondent_id: str
    reason: str


class GradingDatasetCompilationV1(BaseModel):
    """Pure compiler result ready for explicit Eval Inbox promotion."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["grading-dataset-compilation.v1"] = "grading-dataset-compilation.v1"
    pack_id: str
    manifest_sha256: str
    question_spec_sha256: str
    source_sha256: dict[str, str]
    annotator: str
    adjudicated_at: str
    samples: tuple[GradingCalibrationSample, ...]
    exclusions: tuple[RubricExclusionV1, ...]
    content_sha256: str

    @property
    def eligible_sample_count(self) -> int:
        return len(self.samples)

    @property
    def excluded_sample_count(self) -> int:
        return len(self.exclusions)


def compile_grading_dataset(
    pack_dir: str | Path,
    *,
    manifest_name: str = "calibration-manifest.yaml",
) -> GradingDatasetCompilationV1:
    """Validate one frozen curator pack and compile its eligible blind samples."""

    root = Path(pack_dir).resolve()
    manifest_path = _source_path(root, manifest_name)
    manifest = _SourceManifest.model_validate(_load_yaml(manifest_path))
    manifest_hash = _sha256(manifest_path)

    question_path = _checked_source(root, manifest.question_specs)
    question_pack = _QuestionPack.model_validate(_load_yaml(question_path))
    if question_pack.pack_id != manifest.pack_id:
        raise GradingDatasetConflict("question pack_id does not match source manifest")
    question_hash = manifest.question_specs.sha256

    questions = _unique_by_id(question_pack.questions, label="question")
    responses: dict[
        str,
        tuple[_ResponseEntry, str, AnswerProvenance, str | None],
    ] = {}
    source_hashes = {manifest.question_specs.file: question_hash}
    seen_respondents: set[str] = set()
    for source in manifest.responses:
        if source.respondent_id in seen_respondents:
            raise GradingDatasetConflict("source manifest repeats a respondent_id")
        seen_respondents.add(source.respondent_id)
        path = _checked_source(root, source)
        source_hashes[source.file] = source.sha256
        pack = _ResponsePack.model_validate(_load_yaml(path))
        if pack.respondent_id != source.respondent_id:
            raise GradingDatasetConflict(f"respondent_id mismatch in {source.file}")
        if not pack.source_pack.startswith(f"{manifest.pack_id}@"):
            raise GradingDatasetConflict(f"source_pack mismatch in {source.file}")
        if pack.question_spec_sha256 != question_hash:
            raise GradingDatasetConflict(f"question spec hash mismatch in {source.file}")
        for entry in pack.responses:
            if entry.sample_id in responses:
                raise GradingDatasetConflict(f"response sample_id repeated: {entry.sample_id}")
            responses[entry.sample_id] = (
                entry,
                pack.respondent_id,
                pack.answer_provenance,
                pack.respondent_model,
            )

    annotation_path = _checked_source(root, manifest.annotations)
    source_hashes[manifest.annotations.file] = manifest.annotations.sha256
    raw_annotations_value = _load_yaml(annotation_path)
    if not isinstance(raw_annotations_value, dict):
        raise GradingDatasetConflict("annotation YAML must contain a mapping")
    raw_annotations = cast("dict[str, object]", raw_annotations_value)
    if raw_annotations.get("label_status") != ("human_adjudicated"):
        raise GradingDatasetConflict("annotation label_status must be human_adjudicated")
    annotation_pack = _AnnotationPack.model_validate(raw_annotations)
    if annotation_pack.question_spec_sha256 != question_hash:
        raise GradingDatasetConflict("annotation question spec hash does not match manifest")
    annotations = _unique_by_id(annotation_pack.annotations, label="annotation")

    question_ids = set(questions)
    answered_question_ids = {
        response.question_id or response.sample_id for response, _, _, _ in responses.values()
    }
    unknown_question_ids = answered_question_ids - question_ids
    if unknown_question_ids:
        unknown = ", ".join(sorted(unknown_question_ids))
        raise GradingDatasetConflict(f"responses reference unknown question_id: {unknown}")
    if answered_question_ids != question_ids:
        raise GradingDatasetConflict("responses must cover every question at least once")
    response_ids = set(responses)
    if set(annotations) != response_ids:
        raise GradingDatasetConflict("annotations must cover every response exactly once")

    samples: list[GradingCalibrationSample] = []
    exclusions: list[RubricExclusionV1] = []
    for sample_id in sorted(response_ids):
        response, respondent_id, answer_provenance, respondent_model = responses[sample_id]
        question_id = response.question_id or sample_id
        question = questions[question_id].question
        annotation = annotations[sample_id]
        if annotation.respondent_id != respondent_id:
            raise GradingDatasetConflict(f"annotation respondent mismatch for {sample_id}")
        point_ids = {point.point_id for point in question.expected_points}
        if set(annotation.point_labels) != point_ids:
            raise GradingDatasetConflict(
                f"annotation point labels must exactly cover the rubric for {sample_id}"
            )
        if annotation.exclude_reason is not None:
            reason = annotation.exclude_reason.strip()
            if not reason:
                raise GradingDatasetConflict(f"blank exclusion reason for {sample_id}")
            exclusions.append(
                RubricExclusionV1(
                    sample_id=sample_id,
                    question_id=response.question_id,
                    respondent_id=respondent_id,
                    reason=reason,
                )
            )
            continue
        matched = sorted(
            point_id for point_id, label in annotation.point_labels.items() if label == "matched"
        )
        missing = sorted(point_ids - set(matched))
        samples.append(
            GradingCalibrationSample(
                sample_id=sample_id,
                question_id=response.question_id,
                annotator=annotation_pack.annotator,
                blind_to_model_output=True,
                answer_provenance=answer_provenance,
                respondent_model=respondent_model,
                question=question,
                learner_answer=response.answer,
                human_verdict=annotation.human_verdict,
                human_matched_points=matched,
                human_missing_points=missing,
            )
        )

    canonical_body = {
        "schema_version": "grading-dataset-compilation.v1",
        "pack_id": manifest.pack_id,
        "manifest_sha256": manifest_hash,
        "question_spec_sha256": question_hash,
        "source_sha256": dict(sorted(source_hashes.items())),
        "annotator": annotation_pack.annotator,
        "adjudicated_at": annotation_pack.adjudicated_at,
        "samples": [sample.model_dump(mode="json") for sample in samples],
        "exclusions": [item.model_dump(mode="json") for item in exclusions],
    }
    content_hash = hashlib.sha256(_canonical_json(canonical_body).encode("utf-8")).hexdigest()
    return GradingDatasetCompilationV1(
        pack_id=manifest.pack_id,
        manifest_sha256=manifest_hash,
        question_spec_sha256=question_hash,
        source_sha256=dict(sorted(source_hashes.items())),
        annotator=annotation_pack.annotator,
        adjudicated_at=annotation_pack.adjudicated_at,
        samples=tuple(samples),
        exclusions=tuple(exclusions),
        content_sha256=content_hash,
    )


def promote_grading_dataset(
    compilation: GradingDatasetCompilationV1,
    *,
    inbox: EvalInboxLedger,
    request_id: str,
    reviewer: str,
    review_reason: str,
    now: float,
) -> DatasetSnapshotV1:
    """Import, privacy-approve, and freeze one compiled dataset idempotently."""

    normalized_reviewer = reviewer.strip()
    normalized_reason = review_reason.strip()
    if not normalized_reviewer or not normalized_reason:
        raise ValueError("reviewer and review_reason must not be blank")
    imported = inbox.import_blind_labels(
        list(compilation.samples),
        request_id=request_id,
        now=now,
    )
    approved = [
        inbox.review(
            candidate.candidate_id,
            request_id=f"{request_id}:privacy:{candidate.dedupe_key}",
            decision="approved",
            reason=f"{normalized_reviewer}: {normalized_reason}",
            now=now,
        )
        for candidate in imported
    ]
    return inbox.build_snapshot(
        [candidate.candidate_id for candidate in approved],
        now=now,
    )


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _checked_source(root: Path, source: _SourceFile) -> Path:
    path = _source_path(root, source.file)
    if _sha256(path) != source.sha256:
        raise GradingDatasetConflict(f"SHA-256 mismatch for {source.file}")
    return path


def _source_path(root: Path, filename: str) -> Path:
    path = (root / filename).resolve()
    if path.parent != root:
        raise GradingDatasetConflict("source files must be direct children of the pack directory")
    if not path.is_file():
        raise GradingDatasetConflict(f"source file does not exist: {filename}")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique_by_id[T: _HasSampleId](items: tuple[T, ...], *, label: str) -> dict[str, T]:
    result: dict[str, T] = {}
    for item in items:
        sample_id = item.sample_id
        if sample_id in result:
            raise GradingDatasetConflict(f"{label} sample_id repeated: {sample_id}")
        result[sample_id] = item
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

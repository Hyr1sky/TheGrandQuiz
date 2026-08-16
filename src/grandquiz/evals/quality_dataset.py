"""Compile owner-adjudicated semantic-quality labels into calibration samples."""

from __future__ import annotations

import hashlib
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grandquiz.evals.quality_contracts import CalibrationSample, ScoreRange
from grandquiz.evals.rubrics import get_rubric

QuestionBoundary = Literal["good", "partial", "leaked", "unsupported", "misleading"]
QuestionFormat = Literal["multiple_choice", "open_response"]
_QUESTION_BOUNDARIES = {"good", "partial", "leaked", "unsupported", "misleading"}
_QUESTION_FORMATS = {"multiple_choice", "open_response"}


class QualityCalibrationPackError(ValueError):
    """A proposed, malformed, or incomplete human label pack cannot calibrate a judge."""


class _PackSample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str = Field(min_length=1)
    boundary: QuestionBoundary
    question_format: QuestionFormat
    question: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    expected_scores: dict[str, ScoreRange] = Field(min_length=1)


class _AdjudicatedPack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["quality-calibration-pack.v1"]
    pack_id: str = Field(min_length=1)
    rubric_id: Literal["question_quality"]
    evidence_class: Literal["development_gold"]
    label_status: Literal["human_adjudicated"]
    annotator: str = Field(min_length=1)
    adjudicated_at: str = Field(min_length=1)
    blind_to_judge_output: Literal[True]
    samples: tuple[_PackSample, ...] = Field(min_length=1)


class CompiledQualityCalibration(BaseModel):
    """Frozen, non-secret input ready for a separately traced judge calibration."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["compiled-quality-calibration.v1"] = "compiled-quality-calibration.v1"
    pack_id: str
    rubric_id: str
    evidence_class: Literal["development_gold"]
    annotator: str
    adjudicated_at: str
    content_sha256: str
    boundaries: tuple[QuestionBoundary, ...]
    samples: tuple[CalibrationSample, ...]


def compile_quality_calibration_pack(raw: object) -> CompiledQualityCalibration:
    """Fail closed until the owner has explicitly adjudicated every score boundary."""

    if not isinstance(raw, dict):
        raise QualityCalibrationPackError(
            "quality calibration labels must be human-adjudicated before use"
        )
    raw_mapping = cast("dict[str, object]", raw)
    if raw_mapping.get("label_status") != "human_adjudicated":
        raise QualityCalibrationPackError(
            "quality calibration labels must be human-adjudicated before use"
        )
    try:
        pack = _AdjudicatedPack.model_validate(raw_mapping)
    except ValidationError as exc:
        raise QualityCalibrationPackError(f"invalid quality calibration pack: {exc}") from exc
    rubric = get_rubric(pack.rubric_id)
    if rubric is None:
        raise QualityCalibrationPackError(f"unknown quality rubric: {pack.rubric_id}")
    expected_criteria = {criterion.criterion_id for criterion in rubric.criteria}
    sample_ids = [sample.sample_id for sample in pack.samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise QualityCalibrationPackError("quality calibration sample ids must be unique")
    if {sample.boundary for sample in pack.samples} != _QUESTION_BOUNDARIES:
        raise QualityCalibrationPackError(
            "question quality pack must cover all five boundary categories"
        )
    if {sample.question_format for sample in pack.samples} != _QUESTION_FORMATS:
        raise QualityCalibrationPackError(
            "question quality pack must cover multiple-choice and open-response formats"
        )
    for sample in pack.samples:
        if set(sample.expected_scores) != expected_criteria:
            raise QualityCalibrationPackError(
                f"{sample.sample_id} scores must exactly cover the rubric criteria"
            )
    content = pack.model_dump_json(exclude_none=True)
    return CompiledQualityCalibration(
        pack_id=pack.pack_id,
        rubric_id=pack.rubric_id,
        evidence_class=pack.evidence_class,
        annotator=pack.annotator,
        adjudicated_at=pack.adjudicated_at,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        boundaries=tuple(sample.boundary for sample in pack.samples),
        samples=tuple(
            CalibrationSample(
                sample_id=sample.sample_id,
                rubric_id=pack.rubric_id,
                question=sample.question,
                candidate=sample.candidate,
                reference=sample.reference,
                expected_scores=sample.expected_scores,
            )
            for sample in pack.samples
        ),
    )

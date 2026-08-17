"""Compile owner-adjudicated semantic-quality labels into calibration samples."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from grandquiz.evals.quality_contracts import CalibrationSample, ScoreRange
from grandquiz.evals.rubrics import get_rubric

_QUALITY_CASES_DIR = Path(__file__).parent / "quality_cases"


@dataclass(frozen=True)
class _PackPolicy:
    rubric_id: str
    filename: str
    boundaries: frozenset[str]
    sample_kinds: frozenset[str]


_PACK_POLICIES = {
    "question-quality-development-gold-01": _PackPolicy(
        rubric_id="question_quality",
        filename="question_quality.yaml",
        boundaries=frozenset({"good", "partial", "leaked", "unsupported", "misleading"}),
        sample_kinds=frozenset({"multiple_choice", "open_response"}),
    ),
    "reader-fidelity-development-gold-01": _PackPolicy(
        rubric_id="reader_fidelity",
        filename="reader_fidelity.yaml",
        boundaries=frozenset(
            {
                "supported_item",
                "missing_key_concept",
                "duplicate_concept",
                "pseudo_item",
                "cross_node_evidence",
            }
        ),
        sample_kinds=frozenset({"knowledge_item"}),
    ),
    "grounded-answer-development-gold-02": _PackPolicy(
        rubric_id="grounded_answer",
        filename="grounded_answer_slices.yaml",
        boundaries=frozenset(
            {
                "multi_material_scope",
                "justified_refusal",
                "conflicting_evidence",
                "bilingual_wording",
                "incomplete_supported",
            }
        ),
        sample_kinds=frozenset({"grounded_answer"}),
    ),
}


class QualityCalibrationPackError(ValueError):
    """A proposed, malformed, or incomplete human label pack cannot calibrate a judge."""


class _PackSample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str = Field(min_length=1)
    boundary: str = Field(min_length=1)
    question_format: str | None = Field(default=None, min_length=1)
    sample_kind: str | None = Field(default=None, min_length=1)
    question: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    expected_scores: dict[str, ScoreRange] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_sample_kind(self) -> Self:
        if (self.question_format is None) == (self.sample_kind is None):
            raise ValueError("sample must declare exactly one of question_format or sample_kind")
        return self

    @property
    def normalized_kind(self) -> str:
        return self.question_format or self.sample_kind or ""


class _AdjudicatedPack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["quality-calibration-pack.v1"]
    pack_id: str = Field(min_length=1)
    rubric_id: str = Field(min_length=1)
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
    boundaries: tuple[str, ...]
    sample_kinds: tuple[str, ...]
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
    pack_id = raw_mapping.get("pack_id")
    policy = _PACK_POLICIES.get(pack_id) if isinstance(pack_id, str) else None
    if policy is None:
        raise QualityCalibrationPackError(f"unregistered quality calibration pack: {pack_id!r}")
    try:
        pack = _AdjudicatedPack.model_validate(raw_mapping)
    except ValidationError as exc:
        raise QualityCalibrationPackError(f"invalid quality calibration pack: {exc}") from exc
    if pack.rubric_id != policy.rubric_id:
        raise QualityCalibrationPackError(
            f"{pack.pack_id} must target the registered rubric {policy.rubric_id}"
        )
    rubric = get_rubric(pack.rubric_id)
    if rubric is None:
        raise QualityCalibrationPackError(f"unknown quality rubric: {pack.rubric_id}")
    expected_criteria = {criterion.criterion_id for criterion in rubric.criteria}
    sample_ids = [sample.sample_id for sample in pack.samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise QualityCalibrationPackError("quality calibration sample ids must be unique")
    if frozenset(sample.boundary for sample in pack.samples) != policy.boundaries:
        raise QualityCalibrationPackError(
            f"{pack.pack_id} must cover all registered boundary categories"
        )
    if frozenset(sample.normalized_kind for sample in pack.samples) != policy.sample_kinds:
        raise QualityCalibrationPackError(f"{pack.pack_id} must cover all registered sample kinds")
    for sample in pack.samples:
        if set(sample.expected_scores) != expected_criteria:
            raise QualityCalibrationPackError(
                f"{sample.sample_id} scores must exactly cover the rubric criteria"
            )
    content = json.dumps(
        pack.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return CompiledQualityCalibration(
        pack_id=pack.pack_id,
        rubric_id=pack.rubric_id,
        evidence_class=pack.evidence_class,
        annotator=pack.annotator,
        adjudicated_at=pack.adjudicated_at,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        boundaries=tuple(sample.boundary for sample in pack.samples),
        sample_kinds=tuple(sample.normalized_kind for sample in pack.samples),
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


def load_development_gold(pack_id: str) -> CompiledQualityCalibration:
    """Load one explicitly registered owner-adjudicated development-only pack."""

    policy = _PACK_POLICIES.get(pack_id)
    if policy is None:
        raise QualityCalibrationPackError(f"unregistered development-gold pack: {pack_id!r}")
    path = _QUALITY_CASES_DIR / policy.filename
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    compiled = compile_quality_calibration_pack(raw)
    if compiled.pack_id != pack_id:
        raise QualityCalibrationPackError(
            f"development-gold path for {pack_id} contains {compiled.pack_id}"
        )
    return compiled


def load_question_quality_development_gold() -> CompiledQualityCalibration:
    """Load the owner-adjudicated development-only question-quality boundary pack."""

    return load_development_gold("question-quality-development-gold-01")


def load_reader_fidelity_development_gold() -> CompiledQualityCalibration:
    """Load the owner-adjudicated development-only Reader fidelity boundary pack."""

    return load_development_gold("reader-fidelity-development-gold-01")


def load_grounded_answer_development_gold() -> CompiledQualityCalibration:
    """Load the owner-adjudicated development-only grounded-answer boundary pack."""

    return load_development_gold("grounded-answer-development-gold-02")

"""Frozen contracts for the question-design baseline experiment.

This module does not change production assessment routing.  It only gives the
Prototype a deterministic dataset identity and a balanced intake sampler so
baseline/candidate results can be compared against the same human gold.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvidenceAffordanceV0 = Literal[
    "narrow_proposition",
    "definition_attributes",
    "rule_conditions",
    "process",
    "contrast_set",
    "insufficient",
]
EvidenceExtentV0 = Literal["narrow", "medium", "broad"]
RequestedQuestionFormatV0 = Literal["auto", "multiple_choice", "open_response"]
RequestedChallengeV0 = Literal["foundation", "adaptive", "challenge"]
AssessmentModeV0 = Literal["atomic", "composite", "exploratory"]
CognitiveTaskV0 = Literal[
    "paraphrase",
    "explain",
    "apply",
    "diagnose_process",
    "discriminate",
]
FormatFitV0 = Literal["strong", "limited", "unsuitable"]
QuestionBuildOutcomeV0 = Literal["ready", "ready_degraded", "reroute_required", "failed"]

_AFFORDANCE_STRATA: frozenset[EvidenceAffordanceV0] = frozenset(
    {
        "narrow_proposition",
        "definition_attributes",
        "rule_conditions",
        "process",
        "contrast_set",
        "insufficient",
    }
)
_EXTENT_ORDER: tuple[EvidenceExtentV0, ...] = ("narrow", "medium", "broad")


class QuestionDesignSampleV0(BaseModel):
    """One owner-labelled question-design sample frozen before model calls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_quotes: tuple[str, ...] = Field(min_length=1)
    evidence_affordance: EvidenceAffordanceV0
    assessment_claim: str = Field(min_length=1)
    requested_format: RequestedQuestionFormatV0 = "auto"
    requested_challenge: RequestedChallengeV0 = "adaptive"

    @model_validator(mode="after")
    def _non_empty_evidence(self) -> QuestionDesignSampleV0:
        if any(not quote.strip() for quote in self.evidence_quotes):
            raise ValueError("evidence_quotes 不得包含空文本")
        return self


class QuestionDesignDatasetV0(BaseModel):
    """Canonical, content-addressed human-gold dataset snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["question-design-dataset.v0"] = "question-design-dataset.v0"
    dataset_id: str
    samples: tuple[QuestionDesignSampleV0, ...]
    stratum_counts: dict[str, int]


class QuestionDesignCandidateV0(BaseModel):
    """Unlabelled real KnowledgeItem offered to the owner for intake review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str = Field(min_length=1)
    resource_label: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_quotes: tuple[str, ...] = Field(min_length=1)

    @property
    def evidence_extent(self) -> EvidenceExtentV0:
        size = sum(len(quote) for quote in self.evidence_quotes)
        if size <= 30:
            return "narrow"
        if size <= 120:
            return "medium"
        return "broad"


class ItemDesignTargetV0(BaseModel):
    """Fully visible state returned by the throwaway planning Prototype."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_id: str
    mode: AssessmentModeV0
    assessment_claim: str
    evidence_affordance: EvidenceAffordanceV0
    cognitive_task: CognitiveTaskV0 | None
    requested_format: RequestedQuestionFormatV0
    selected_format: Literal["multiple_choice", "open_response"] | None
    requested_challenge: RequestedChallengeV0
    option_count_range: tuple[int, int] | None
    allowed_evidence_refs: tuple[str, ...]
    format_fit: FormatFitV0
    outcome: QuestionBuildOutcomeV0
    rationale: str


_TASK_BY_AFFORDANCE: dict[EvidenceAffordanceV0, CognitiveTaskV0 | None] = {
    "narrow_proposition": "paraphrase",
    "definition_attributes": "explain",
    "rule_conditions": "apply",
    "process": "diagnose_process",
    "contrast_set": "discriminate",
    "insufficient": None,
}
_AUTO_FORMAT_BY_AFFORDANCE: dict[
    EvidenceAffordanceV0, Literal["multiple_choice", "open_response"] | None
] = {
    "narrow_proposition": "open_response",
    "definition_attributes": "multiple_choice",
    "rule_conditions": "multiple_choice",
    "process": "open_response",
    "contrast_set": "multiple_choice",
    "insufficient": None,
}
_MC_OPTIONS_BY_AFFORDANCE: dict[EvidenceAffordanceV0, tuple[int, int] | None] = {
    "narrow_proposition": (3, 3),
    "definition_attributes": (3, 3),
    "rule_conditions": (4, 4),
    "process": (3, 4),
    "contrast_set": (3, 4),
    "insufficient": None,
}


def plan_question_design(sample: QuestionDesignSampleV0) -> ItemDesignTargetV0:
    """Prototype one explicit design state without calling a model.

    The human-gold affordance is an input, not inferred from text.  This keeps
    the Prototype focused on the routing/state question and prevents a hidden
    classifier from becoming its own source of truth.
    """

    affordance = sample.evidence_affordance
    task = _TASK_BY_AFFORDANCE[affordance]
    if task is None:
        return ItemDesignTargetV0(
            sample_id=sample.sample_id,
            mode="atomic",
            assessment_claim=sample.assessment_claim,
            evidence_affordance=affordance,
            cognitive_task=None,
            requested_format=sample.requested_format,
            selected_format=None,
            requested_challenge=sample.requested_challenge,
            option_count_range=None,
            allowed_evidence_refs=sample.evidence_quotes,
            format_fit="unsuitable",
            outcome="failed",
            rationale="允许 Evidence 不足以支持可评分的 atomic 任务；应补充材料或停止出题。",
        )

    selected_format = (
        _AUTO_FORMAT_BY_AFFORDANCE[affordance]
        if sample.requested_format == "auto"
        else sample.requested_format
    )
    assert selected_format is not None
    limited_mc = selected_format == "multiple_choice" and affordance in {
        "narrow_proposition",
        "process",
    }
    challenge_exceeds_evidence = (
        sample.requested_challenge == "challenge" and affordance == "narrow_proposition"
    )
    outcome: QuestionBuildOutcomeV0 = (
        "ready_degraded" if limited_mc or challenge_exceeds_evidence else "ready"
    )
    reasons: list[str] = []
    if limited_mc:
        reasons.append("该 Evidence 可支持选择题，但可信干扰项空间有限")
    if challenge_exceeds_evidence:
        reasons.append("窄命题无法诚实兑现高认知挑战，实际任务降为释义")
    if not reasons:
        reasons.append("Evidence 形态与认知任务、题型相匹配")
    return ItemDesignTargetV0(
        sample_id=sample.sample_id,
        mode="atomic",
        assessment_claim=sample.assessment_claim,
        evidence_affordance=affordance,
        cognitive_task=task,
        requested_format=sample.requested_format,
        selected_format=selected_format,
        requested_challenge=sample.requested_challenge,
        option_count_range=(
            _MC_OPTIONS_BY_AFFORDANCE[affordance] if selected_format == "multiple_choice" else None
        ),
        allowed_evidence_refs=sample.evidence_quotes,
        format_fit="limited" if limited_mc else "strong",
        outcome=outcome,
        rationale="；".join(reasons) + "。",
    )


def compile_question_design_dataset(
    samples: Iterable[QuestionDesignSampleV0],
    *,
    require_complete_strata: bool = True,
    min_samples: int = 1,
    min_resources: int = 1,
) -> QuestionDesignDatasetV0:
    """Freeze owner gold into one stable identity before any model execution."""

    materialized = list(samples)
    sample_ids = [sample.sample_id for sample in materialized]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_id 重复")
    if not materialized:
        raise ValueError("命题设计数据集不能为空")
    if len(materialized) < min_samples:
        raise ValueError(f"样本不足：需要至少 {min_samples} 个")
    resource_count = len({sample.resource_id for sample in materialized})
    if resource_count < min_resources:
        raise ValueError(f"材料不足：需要至少 {min_resources} 份")

    observed = {sample.evidence_affordance for sample in materialized}
    missing = sorted(_AFFORDANCE_STRATA - observed)
    if require_complete_strata and missing:
        raise ValueError(f"缺少预注册 Evidence 分层：{missing}")

    canonical_samples = tuple(sorted(materialized, key=lambda sample: sample.sample_id))
    canonical_payload = [sample.model_dump(mode="json") for sample in canonical_samples]
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    dataset_id = f"question-design-dataset.v0:{hashlib.sha256(encoded).hexdigest()}"
    counts = Counter(sample.evidence_affordance for sample in canonical_samples)
    return QuestionDesignDatasetV0(
        dataset_id=dataset_id,
        samples=canonical_samples,
        stratum_counts=dict(sorted(counts.items())),
    )


def select_balanced_candidates(
    candidates: Sequence[QuestionDesignCandidateV0],
    *,
    sample_count: int,
    min_resources: int,
) -> tuple[QuestionDesignCandidateV0, ...]:
    """Select a deterministic intake spanning resources and evidence extent.

    Evidence length is only a sampling axis.  It must never be treated as the
    human ``EvidenceAffordance`` label that the experiment is designed to test.
    """

    if sample_count < 1:
        raise ValueError("sample_count 至少为 1")
    if sample_count > len(candidates):
        raise ValueError("候选数量不足")
    resources = sorted({candidate.resource_id for candidate in candidates})
    if len(resources) < min_resources:
        raise ValueError(f"真实材料不足：需要至少 {min_resources} 份")

    groups: dict[tuple[str, EvidenceExtentV0], list[QuestionDesignCandidateV0]] = defaultdict(list)
    for candidate in candidates:
        groups[(candidate.resource_id, candidate.evidence_extent)].append(candidate)
    for group in groups.values():
        group.sort(key=lambda candidate: candidate.item_id)

    selected: list[QuestionDesignCandidateV0] = []
    selected_ids: set[str] = set()
    round_index = 0
    while len(selected) < sample_count:
        made_progress = False
        for resource_index, resource_id in enumerate(resources):
            preferred = (resource_index + round_index) % len(_EXTENT_ORDER)
            for offset in range(len(_EXTENT_ORDER)):
                extent = _EXTENT_ORDER[(preferred + offset) % len(_EXTENT_ORDER)]
                available = groups[(resource_id, extent)]
                candidate = next(
                    (entry for entry in available if entry.item_id not in selected_ids),
                    None,
                )
                if candidate is None:
                    continue
                selected.append(candidate)
                selected_ids.add(candidate.item_id)
                made_progress = True
                break
            if len(selected) == sample_count:
                break
        if not made_progress:
            raise ValueError("无法从候选中构造指定规模的平衡样本")
        round_index += 1
    return tuple(selected)

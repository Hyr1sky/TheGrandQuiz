"""Pure contracts and deterministic rules for knowledge classification."""

import re
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.vocabulary import VocabularyCatalog

KnowledgeKind = Literal[
    "concept",
    "mechanism",
    "procedure",
    "method",
    "tradeoff",
    "failure_mode",
    "case",
]
KnowledgeOrientation = Literal["theory", "practice"]
ClassificationSource = Literal["rule", "model", "user"]
ReviewStatus = Literal["proposed", "approved", "rejected"]
LifecycleStatus = Literal["active", "superseded", "retracted"]
SourceGenre = Literal[
    "official_documentation",
    "tutorial",
    "research_paper",
    "forum_post",
    "incident_report",
    "source_code",
    "personal_notes",
]


class KnowledgeClassificationV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["knowledge-classification.v1"] = "knowledge-classification.v1"
    taxonomy_version: str
    classification_id: str
    item_id: str
    revision: int = Field(ge=1)
    supersedes_id: str | None = None
    primary_kind: KnowledgeKind
    orientations: tuple[KnowledgeOrientation, ...]
    classified_by: ClassificationSource
    review_status: ReviewStatus
    lifecycle_status: LifecycleStatus
    trace_id: str


class ResourceRevisionClassificationV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["resource-revision-classification.v1"] = (
        "resource-revision-classification.v1"
    )
    taxonomy_version: str
    classification_id: str
    revision_id: str
    revision: int = Field(ge=1)
    supersedes_id: str | None = None
    primary_source_genre: SourceGenre
    classified_by: ClassificationSource
    review_status: ReviewStatus
    lifecycle_status: LifecycleStatus
    trace_id: str


class VocabularyTermView(BaseModel):
    term_id: str
    namespace: str
    key: str
    label_zh: str
    aliases: tuple[str, ...]
    status: Literal["proposed", "approved", "deprecated"]
    replacement_term_id: str | None = None
    taxonomy_version: str


class TagAssignmentV1(BaseModel):
    schema_version: Literal["tag-assignment.v1"] = "tag-assignment.v1"
    assignment_id: str
    item_id: str
    term_id: str
    revision: int = Field(ge=1)
    supersedes_id: str | None = None
    assigned_by: ClassificationSource
    review_status: ReviewStatus
    lifecycle_status: LifecycleStatus
    trace_id: str
    taxonomy_version: str


class TagCandidateV1(BaseModel):
    schema_version: Literal["tag-candidate.v1"] = "tag-candidate.v1"
    candidate_id: str
    raw_value: str
    namespace: str
    normalized_value: str
    review_status: ReviewStatus
    promoted_term_id: str | None = None
    trace_id: str
    taxonomy_version: str


class ClassificationProposal(BaseModel):
    schema_version: Literal["classification-proposal.v1"] = "classification-proposal.v1"
    primary_kind: KnowledgeKind
    orientations: tuple[KnowledgeOrientation, ...]
    classified_by: Literal["rule"] = "rule"
    classifier_version: Literal["knowledge-kind-rules.v1"] = "knowledge-kind-rules.v1"
    taxonomy_version: str
    tag_candidates: tuple[str, ...] = ()


def propose_item_classification(
    item: KnowledgeItem,
    *,
    vocabulary: VocabularyCatalog,
) -> ClassificationProposal:
    """Return a conservative deterministic proposal; never approve persistence."""

    text = f"{item.concept}\n{item.summary}".casefold()
    if any(token in text for token in ("故障", "失败", "报错", "failure", "error")):
        kind: KnowledgeKind = "failure_mode"
    elif any(token in text for token in ("步骤", "流程", "依次", "step", "procedure")):
        kind = "procedure"
    elif any(
        token in text for token in ("方法", "pageindex", "策略", "技术", "method", "approach")
    ):
        kind = "method"
    elif any(token in text for token in ("权衡", "取舍", "tradeoff", "trade-off")):
        kind = "tradeoff"
    elif any(token in text for token in ("原理", "机制", "为何", "如何工作", "mechanism")):
        kind = "mechanism"
    else:
        kind = "concept"
    defaults = vocabulary.default_orientations(kind)
    tag_candidates = tuple(
        sorted(
            {
                match.group(1)
                for match in re.finditer(r"#([A-Za-z][\w-]{1,40})", text)
                if vocabulary.resolve_managed_term(match.group(1)) is None
            }
        )
    )
    return ClassificationProposal(
        primary_kind=kind,
        orientations=tuple(
            cast("KnowledgeOrientation", orientation) for orientation in sorted(defaults)
        ),
        taxonomy_version=vocabulary.schema_version,
        tag_candidates=tag_candidates,
    )

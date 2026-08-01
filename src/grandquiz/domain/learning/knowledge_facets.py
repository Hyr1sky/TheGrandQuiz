"""Approved-only classification read model for explicit product filtering.

Classification proposals remain useful review input, but they are not product truth.  This
module is the single consumer boundary that turns reviewed classifications into facet counts
or exact item IDs.  It has no LLM path and preserves the caller's stable item ordering.
"""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from grandquiz.domain.learning.classification import KnowledgeClassificationV1, KnowledgeKind
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.vocabulary import load_vocabulary


class ApprovedClassificationReader(Protocol):
    def active_for_item(self, item_id: str) -> KnowledgeClassificationV1 | None: ...


class KnowledgeFacetFilter(BaseModel):
    """Explicit v0.3 filter: selected knowledge kinds are alternatives."""

    model_config = ConfigDict(frozen=True)

    primary_kinds: frozenset[KnowledgeKind] = Field(
        default_factory=lambda: frozenset[KnowledgeKind]()
    )

    @model_validator(mode="after")
    def _has_a_constraint(self) -> "KnowledgeFacetFilter":
        if not self.primary_kinds:
            raise ValueError("knowledge facet filter must contain at least one constraint")
        return self


class KnowledgeFacetInventoryV1(BaseModel):
    schema_version: Literal["knowledge-facet-inventory.v1"] = "knowledge-facet-inventory.v1"
    taxonomy_version: str
    item_count: int = Field(ge=0)
    approved_item_count: int = Field(ge=0)
    excluded_item_count: int = Field(ge=0)
    kind_counts: dict[KnowledgeKind, int]


class KnowledgeFacetSelectionV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["knowledge-facet-selection.v1"] = "knowledge-facet-selection.v1"
    taxonomy_version: str
    facet_filter: KnowledgeFacetFilter
    inventory_item_count: int = Field(ge=0)
    approved_item_count: int = Field(ge=0)
    matched_item_count: int = Field(ge=0)
    item_ids: tuple[str, ...]


def _approved(
    classifications: ApprovedClassificationReader,
    item_id: str,
) -> KnowledgeClassificationV1 | None:
    classification = classifications.active_for_item(item_id)
    if (
        classification is None
        or classification.review_status != "approved"
        or classification.lifecycle_status != "active"
    ):
        return None
    return classification


def _taxonomy_version(versions: set[str]) -> str:
    """Return one explicit taxonomy truth; never combine versions by lexical order."""

    if len(versions) > 1:
        raise ValueError(f"multiple taxonomy versions cannot share one facet view: {versions}")
    return next(iter(versions), load_vocabulary().schema_version)


def build_knowledge_facet_inventory(
    items: list[KnowledgeItem],
    *,
    classifications: ApprovedClassificationReader,
) -> KnowledgeFacetInventoryV1:
    kind_counts: dict[KnowledgeKind, int] = {}
    taxonomy_versions: set[str] = set()
    approved_count = 0
    for item in items:
        classification = _approved(classifications, item.item_id)
        if classification is None:
            continue
        approved_count += 1
        taxonomy_versions.add(classification.taxonomy_version)
        kind_counts[classification.primary_kind] = (
            kind_counts.get(classification.primary_kind, 0) + 1
        )
    return KnowledgeFacetInventoryV1(
        taxonomy_version=_taxonomy_version(taxonomy_versions),
        item_count=len(items),
        approved_item_count=approved_count,
        excluded_item_count=len(items) - approved_count,
        kind_counts=dict(sorted(kind_counts.items())),
    )


def select_knowledge_facets(
    items: list[KnowledgeItem],
    *,
    classifications: ApprovedClassificationReader,
    facet_filter: KnowledgeFacetFilter,
) -> KnowledgeFacetSelectionV1:
    """Resolve reviewed facets to exact IDs without changing item order or inventing fallback."""

    approved_count = 0
    matched: list[str] = []
    taxonomy_versions: set[str] = set()
    for item in items:
        classification = _approved(classifications, item.item_id)
        if classification is None:
            continue
        approved_count += 1
        taxonomy_versions.add(classification.taxonomy_version)
        if (
            facet_filter.primary_kinds
            and classification.primary_kind not in facet_filter.primary_kinds
        ):
            continue
        matched.append(item.item_id)
    return KnowledgeFacetSelectionV1(
        taxonomy_version=_taxonomy_version(taxonomy_versions),
        facet_filter=facet_filter,
        inventory_item_count=len(items),
        approved_item_count=approved_count,
        matched_item_count=len(matched),
        item_ids=tuple(matched),
    )

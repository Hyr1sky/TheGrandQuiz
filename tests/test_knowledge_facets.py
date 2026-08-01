"""Approved classification facets are a deterministic product read model."""

from grandquiz.domain.learning.assessment.selection import apply_scope
from grandquiz.domain.learning.classification import KnowledgeClassificationV1
from grandquiz.domain.learning.knowledge_facets import (
    KnowledgeFacetFilter,
    build_knowledge_facet_inventory,
    select_knowledge_facets,
)
from grandquiz.domain.learning.models import Evidence, KnowledgeItem


def _item(item_id: str) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=item_id,
        resource_id="resource-http",
        concept=item_id,
        summary=f"{item_id} summary",
        evidence=[Evidence(quote=f"{item_id} evidence")],
        confidence=1.0,
    )


def _classification(
    item_id: str,
    *,
    kind: str,
    orientations: tuple[str, ...],
    review_status: str = "approved",
    lifecycle_status: str = "active",
) -> KnowledgeClassificationV1:
    return KnowledgeClassificationV1.model_validate(
        {
            "taxonomy_version": "learning-vocabulary.v1",
            "classification_id": f"classification-{item_id}",
            "item_id": item_id,
            "revision": 1,
            "primary_kind": kind,
            "orientations": orientations,
            "classified_by": "user",
            "review_status": review_status,
            "lifecycle_status": lifecycle_status,
            "trace_id": "trace-classification",
        }
    )


class _ClassificationReader:
    def __init__(self, values: dict[str, KnowledgeClassificationV1 | None]) -> None:
        self._values = values

    def active_for_item(self, item_id: str) -> KnowledgeClassificationV1 | None:
        return self._values.get(item_id)


def test_inventory_and_filter_ignore_every_non_approved_classification() -> None:
    items = [_item("method"), _item("failure"), _item("proposed"), _item("superseded")]
    reader = _ClassificationReader(
        {
            "method": _classification("method", kind="method", orientations=("practice",)),
            "failure": _classification(
                "failure", kind="failure_mode", orientations=("practice", "theory")
            ),
            "proposed": _classification(
                "proposed",
                kind="method",
                orientations=("practice",),
                review_status="proposed",
            ),
            "superseded": _classification(
                "superseded",
                kind="method",
                orientations=("practice",),
                lifecycle_status="superseded",
            ),
        }
    )

    inventory = build_knowledge_facet_inventory(items, classifications=reader)
    selected = select_knowledge_facets(
        items,
        classifications=reader,
        facet_filter=KnowledgeFacetFilter(primary_kinds=frozenset({"method"})),
    )

    assert inventory.item_count == 4
    assert inventory.approved_item_count == 2
    assert inventory.excluded_item_count == 2
    assert inventory.kind_counts == {"failure_mode": 1, "method": 1}
    assert selected.item_ids == ("method",)
    assert selected.approved_item_count == 2
    assert selected.matched_item_count == 1


def test_primary_kinds_are_alternatives() -> None:
    items = [_item("method"), _item("failure")]
    reader = _ClassificationReader(
        {
            "method": _classification("method", kind="method", orientations=("practice",)),
            "failure": _classification(
                "failure", kind="failure_mode", orientations=("practice", "theory")
            ),
        }
    )

    selected = select_knowledge_facets(
        items,
        classifications=reader,
        facet_filter=KnowledgeFacetFilter(
            primary_kinds=frozenset({"method", "failure_mode"}),
        ),
    )

    assert selected.item_ids == ("method", "failure")


def test_empty_inventory_uses_the_single_controlled_vocabulary_version() -> None:
    inventory = build_knowledge_facet_inventory([], classifications=_ClassificationReader({}))

    assert inventory.taxonomy_version == "learning-vocabulary.v1"


def test_mixed_taxonomy_versions_fail_closed() -> None:
    items = [_item("current"), _item("future")]
    current = _classification("current", kind="method", orientations=("practice",))
    future = _classification("future", kind="method", orientations=("practice",)).model_copy(
        update={"taxonomy_version": "learning-vocabulary.v2"}
    )

    try:
        build_knowledge_facet_inventory(
            items,
            classifications=_ClassificationReader({"current": current, "future": future}),
        )
    except ValueError as exc:
        assert "multiple taxonomy versions" in str(exc)
    else:
        raise AssertionError("mixed taxonomy versions must not be merged")


def test_exact_item_scope_preserves_order_and_can_fail_closed() -> None:
    items = [_item("a"), _item("b"), _item("c")]

    assert apply_scope(items, None, item_ids=["c", "a"]) == [items[0], items[2]]
    assert apply_scope(items, None, item_ids=[]) == []
    assert apply_scope(items, None) is items

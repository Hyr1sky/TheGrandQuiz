import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.classification import propose_item_classification
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.domain.learning.vocabulary import load_vocabulary
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.clock import ManualClock
from grandquiz.providers.base import Completion, Message, Role


class _UnusedProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        del messages, role, tools
        raise AssertionError("classification API must not call an LLM")


def _app(tmp_path: Path):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=_UnusedProvider(),
    )


def _seed_item(tmp_path: Path) -> tuple[LearningResource, KnowledgeItem]:
    content = "# RAG\n\n## PageIndex\n\nPageIndex 使用树结构组织长文档。\n"
    resource = LearningResource.create(url="file://local/page-index.md").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "RAG",
            "trusted": True,
        }
    )
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="PageIndex",
        summary="PageIndex 是一种长文档检索方法。",
        evidence=[Evidence(quote="PageIndex 使用树结构组织长文档。")],
        confidence=0.95,
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    grounded = ground_items(snapshot, [item])[0]
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        persistence.store.replace_snapshot(resource, [grounded])
        stored = persistence.store.get_resource(resource.resource_id)
        assert stored is not None
        return stored, persistence.store.items_for_resource(resource.resource_id)[0]


def test_vocabulary_catalog_contains_method_and_resolves_seed_alias() -> None:
    catalog = load_vocabulary()

    assert "method" in catalog.keys("knowledge_kind")
    assert catalog.default_orientations("method") == frozenset({"practice"})
    term = catalog.resolve_managed_term("retrieval_augmented_generation")
    assert term is not None
    assert term.term_id == "domain:rag"


def test_user_classification_correction_appends_revision_and_supersedes_old(
    tmp_path: Path,
) -> None:
    _, item = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        first = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "classification-1",
                "primary_kind": "mechanism",
                "orientations": ["theory"],
            },
        )
        second = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "classification-2",
                "primary_kind": "method",
                "orientations": ["practice"],
            },
        )
        history = client.get(f"/api/v1/learning/items/{item.item_id}/classifications")

    assert first.status_code == 201
    assert second.status_code == 201
    payload = history.json()
    assert payload["active"]["primary_kind"] == "method"
    assert payload["active"]["orientations"] == ["practice"]
    assert payload["active"]["revision"] == 2
    assert payload["active"]["supersedes_id"] == first.json()["classification_id"]
    assert payload["history"][0]["lifecycle_status"] == "superseded"
    assert payload["history"][1]["lifecycle_status"] == "active"


def test_unknown_closed_classification_value_is_rejected(tmp_path: Path) -> None:
    _, item = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "classification-invalid",
                "primary_kind": "whatever-the-model-invented",
                "orientations": ["practice"],
            },
        )

    assert response.status_code == 422


def test_facet_inventory_exposes_only_reviewed_product_truth(tmp_path: Path) -> None:
    resource, item = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        proposed = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "facet-proposal",
                "primary_kind": "method",
                "orientations": ["practice"],
                "review_status": "proposed",
            },
        ).json()
        before = client.get(
            "/api/v1/learning/facets",
            params={"resource_id": resource.resource_id},
        ).json()
        client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications/"
            f"{proposed['classification_id']}/review",
            json={"request_id": "facet-approve", "review_status": "approved"},
        )
        after = client.get(
            "/api/v1/learning/facets",
            params={"resource_id": resource.resource_id},
        ).json()

    assert before["item_count"] == 1
    assert before["approved_item_count"] == 0
    assert before["excluded_item_count"] == 1
    assert before["kind_counts"] == {}
    assert after["approved_item_count"] == 1
    assert after["excluded_item_count"] == 0
    assert after["kind_counts"] == {"method": 1}


def test_classification_request_id_rejects_different_payload(tmp_path: Path) -> None:
    _, item = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        first = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "classification-retry",
                "primary_kind": "mechanism",
                "orientations": ["theory"],
            },
        )
        conflict = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "classification-retry",
                "primary_kind": "method",
                "orientations": ["practice"],
            },
        )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_classification_fact_uses_injected_clock(tmp_path: Path) -> None:
    _, item = _seed_item(tmp_path)

    with LearningPersistence(
        tmp_path / "learning.db",
        clock=ManualClock(start=42.5, tick=0.5),
    ) as persistence:
        persistence.classifications.classify_item(
            item_id=item.item_id,
            request_id="classification-clock",
            primary_kind="method",
            orientations={"practice"},
            trace_id="trace-classification-clock",
        )
        fact = persistence.learning_facts.facts(event_type="learning.knowledge_classified")[0]

    assert fact.source_event_ts == 42.5


def test_source_genre_is_revision_level_and_managed_tag_requires_approval(
    tmp_path: Path,
) -> None:
    resource, item = _seed_item(tmp_path)
    assert resource.current_revision_id is not None

    with TestClient(_app(tmp_path)) as client:
        genre = client.post(
            f"/api/v1/learning/revisions/{resource.current_revision_id}/classifications",
            json={
                "request_id": "source-genre-1",
                "primary_source_genre": "tutorial",
            },
        )
        rejected_assignment = client.post(
            f"/api/v1/learning/items/{item.item_id}/tags",
            json={"request_id": "tag-1", "term_id": "domain:rag"},
        )
        approved = client.post(
            "/api/v1/learning/vocabulary/terms/domain:rag/review",
            json={"request_id": "approve-rag", "review_status": "approved"},
        )
        assignment = client.post(
            f"/api/v1/learning/items/{item.item_id}/tags",
            json={"request_id": "tag-1", "term_id": "domain:rag"},
        )
        tags = client.get(f"/api/v1/learning/items/{item.item_id}/tags")

    assert genre.status_code == 201
    assert genre.json()["revision_id"] == resource.current_revision_id
    assert genre.json()["primary_source_genre"] == "tutorial"
    assert rejected_assignment.status_code == 409
    assert approved.status_code == 200
    assert assignment.status_code == 201
    assert tags.json()["items"][0]["term_id"] == "domain:rag"

    with LearningPersistence(tmp_path / "learning.db") as persistence:
        fact_types = {fact.event_type for fact in persistence.learning_facts.facts()}
    assert "learning.resource_revision_classified" in fact_types
    assert "learning.managed_tag_assigned" in fact_types


def test_classification_review_candidate_and_deprecated_replacement(
    tmp_path: Path,
) -> None:
    _, item = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        baseline = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "approved-baseline",
                "primary_kind": "mechanism",
                "orientations": ["theory"],
            },
        )
        proposed = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "proposed-classification",
                "primary_kind": "method",
                "orientations": ["practice"],
                "review_status": "proposed",
            },
        )
        before_review = client.get(f"/api/v1/learning/items/{item.item_id}/classifications").json()
        reviewed = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications/"
            f"{proposed.json()['classification_id']}/review",
            json={"request_id": "approve-classification", "review_status": "approved"},
        )
        original_retry_after_review = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "proposed-classification",
                "primary_kind": "method",
                "orientations": ["practice"],
                "review_status": "proposed",
            },
        )
        review_conflict = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications/"
            f"{proposed.json()['classification_id']}/review",
            json={"request_id": "approve-classification", "review_status": "rejected"},
        )
        client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications/"
            f"{proposed.json()['classification_id']}/review",
            json={"request_id": "reject-classification", "review_status": "rejected"},
        )
        reapproved = client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications/"
            f"{proposed.json()['classification_id']}/review",
            json={"request_id": "reapprove-classification", "review_status": "approved"},
        )
        candidate = client.post(
            "/api/v1/learning/vocabulary/tag-candidates",
            json={
                "request_id": "candidate-1",
                "namespace": "technology",
                "raw_value": "Page Index",
            },
        )
        rejected = client.post(
            f"/api/v1/learning/vocabulary/tag-candidates/{candidate.json()['candidate_id']}/review",
            json={"request_id": "reject-candidate", "review_status": "rejected"},
        )
        promoted_candidate = client.post(
            "/api/v1/learning/vocabulary/tag-candidates",
            json={
                "request_id": "candidate-2",
                "namespace": "technology",
                "raw_value": "Page Tree",
            },
        )
        promoted = client.post(
            f"/api/v1/learning/vocabulary/tag-candidates/"
            f"{promoted_candidate.json()['candidate_id']}/review",
            json={"request_id": "approve-candidate", "review_status": "approved"},
        )
        duplicate_promoted_candidate = client.post(
            "/api/v1/learning/vocabulary/tag-candidates",
            json={
                "request_id": "candidate-2-duplicate",
                "namespace": "technology",
                "raw_value": "Page Tree",
            },
        )
        alias_candidate = client.post(
            "/api/v1/learning/vocabulary/tag-candidates",
            json={
                "request_id": "alias-candidate",
                "namespace": "technology",
                "raw_value": "ros_2",
            },
        )
        client.post(
            "/api/v1/learning/vocabulary/terms/domain:rag/review",
            json={
                "request_id": "deprecate-rag",
                "review_status": "deprecated",
                "replacement_term_id": "domain:evaluation",
            },
        )
        client.post(
            "/api/v1/learning/vocabulary/terms/domain:evaluation/review",
            json={"request_id": "approve-evaluation", "review_status": "approved"},
        )
        first = client.post(
            f"/api/v1/learning/items/{item.item_id}/tags",
            json={"request_id": "replacement-tag-1", "term_id": "domain:rag"},
        )
        second = client.post(
            f"/api/v1/learning/items/{item.item_id}/tags",
            json={"request_id": "replacement-tag-2", "term_id": "domain:rag"},
        )

    assert proposed.status_code == 201
    assert proposed.json()["review_status"] == "proposed"
    assert before_review["active"]["classification_id"] == baseline.json()["classification_id"]
    assert reviewed.json()["review_status"] == "approved"
    assert original_retry_after_review.status_code == 201
    assert (
        original_retry_after_review.json()["classification_id"]
        == proposed.json()["classification_id"]
    )
    assert review_conflict.status_code == 409
    assert reapproved.json()["review_status"] == "approved"
    assert candidate.status_code == 201
    assert rejected.json()["review_status"] == "rejected"
    assert promoted.json()["promoted_term_id"] == "technology:page_tree"
    assert duplicate_promoted_candidate.status_code == 409
    assert alias_candidate.status_code == 409
    assert first.json()["term_id"] == "domain:evaluation"
    assert first.json()["revision"] == 1
    assert second.json()["revision"] == 2
    assert second.json()["supersedes_id"] == first.json()["assignment_id"]
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        reviews = persistence.learning_facts.facts(
            event_type="learning.knowledge_classification_reviewed"
        )
        vocabulary_reviews = persistence.learning_facts.facts(
            event_type="learning.vocabulary_term_reviewed"
        )
    assert len(reviews) == 3
    assert len(vocabulary_reviews) == 2


def test_classification_replay_distinguishes_method_mechanism_and_procedure() -> None:
    catalog = load_vocabulary()
    fixture = Path(__file__).parent / "fixtures" / "learning_classification_replay.json"
    samples = json.loads(fixture.read_text(encoding="utf-8"))

    for sample in samples:
        item = KnowledgeItem.create(
            resource_id="resource-replay",
            concept=sample["concept"],
            summary=sample["summary"],
            evidence=[Evidence(quote=sample["evidence"])],
            confidence=0.9,
        )
        assert (
            propose_item_classification(item, vocabulary=catalog).primary_kind
            == sample["expected_primary_kind"]
        )

    unknown_tag_item = KnowledgeItem.create(
        resource_id="resource-replay",
        concept="PageTree",
        summary="使用 #PageTree 组织文档。",
        evidence=[Evidence(quote="使用 PageTree 组织文档。")],
        confidence=0.9,
    )
    assert propose_item_classification(
        unknown_tag_item,
        vocabulary=catalog,
    ).tag_candidates == ("pagetree",)

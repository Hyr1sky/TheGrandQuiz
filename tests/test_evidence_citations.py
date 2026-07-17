"""精确 Evidence locator 与 citation 行为（ADR-0008 / DS-S2）。"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import grandquiz.domain.learning
from grandquiz.domain.learning.citations import (
    CitationResolutionError,
    GroundingError,
    ground_items,
    render_citation,
    resolve_citation,
)
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.models import (
    Evidence,
    EvidenceLocator,
    KnowledgeItem,
    LearningResource,
)
from grandquiz.domain.learning.store import LearningStore, SqliteLearningStore
from grandquiz.kernel.db import connect, migrate


def test_unique_quote_is_grounded_to_revision_node_and_exact_source_span() -> None:
    content = "# React\n\n## Hooks\n\n闭包证据。\n"
    resource = LearningResource.create(url="https://example.com/react").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="闭包",
        summary="闭包摘要",
        evidence=[Evidence(quote="闭包证据。")],
        confidence=0.9,
    )

    grounded = ground_items(snapshot, [item])[0]
    locator = grounded.evidence[0].locator
    assert isinstance(locator, EvidenceLocator) and locator.resolved
    assert locator.revision_id == snapshot.revision.revision_id
    assert locator.section_path == "React > Hooks"
    assert content[locator.start_offset : locator.end_offset] == "闭包证据。"
    node = next(node for node in snapshot.nodes if node.node_id == locator.node_id)
    assert node.start_offset <= locator.start_offset < locator.end_offset <= node.end_offset
    assert locator.quote_hash == hashlib.sha256("闭包证据。".encode()).hexdigest()
    assert "React > Hooks" in render_citation(resource, grounded.evidence[0])


@pytest.mark.parametrize("quote", ["重复", "不存在"])
def test_ambiguous_or_missing_quote_is_rejected(quote: str) -> None:
    content = "# 文档\n\n重复。重复。\n"
    resource = LearningResource.create(url="https://example.com/ambiguous").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="测试",
        summary="摘要",
        evidence=[Evidence(quote=quote)],
        confidence=0.8,
    )

    with pytest.raises(GroundingError, match="无法唯一定位"):
        ground_items(snapshot, [item])


def test_quote_crossing_natural_nodes_is_rejected_instead_of_anchored_to_section() -> None:
    content = "# 文档\n\n第一段。\n\n第二段。\n"
    resource = LearningResource.create(url="https://example.com/cross-node").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="跨节点",
        summary="摘要",
        evidence=[Evidence(quote="第一段。\n\n第二段。")],
        confidence=0.8,
    )

    with pytest.raises(GroundingError, match="未落在单一 DocumentNode"):
        ground_items(snapshot, [item])


def test_sqlite_normalized_evidence_reopens_with_exact_locator(tmp_path: Path) -> None:
    content = "# React\n\n闭包证据。\n"
    resource = LearningResource.create(url="https://example.com/sqlite").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="闭包",
        summary="摘要",
        evidence=[Evidence(quote="闭包证据。")],
        confidence=0.9,
    )
    grounded = ground_items(snapshot, [item])[0]
    database = tmp_path / "learning.db"
    store = SqliteLearningStore(database)
    store.replace_snapshot(resource, [grounded])
    store.close()

    reopened = SqliteLearningStore(database)
    assert reopened.evidence_for_item(grounded.item_id) == grounded.evidence
    assert reopened.items_for_resource(resource.resource_id)[0].evidence == grounded.evidence
    reopened.close()


def test_historical_citation_resolves_declared_revision_with_bounded_context() -> None:
    first_content = "# 第一版\n\n前文。唯一证据。后文。\n"
    first = LearningResource.create(url="https://example.com/history").model_copy(
        update={
            "raw_content": first_content,
            "content_hash": hashlib.sha256(first_content.encode()).hexdigest(),
            "status": "read",
            "topic": "历史材料",
        }
    )
    snapshot = build_document_snapshot(first)
    assert snapshot is not None
    item = KnowledgeItem.create(
        resource_id=first.resource_id,
        concept="历史证据",
        summary="摘要",
        evidence=[Evidence(quote="唯一证据。")],
        confidence=0.9,
    )
    grounded = ground_items(snapshot, [item])[0]
    store = LearningStore()
    store.replace_snapshot(first, [grounded])

    second_content = "# 第二版\n\n新正文。\n"
    second = first.model_copy(
        update={
            "raw_content": second_content,
            "content_hash": hashlib.sha256(second_content.encode()).hexdigest(),
        }
    )
    store.replace_snapshot(second, [])
    resolved = resolve_citation(store, grounded.evidence[0], context_chars=3)

    assert resolved.revision_id == snapshot.revision.revision_id
    assert resolved.revision_id != store.current_revision(first.resource_id).revision_id  # type: ignore[union-attr]
    assert resolved.quote == "唯一证据。"
    assert resolved.context == "前文。唯一证据。后文。"
    rendered = render_citation(first, grounded.evidence[0])
    assert f"@{resolved.revision_id}" in rendered


def test_historical_citation_reports_missing_revision() -> None:
    content = "证据。"
    resource = LearningResource.create(url="https://example.com/missing-history").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="证据",
        summary="摘要",
        evidence=[Evidence(quote="证据。")],
        confidence=0.9,
    )
    grounded = ground_items(snapshot, [item])[0]

    with pytest.raises(CitationResolutionError) as error:
        resolve_citation(LearningStore(), grounded.evidence[0])
    assert error.value.classification == "revision_missing"


def test_tampered_quote_hash_rejects_whole_snapshot_and_preserves_current(tmp_path: Path) -> None:
    content = "# 文档\n\n精确证据。\n"
    resource = LearningResource.create(url="https://example.com/tampered").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="精确证据",
        summary="摘要",
        evidence=[Evidence(quote="精确证据。")],
        confidence=0.9,
    )
    grounded = ground_items(snapshot, [item])[0]
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [grounded])
    previous = store.items_for_resource(resource.resource_id)
    locator = grounded.evidence[0].locator
    assert isinstance(locator, EvidenceLocator)
    tampered = grounded.model_copy(
        update={
            "evidence": [
                grounded.evidence[0].model_copy(
                    update={"locator": locator.model_copy(update={"quote_hash": "0" * 64})}
                )
            ]
        }
    )

    with pytest.raises(CitationResolutionError) as error:
        store.replace_snapshot(resource, [tampered])
    assert error.value.classification == "quote_hash_mismatch"
    assert store.items_for_resource(resource.resource_id) == previous
    store.close()


def test_opening_v9_database_backfills_unique_quote_and_marks_ambiguous_unresolved(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learning.db"
    legacy_migrations = tmp_path / "legacy-migrations"
    legacy_migrations.mkdir()
    migrations = Path(grandquiz.domain.learning.__file__).parent / "migrations"
    for source in sorted(migrations.glob("000[1-9]_*.sql")):
        shutil.copy(source, legacy_migrations / source.name)

    content = "# React\n\n唯一证据。重复。重复。\n"
    resource = LearningResource.create(url="https://example.com/legacy-evidence").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    conn = connect(database)
    migrate(conn, legacy_migrations)
    conn.execute(
        "INSERT INTO resources "
        "(resource_id, url, raw_content, content_hash, trusted, status, topic, "
        "current_revision_id) VALUES (?, ?, ?, ?, 0, 'read', 'React', NULL)",
        (resource.resource_id, resource.url, content, resource.content_hash),
    )
    for item_id, quote in [("item-unique", "唯一证据。"), ("item-ambiguous", "重复")]:
        conn.execute(
            "INSERT INTO knowledge_items "
            "(item_id, resource_id, concept, summary, evidence, confidence, concept_key) "
            "VALUES (?, ?, ?, '摘要', ?, 0.9, NULL)",
            (
                item_id,
                resource.resource_id,
                item_id,
                json.dumps([{"quote": quote, "locator": None}], ensure_ascii=False),
            ),
        )
    conn.commit()
    conn.close()

    store = SqliteLearningStore(database)
    unique = store.evidence_for_item("item-unique")[0]
    ambiguous = store.evidence_for_item("item-ambiguous")[0]
    assert isinstance(unique.locator, EvidenceLocator)
    assert content[unique.locator.start_offset : unique.locator.end_offset] == unique.quote
    assert ambiguous.locator is None
    assert [(entry.item_id, entry.ordinal) for entry in store.unresolved_evidence()] == [
        ("item-ambiguous", 0)
    ]
    assert [item.item_id for item in store.items_for_resource(resource.resource_id)] == [
        "item-ambiguous",
        "item-unique",
    ]
    assert store.get_resource(resource.resource_id).status == "read"  # type: ignore[union-attr]
    store.close()

    reopened = SqliteLearningStore(database)
    assert [(entry.item_id, entry.ordinal) for entry in reopened.unresolved_evidence()] == [
        ("item-ambiguous", 0)
    ]
    assert reopened.evidence_for_item("item-unique") == [unique]
    reopened.close()

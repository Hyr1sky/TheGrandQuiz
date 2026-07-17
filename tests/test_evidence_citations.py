"""精确 Evidence locator 与 citation 行为（ADR-0008 / DS-S2）。"""

import hashlib
import json
import shutil
import sqlite3
from itertools import product
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
from grandquiz.domain.learning.document_search import DocumentSearch, SearchScope
from grandquiz.domain.learning.models import (
    Evidence,
    EvidenceLocator,
    KnowledgeItem,
    LearningResource,
)
from grandquiz.domain.learning.persistence import LearningDatabase
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


def test_grounding_accepts_normalized_whitespace_but_persists_exact_source_quote() -> None:
    content = "# 文档\n\nAlpha\t beta\n gamma。\n"
    resource = LearningResource.create(url="https://example.com/whitespace").model_copy(
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
        concept="空白规范化",
        summary="摘要",
        evidence=[Evidence(quote="Alpha beta gamma。")],
        confidence=0.9,
    )

    grounded = ground_items(snapshot, [item])[0]
    evidence = grounded.evidence[0]
    locator = evidence.locator
    assert isinstance(locator, EvidenceLocator)
    assert evidence.quote == "Alpha\t beta\n gamma。"
    assert content[locator.start_offset : locator.end_offset] == evidence.quote
    assert locator.quote_hash == hashlib.sha256(evidence.quote.encode()).hexdigest()
    assert grounded.item_id == item.item_id


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


def test_overlapping_quote_occurrences_are_ambiguous() -> None:
    content = "aaaa"
    resource = LearningResource.create(url="https://example.com/overlap").model_copy(
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
        concept="重叠引文",
        summary="摘要",
        evidence=[Evidence(quote="aaa")],
        confidence=0.8,
    )

    with pytest.raises(GroundingError) as error:
        ground_items(snapshot, [item])
    assert error.value.classification == "quote_ambiguous"


def test_generated_unicode_quotes_preserve_exact_offsets_at_node_boundaries() -> None:
    quotes = ["知识点🧭", "cafe\u0301", "数学∑", "العربية"]
    placements = [("", "尾部"), ("前部", "尾部"), ("前部", "")]
    for index, (quote, (prefix, suffix)) in enumerate(product(quotes, placements)):
        paragraph = f"{prefix}{quote}{suffix}"
        content = f"# Unicode\n\n{paragraph}\n"
        resource = LearningResource.create(url=f"https://example.com/unicode-{index}").model_copy(
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
            concept=f"Unicode {index}",
            summary="摘要",
            evidence=[Evidence(quote=quote)],
            confidence=0.9,
        )

        grounded = ground_items(snapshot, [item])[0]
        evidence = grounded.evidence[0]
        locator = evidence.locator
        assert isinstance(locator, EvidenceLocator)
        assert content[locator.start_offset : locator.end_offset] == quote
        node = next(node for node in snapshot.nodes if node.node_id == locator.node_id)
        assert node.start_offset <= locator.start_offset
        assert locator.end_offset <= node.end_offset
        if not prefix:
            assert locator.start_offset == node.start_offset
        if not suffix:
            assert not content[locator.end_offset : node.end_offset].strip()


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


def test_evidence_write_failure_rolls_back_revision_tree_items_and_search(tmp_path: Path) -> None:
    database = LearningDatabase(tmp_path / "learning.db")
    store = SqliteLearningStore(database)
    first_content = "# 第一版\n\n旧证据 needle-old。\n"
    first = LearningResource.create(url="https://example.com/evidence-atomic").model_copy(
        update={
            "raw_content": first_content,
            "content_hash": hashlib.sha256(first_content.encode()).hexdigest(),
            "status": "read",
        }
    )
    first_document = build_document_snapshot(first)
    assert first_document is not None
    first_item = KnowledgeItem.create(
        resource_id=first.resource_id,
        concept="旧证据",
        summary="旧摘要",
        evidence=[Evidence(quote="旧证据 needle-old。")],
        confidence=0.9,
    )
    store.replace_snapshot(first, [ground_items(first_document, [first_item])[0]])
    previous_revision = store.current_revision(first.resource_id)
    previous_nodes = store.document_nodes(first.resource_id)
    previous_items = store.items_for_resource(first.resource_id)
    database.connection.execute(
        "CREATE TRIGGER fail_evidence_insert BEFORE INSERT ON knowledge_item_evidence "
        "BEGIN SELECT RAISE(FAIL, 'evidence write failed'); END"
    )
    database.commit()

    second_content = "# 第二版\n\n新证据 needle-new。\n"
    second = first.model_copy(
        update={
            "raw_content": second_content,
            "content_hash": hashlib.sha256(second_content.encode()).hexdigest(),
        }
    )
    second_document = build_document_snapshot(second)
    assert second_document is not None
    second_item = KnowledgeItem.create(
        resource_id=second.resource_id,
        concept="新证据",
        summary="新摘要",
        evidence=[Evidence(quote="新证据 needle-new。")],
        confidence=0.9,
    )

    with pytest.raises(sqlite3.IntegrityError, match="evidence write failed"):
        store.replace_snapshot(second, [ground_items(second_document, [second_item])[0]])

    assert store.current_revision(first.resource_id) == previous_revision
    assert store.document_nodes(first.resource_id) == previous_nodes
    assert store.items_for_resource(first.resource_id) == previous_items
    search = DocumentSearch(store)
    scope = SearchScope(mode="selected", resource_ids=[first.resource_id])
    assert search.search("needle old", scope=scope)
    assert search.search("needle new", scope=scope) == []
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

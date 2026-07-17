"""修订化文档树的公共行为测试（ADR-0008 / DS-S1）。"""

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

import grandquiz.domain.learning
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.models import LearningResource
from grandquiz.domain.learning.persistence import LearningDatabase
from grandquiz.domain.learning.store import LearningStore, SqliteLearningStore
from grandquiz.kernel.db import connect, migrate


def _approved_resource(content: str) -> LearningResource:
    return LearningResource.create(url="https://example.com/guide").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "status": "read",
        }
    )


def test_approved_snapshot_exposes_stable_current_revision_and_outline() -> None:
    content = "# React\n\n导言。\n\n## Hooks\n\n闭包证据。\n"
    resource = _approved_resource(content)
    store = LearningStore()

    store.replace_snapshot(resource, [])

    revision = store.current_revision(resource.resource_id)
    assert revision is not None
    assert revision.resource_id == resource.resource_id
    assert revision.content_hash == resource.content_hash
    assert revision.raw_content == content
    assert store.get_revision(revision.revision_id) == revision

    outline = store.document_outline(resource.resource_id)
    assert [(node.title, node.section_path, node.depth) for node in outline] == [
        ("React", "React", 1),
        ("Hooks", "React > Hooks", 2),
    ]
    assert all(node.revision_id == revision.revision_id for node in outline)

    # 同一个获批快照重放不会漂移 revision/node 身份或顺序。
    first_node_ids = [node.node_id for node in outline]
    store.replace_snapshot(resource, [])
    assert store.current_revision(resource.resource_id) == revision
    assert [node.node_id for node in store.document_outline(resource.resource_id)] == first_node_ids


def test_sqlite_reopens_with_same_current_revision_and_outline(tmp_path: Path) -> None:
    content = "# React\n\n导言。\n\n## Hooks\n\n闭包证据。\n"
    resource = _approved_resource(content)
    database = tmp_path / "learning.db"

    store = SqliteLearningStore(database)
    store.replace_snapshot(resource, [])
    revision = store.current_revision(resource.resource_id)
    outline = store.document_outline(resource.resource_id)
    store.close()

    reopened = SqliteLearningStore(database)
    assert revision is not None
    assert reopened.current_revision(resource.resource_id) == revision
    assert reopened.get_revision(revision.revision_id) == revision
    assert reopened.document_outline(resource.resource_id) == outline
    assert reopened.get_resource(resource.resource_id).current_revision_id == revision.revision_id  # type: ignore[union-attr]
    reopened.close()


def test_plain_text_keeps_ordered_paragraph_list_table_and_code_nodes() -> None:
    content = (
        "导言。\n\n"
        "- 第一项\n- 第二项\n\n"
        "| 名称 | 值 |\n| --- | --- |\n| hooks | 2 |\n\n"
        "```python\nprint('ok')\n```\n"
    )
    resource = _approved_resource(content)
    store = LearningStore()
    store.replace_snapshot(resource, [])

    nodes = store.document_nodes(resource.resource_id)
    assert [node.kind for node in nodes] == ["document", "paragraph", "list", "table", "code"]
    assert nodes[0].parent_node_id is None
    assert all(node.parent_node_id == nodes[0].node_id for node in nodes[1:])
    assert [node.ordinal for node in nodes] == list(range(len(nodes)))
    assert [content[node.start_offset : node.end_offset] for node in nodes[1:]] == [
        "导言。\n",
        "- 第一项\n- 第二项\n",
        "| 名称 | 值 |\n| --- | --- |\n| hooks | 2 |\n",
        "```python\nprint('ok')\n```\n",
    ]


def test_markdown_heading_inside_fenced_code_is_not_a_section() -> None:
    content = "# Real\n\n```markdown\n# Not a heading\n```\n"
    resource = _approved_resource(content)
    store = LearningStore()
    store.replace_snapshot(resource, [])

    assert [node.title for node in store.document_outline(resource.resource_id)] == ["Real"]
    assert [node.kind for node in store.document_nodes(resource.resource_id)] == [
        "document",
        "section",
        "code",
    ]


def test_opening_v8_database_backfills_revision_tree_without_clearing_learning_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learning.db"
    legacy_migrations = tmp_path / "legacy-migrations"
    legacy_migrations.mkdir()
    migrations = Path(grandquiz.domain.learning.__file__).parent / "migrations"
    for source in sorted(migrations.glob("000[1-8]_*.sql")):
        shutil.copy(source, legacy_migrations / source.name)

    content = "# React\n\n闭包证据。\n"
    resource = _approved_resource(content)
    conn = connect(database)
    migrate(conn, legacy_migrations)
    conn.execute(
        "INSERT INTO resources "
        "(resource_id, url, raw_content, content_hash, trusted, status, topic) "
        "VALUES (?, ?, ?, ?, 0, 'read', 'React')",
        (resource.resource_id, resource.url, content, resource.content_hash),
    )
    conn.execute(
        "INSERT INTO knowledge_items "
        "(item_id, resource_id, concept, summary, evidence, confidence, concept_key) "
        "VALUES ('item-1', ?, '闭包', '摘要', '[{\"quote\": \"闭包证据。\", "
        '"locator": null}]\', 0.9, NULL)',
        (resource.resource_id,),
    )
    conn.execute(
        "INSERT INTO learning_memory "
        "(item_id, state, consecutive_correct, verdict_history) "
        "VALUES ('item-1', '薄弱', 0, '[\"错\"]')"
    )
    conn.commit()
    conn.close()

    store = SqliteLearningStore(database)
    revision = store.current_revision(resource.resource_id)
    assert revision is not None
    assert (
        revision.revision_id
        == hashlib.sha256(f"{resource.resource_id}\0{resource.content_hash}".encode()).hexdigest()[
            :16
        ]
    )
    assert [node.title for node in store.document_outline(resource.resource_id)] == ["React"]
    assert [item.item_id for item in store.items_for_resource(resource.resource_id)] == ["item-1"]
    assert SqliteLearningMemory(database).state_of("item-1") == "薄弱"
    store.close()


def test_new_content_switches_current_but_keeps_old_revision_and_nodes(tmp_path: Path) -> None:
    stores = [LearningStore(), SqliteLearningStore(tmp_path / "learning.db")]
    first = _approved_resource("# 第一版\n\n旧正文。\n")
    second_content = "# 第二版\n\n新正文。\n"
    second = first.model_copy(
        update={
            "raw_content": second_content,
            "content_hash": hashlib.sha256(second_content.encode()).hexdigest(),
        }
    )

    for store in stores:
        store.replace_snapshot(first, [])
        old_revision = store.current_revision(first.resource_id)
        assert old_revision is not None
        old_nodes = store.document_nodes(first.resource_id, revision_id=old_revision.revision_id)

        store.replace_snapshot(second, [])
        current = store.current_revision(first.resource_id)
        assert current is not None and current.revision_id != old_revision.revision_id
        assert [node.title for node in store.document_outline(first.resource_id)] == ["第二版"]
        assert store.get_revision(old_revision.revision_id) == old_revision
        assert (
            store.document_nodes(first.resource_id, revision_id=old_revision.revision_id)
            == old_nodes
        )

    assert stores[0].current_revision(first.resource_id) == stores[1].current_revision(
        first.resource_id
    )
    assert stores[0].document_nodes(first.resource_id) == stores[1].document_nodes(
        first.resource_id
    )

    stores[1].close()  # type: ignore[union-attr]


def test_oversized_single_paragraph_becomes_lossless_synthetic_leaves() -> None:
    content = "证" * 20_001
    resource = _approved_resource(content)
    store = LearningStore()
    store.replace_snapshot(resource, [])

    leaves = [
        node for node in store.document_nodes(resource.resource_id) if node.kind != "document"
    ]
    assert len(leaves) == 2
    assert all(node.synthetic for node in leaves)
    assert all(node.end_offset - node.start_offset <= 16_000 for node in leaves)
    assert "".join(content[node.start_offset : node.end_offset] for node in leaves) == content


def test_content_hash_mismatch_is_rejected_without_switching_current() -> None:
    store = LearningStore()
    approved = _approved_resource("# 正确版本\n")
    store.replace_snapshot(approved, [])
    current = store.current_revision(approved.resource_id)

    corrupted = approved.model_copy(update={"raw_content": "# 被篡改正文\n"})
    with pytest.raises(ValueError, match="content_hash"):
        store.replace_snapshot(corrupted, [])

    assert store.current_revision(approved.resource_id) == current


def test_sqlite_node_write_failure_rolls_back_resource_revision_and_current_pointer(
    tmp_path: Path,
) -> None:
    database = LearningDatabase(tmp_path / "learning.db")
    store = SqliteLearningStore(database)
    approved = _approved_resource("# 第一版\n\n旧正文。\n")
    store.replace_snapshot(approved, [])
    previous_resource = store.get_resource(approved.resource_id)
    previous_revision = store.current_revision(approved.resource_id)

    database.connection.execute(
        "CREATE TRIGGER fail_document_node_insert "
        "BEFORE INSERT ON document_nodes "
        "BEGIN SELECT RAISE(ABORT, 'node write failed'); END"
    )
    updated = _approved_resource("# 第二版\n\n新正文。\n")
    with pytest.raises(sqlite3.IntegrityError, match="node write failed"):
        store.replace_snapshot(updated, [])

    assert store.get_resource(approved.resource_id) == previous_resource
    assert store.current_revision(approved.resource_id) == previous_revision
    expected_revision_id = hashlib.sha256(
        f"{approved.resource_id}\0{updated.content_hash}".encode()
    ).hexdigest()[:16]
    assert store.get_revision(expected_revision_id) is None
    store.close()

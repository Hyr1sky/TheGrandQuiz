"""DS-S4：current revision 上的渐进式稀疏检索与预算内节点读取。"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from grandquiz.domain.learning.document_search import (
    DocumentSearch,
    ReadBudgetExceeded,
    ScopeResolutionError,
    SearchScope,
)
from grandquiz.domain.learning.models import LearningResource
from grandquiz.domain.learning.persistence import LearningDatabase
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.kernel.db import connect


def _resource(url: str, content: str, topic: str) -> LearningResource:
    return LearningResource.create(url=url).model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": topic,
        }
    )


def test_fts_search_is_stable_scoped_and_only_indexes_current_revision(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    react = _resource(
        "https://example.com/react",
        "# React\n\n## Hooks\n\n闭包捕获变量引用。\n",
        "React",
    )
    python = _resource(
        "https://example.com/python",
        "# Python\n\n## Closure\n\n闭包通过 cell 保存自由变量。\n",
        "Python",
    )
    store.replace_snapshot(react, [])
    store.replace_snapshot(python, [])
    search = DocumentSearch(store)

    all_hits = search.search("闭包", scope=SearchScope(mode="all"), limit=10)
    assert {hit.resource_id for hit in all_hits} == {react.resource_id, python.resource_id}
    assert all_hits == search.search("闭包", scope=SearchScope(mode="all"), limit=10)
    selected = search.search(
        "闭包",
        scope=SearchScope(mode="selected", resource_ids=[react.resource_id]),
        limit=10,
    )
    assert selected and {hit.resource_id for hit in selected} == {react.resource_id}

    updated = _resource(react.url, "# React\n\n新版本只讲并发渲染。\n", "React")
    store.replace_snapshot(updated, [])
    current_hits = search.search(
        "闭包",
        scope=SearchScope(mode="selected", resource_ids=[react.resource_id]),
        limit=10,
    )
    assert current_hits == []
    store.close()


def test_search_rejects_query_without_fts_terms(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    search = DocumentSearch(store)

    with pytest.raises(ValueError, match="可检索词"):
        search.search("!!! 🧭", scope=SearchScope(mode="all"))
    store.close()


def test_same_named_sections_use_stable_node_id_tie_break(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    resource = _resource(
        "https://example.com/repeated-sections",
        "# 文档\n\n## Replay\n\nneedle evidence。\n\n## Replay\n\nneedle evidence。\n",
        "重复章节",
    )
    store.replace_snapshot(resource, [])
    search = DocumentSearch(store)

    first = search.search(
        "needle evidence",
        scope=SearchScope(mode="selected", resource_ids=[resource.resource_id]),
        limit=20,
    )
    second = search.search(
        "needle evidence",
        scope=SearchScope(mode="selected", resource_ids=[resource.resource_id]),
        limit=20,
    )
    paragraphs = [hit for hit in first if hit.kind == "paragraph"]
    assert len(paragraphs) == 2
    assert [hit.node_id for hit in paragraphs] == sorted(hit.node_id for hit in paragraphs)
    assert second == first
    store.close()


def test_unresolved_selected_scope_fails_closed_before_search(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    search = DocumentSearch(store)

    with pytest.raises(ScopeResolutionError) as error:
        search.search(
            "闭包",
            scope=SearchScope(mode="selected", resource_ids=["missing-resource"]),
        )
    assert error.value.unresolved_resource_ids == ["missing-resource"]
    store.close()


def test_outline_expand_and_read_are_progressive_and_share_turn_budget(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    resource = _resource(
        "https://example.com/guide",
        "# Runtime\n\n导言。\n\n## Events\n\n事件是信封。\n\n### Replay\n\n回放复用事件流。\n",
        "Agent Runtime",
    )
    store.replace_snapshot(resource, [])
    search = DocumentSearch(store, turn_read_budget=12)

    outline = search.outline(resource.resource_id)
    assert [node.section_path for node in outline] == [
        "Runtime",
        "Runtime > Events",
        "Runtime > Events > Replay",
    ]
    descendants = search.expand(
        resource.resource_id,
        outline[1].node_id,
        max_depth=2,
        limit=10,
    )
    assert [node.section_path for node in descendants] == [
        "Runtime > Events",
        "Runtime > Events > Replay",
        "Runtime > Events > Replay",
    ]
    leaf = next(node for node in descendants if node.kind == "paragraph")
    first = search.read_node(
        resource.resource_id,
        leaf.node_id,
        max_chars=6,
        budget_key="turn-1",
    )
    assert first.untrusted is True
    assert len(first.content) == 6
    with pytest.raises(ReadBudgetExceeded):
        search.read_node(
            resource.resource_id,
            leaf.node_id,
            start=0,
            max_chars=7,
            budget_key="turn-1",
        )
    store.close()


def test_opening_v10_database_rebuilds_current_fts_projection(tmp_path: Path) -> None:
    database = tmp_path / "learning.db"
    resource = _resource(
        "https://example.com/v10-search",
        "# 文档\n\n迁移后仍可搜索稀疏索引。\n",
        "迁移测试",
    )
    store = SqliteLearningStore(database)
    store.replace_snapshot(resource, [])
    store.close()

    conn = connect(database)
    conn.execute("DROP TABLE document_nodes_fts")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()

    reopened = SqliteLearningStore(database)
    hits = DocumentSearch(reopened).search("稀疏索引", scope=SearchScope(mode="all"))
    assert hits and {hit.resource_id for hit in hits} == {resource.resource_id}
    reopened.close()


def test_fts_write_failure_rolls_back_current_revision_and_tree(tmp_path: Path) -> None:
    database = LearningDatabase(tmp_path / "learning.db")
    store = SqliteLearningStore(database)
    first = _resource("https://example.com/atomic-fts", "# 第一版\n\n旧正文。\n", "原子性")
    store.replace_snapshot(first, [])
    previous_revision = store.current_revision(first.resource_id)
    previous_nodes = store.document_nodes(first.resource_id)
    database.connection.execute("DROP TABLE document_nodes_fts")
    database.commit()

    second = _resource(first.url, "# 第二版\n\n新正文。\n", "原子性")
    with pytest.raises(sqlite3.OperationalError, match="document_nodes_fts"):
        store.replace_snapshot(second, [])

    assert store.current_revision(first.resource_id) == previous_revision
    assert store.document_nodes(first.resource_id) == previous_nodes
    store.close()

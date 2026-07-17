"""LearningResource / KnowledgeItem 的存储——记账（谁入了库，全局 KB 单池）。

``Store`` 协议是存储的结构化契约（ingest 编排依赖它，不认具体实现）；两种实现满足它：

- ``LearningStore``：**进程内 dict**、无任何 I/O——测试 / 快速用的内存实现（不再是骨架欠账）。
  dict 保序，读取顺序即写入顺序（确定性）。
- ``SqliteLearningStore``：**SQLite 持久化**——入库 item 重启后仍在、仍可锚定出题（M7 正式实现）。

竖切先穿透时用 dict 让 ingest 链路早点在事件脊柱上亮起来；M7 把持久化不变量（重启后 item 仍在）
落地为 SqliteLearningStore。因两者都满足 ``Store`` 协议，调用方（ingest 编排）**签名一字不改**
即可替换实现（兑现走骨架台账 #2 的"替换不改调用方"）。
"""

import json
from pathlib import Path
from typing import Any, Literal, Protocol

from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.models import (
    DocumentNode,
    KnowledgeItem,
    LearningResource,
    ResourceRevision,
)
from grandquiz.domain.learning.persistence import DatabaseSource, database_from

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 资源状态的枚举（与 LearningResource.status 一致）——协议与实现共用。
ResourceStatus = Literal["pending", "read", "failed"]


class Store(Protocol):
    """资源 / 知识点存储的结构化契约（ingest 编排的形参类型；全局 KB 单池，无 task 分区）。

    dict 版（``LearningStore``）与 SQLite 版（``SqliteLearningStore``）都结构上满足它，
    故调用方按此协议编程、可无改动地替换实现。方法语义见各实现的 docstring。
    """

    def add_resource(self, resource: LearningResource) -> None: ...
    def get_resource(self, resource_id: str) -> LearningResource | None: ...
    def set_resource_status(self, resource_id: str, status: ResourceStatus) -> None: ...
    def add_items(self, items: list[KnowledgeItem]) -> None: ...
    def replace_snapshot(self, resource: LearningResource, items: list[KnowledgeItem]) -> None: ...
    def current_revision(self, resource_id: str) -> ResourceRevision | None: ...
    def get_revision(self, revision_id: str) -> ResourceRevision | None: ...
    def document_outline(self, resource_id: str) -> list[DocumentNode]: ...
    def document_nodes(
        self, resource_id: str, *, revision_id: str | None = None
    ) -> list[DocumentNode]: ...
    def items_for_resource(self, resource_id: str) -> list[KnowledgeItem]: ...
    def all_items(self) -> list[KnowledgeItem]: ...
    def resource_topics(self) -> list[tuple[str, str]]: ...


class LearningStore:
    """资源 / 知识点的进程内账本（测试 / 快速用的内存实现）。dict 保序、确定性、无 I/O。"""

    def __init__(self) -> None:
        self._resources: dict[str, LearningResource] = {}
        self._items: dict[str, KnowledgeItem] = {}
        self._revisions: dict[str, ResourceRevision] = {}
        self._nodes: dict[str, DocumentNode] = {}

    # --- resources ---------------------------------------------------------
    def add_resource(self, resource: LearningResource) -> None:
        """按 ``resource_id`` 存 / 覆盖——回填 content / status 时以更新后的资源再调一次即可。"""
        self._resources[resource.resource_id] = resource

    def get_resource(self, resource_id: str) -> LearningResource | None:
        return self._resources.get(resource_id)

    def set_resource_status(self, resource_id: str, status: ResourceStatus) -> None:
        """把已存资源的 ``status`` 改成给定值（深读失败 → ``failed``）。资源不存在则报错。"""
        resource = self._resources.get(resource_id)
        if resource is None:
            raise KeyError(f"资源不存在：{resource_id}")
        self._resources[resource_id] = resource.model_copy(update={"status": status})

    # --- items -------------------------------------------------------------
    def add_items(self, items: list[KnowledgeItem]) -> None:
        """按 ``item_id`` 逐个入库（资源内唯一，ADR-0002）。仅获批 item 应流到此处。"""
        for item in items:
            self._items[item.item_id] = item

    def replace_snapshot(self, resource: LearningResource, items: list[KnowledgeItem]) -> None:
        """原子替换资源的获批知识快照；空列表表示获批清空。"""
        _validate_snapshot(resource, items)
        document = build_document_snapshot(resource)
        resources = dict(self._resources)
        revisions = dict(self._revisions)
        nodes = dict(self._nodes)
        stored_items = {
            item_id: item
            for item_id, item in self._items.items()
            if item.resource_id != resource.resource_id
        }
        stored_resource = resource
        if document is not None:
            stored_resource = resource.model_copy(
                update={"current_revision_id": document.revision.revision_id}
            )
            revisions[document.revision.revision_id] = document.revision
            nodes.update((node.node_id, node) for node in document.nodes)
        resources[resource.resource_id] = stored_resource
        stored_items.update((item.item_id, item) for item in items)
        self._resources = resources
        self._items = stored_items
        self._revisions = revisions
        self._nodes = nodes

    def current_revision(self, resource_id: str) -> ResourceRevision | None:
        resource = self._resources.get(resource_id)
        if resource is None or resource.current_revision_id is None:
            return None
        return self._revisions.get(resource.current_revision_id)

    def get_revision(self, revision_id: str) -> ResourceRevision | None:
        return self._revisions.get(revision_id)

    def document_outline(self, resource_id: str) -> list[DocumentNode]:
        return [node for node in self.document_nodes(resource_id) if node.kind == "section"]

    def document_nodes(
        self, resource_id: str, *, revision_id: str | None = None
    ) -> list[DocumentNode]:
        revision = (
            self.get_revision(revision_id) if revision_id else self.current_revision(resource_id)
        )
        if revision is None or revision.resource_id != resource_id:
            return []
        return sorted(
            (node for node in self._nodes.values() if node.revision_id == revision.revision_id),
            key=lambda node: (node.ordinal, node.node_id),
        )

    def items_for_resource(self, resource_id: str) -> list[KnowledgeItem]:
        """某资源下已入库的 item，**按 item_id 升序**（与 SQLite 版一致的确定性顺序契约）。"""
        matched = [item for item in self._items.values() if item.resource_id == resource_id]
        return sorted(matched, key=lambda item: item.item_id)

    def all_items(self) -> list[KnowledgeItem]:
        """**全库**所有 item，**按 item_id 升序**——全局 KB 唯一的选题读（不按 resource 过滤）。

        顺序契约须与 SqliteLearningStore 一致：选题 ``select_target`` 用 ``rng.choice`` 按下标选，
        两实现顺序不同则同种子选中不同 item（跨实现 / replay 不对齐）。故两版都按 item_id 排序。
        """
        return sorted(self._items.values(), key=lambda item: item.item_id)

    def resource_topics(self) -> list[tuple[str, str]]:
        """全库已抽出 topic 的资源目录：``[(resource_id, topic)]``，**按 resource_id 升序**。

        只列 ``topic is not None`` 的资源（pending / failed 无 topic → 不进目录）。供 domain 目录
        注入渲染全库库存清单（GKB-S3）；升序确定性契约须与 SqliteLearningStore 一致。
        """
        pairs = [(r.resource_id, r.topic) for r in self._resources.values() if r.topic is not None]
        return sorted(pairs, key=lambda pair: pair[0])


class SqliteLearningStore:
    """资源 / 知识点的 SQLite 持久化账本（M7 正式实现，满足 ``Store`` 协议；全局 KB 单池）。

    ``db_path`` 是 learning 数据的**独立 db 文件**（与 trace.db 分开）；``__init__`` 打开连接并跑
    ``migrate``（幂等，重复开同一文件不会重复建表；user_version 独立于 trace.db）。模型 ↔ 行经
    ``model_dump()`` / ``model_validate``；list 字段（evidence）存 JSON 文本。``add_*`` 用
    ``INSERT OR REPLACE`` 保持与 dict 版一致的幂等覆盖语义（同 URL 重 ingest → 同 resource_id →
    天然去重）。SQLite 是 I/O 但确定（同操作同状态），schema 无时间戳列，故不破坏 replay。
    """

    def __init__(self, db_path: DatabaseSource) -> None:
        self._db = database_from(db_path)
        self._conn = self._db.connection
        self._backfill_legacy_revisions()

    # --- resources ---------------------------------------------------------
    def add_resource(self, resource: LearningResource) -> None:
        """按 ``resource_id`` 存 / 覆盖（``INSERT OR REPLACE``）。``trusted`` 存 0/1。"""
        self._conn.execute(
            "INSERT INTO resources "
            "(resource_id, url, raw_content, content_hash, trusted, status, topic, "
            "current_revision_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(resource_id) DO UPDATE SET "
            "url=excluded.url, raw_content=excluded.raw_content, "
            "content_hash=excluded.content_hash, trusted=excluded.trusted, "
            "status=excluded.status, topic=excluded.topic, "
            "current_revision_id=COALESCE("
            "excluded.current_revision_id, resources.current_revision_id)",
            (
                resource.resource_id,
                resource.url,
                resource.raw_content,
                resource.content_hash,
                int(resource.trusted),
                resource.status,
                resource.topic,
                resource.current_revision_id,
            ),
        )
        self._db.commit()

    def get_resource(self, resource_id: str) -> LearningResource | None:
        row = self._conn.execute(
            "SELECT resource_id, url, raw_content, content_hash, trusted, status, topic, "
            "current_revision_id "
            "FROM resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        if row is None:
            return None
        return LearningResource.model_validate(
            {
                "resource_id": str(row[0]),
                "url": row[1],
                "raw_content": row[2],
                "content_hash": row[3],
                "trusted": bool(row[4]),
                "status": row[5],
                "topic": row[6],
                "current_revision_id": row[7],
            }
        )

    def set_resource_status(self, resource_id: str, status: ResourceStatus) -> None:
        """把已存资源的 ``status`` 改成给定值；资源不存在则报错（同 dict 版语义）。"""
        resource = self.get_resource(resource_id)
        if resource is None:
            raise KeyError(f"资源不存在：{resource_id}")
        self.add_resource(resource.model_copy(update={"status": status}))

    # --- items -------------------------------------------------------------
    def add_items(self, items: list[KnowledgeItem]) -> None:
        """按 ``item_id`` 逐个入库（``INSERT OR REPLACE``）。evidence 存稳定序 JSON。"""
        for item in items:
            data = item.model_dump()
            evidence_json = json.dumps(data["evidence"], sort_keys=True, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO knowledge_items "
                "(item_id, resource_id, concept, summary, evidence, confidence, concept_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id) DO UPDATE SET "
                "resource_id=excluded.resource_id, concept=excluded.concept, "
                "summary=excluded.summary, evidence=excluded.evidence, "
                "confidence=excluded.confidence, concept_key=excluded.concept_key",
                (
                    item.item_id,
                    item.resource_id,
                    item.concept,
                    item.summary,
                    evidence_json,
                    item.confidence,
                    item.concept_key,
                ),
            )
        self._db.commit()

    def replace_snapshot(self, resource: LearningResource, items: list[KnowledgeItem]) -> None:
        """在一个事务中 upsert revision，并删除本次获批快照之外的旧 item。"""
        _validate_snapshot(resource, items)
        document = build_document_snapshot(resource)
        previous = self.get_resource(resource.resource_id)
        previous_revision_id = previous.current_revision_id if previous is not None else None
        with self._db.transaction():
            staged_resource = resource.model_copy(
                update={"current_revision_id": previous_revision_id}
            )
            self._upsert_resource(staged_resource)
            if document is not None:
                self._upsert_revision(document.revision)
                self._replace_document_nodes(document.revision.revision_id, document.nodes)
                self._conn.execute(
                    "UPDATE resources SET current_revision_id = ? WHERE resource_id = ?",
                    (document.revision.revision_id, resource.resource_id),
                )
            self._upsert_items(items)
            item_ids = [item.item_id for item in items]
            if item_ids:
                placeholders = ", ".join("?" for _ in item_ids)
                self._conn.execute(
                    f"DELETE FROM knowledge_items WHERE resource_id = ? "
                    f"AND item_id NOT IN ({placeholders})",
                    (resource.resource_id, *item_ids),
                )
            else:
                self._conn.execute(
                    "DELETE FROM knowledge_items WHERE resource_id = ?",
                    (resource.resource_id,),
                )

    def _upsert_resource(self, resource: LearningResource) -> None:
        self._conn.execute(
            "INSERT INTO resources "
            "(resource_id, url, raw_content, content_hash, trusted, status, topic, "
            "current_revision_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(resource_id) DO UPDATE SET "
            "url=excluded.url, raw_content=excluded.raw_content, "
            "content_hash=excluded.content_hash, trusted=excluded.trusted, "
            "status=excluded.status, topic=excluded.topic, "
            "current_revision_id=excluded.current_revision_id",
            (
                resource.resource_id,
                resource.url,
                resource.raw_content,
                resource.content_hash,
                int(resource.trusted),
                resource.status,
                resource.topic,
                resource.current_revision_id,
            ),
        )

    def _upsert_revision(self, revision: ResourceRevision) -> None:
        self._conn.execute(
            "INSERT INTO resource_revisions "
            "(revision_id, resource_id, content_hash, raw_content, trusted) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(revision_id) DO UPDATE SET "
            "resource_id=excluded.resource_id, content_hash=excluded.content_hash, "
            "raw_content=excluded.raw_content, trusted=excluded.trusted",
            (
                revision.revision_id,
                revision.resource_id,
                revision.content_hash,
                revision.raw_content,
                int(revision.trusted),
            ),
        )

    def _replace_document_nodes(self, revision_id: str, nodes: tuple[DocumentNode, ...]) -> None:
        self._conn.execute("DELETE FROM document_nodes WHERE revision_id = ?", (revision_id,))
        for node in nodes:
            self._conn.execute(
                "INSERT INTO document_nodes "
                "(node_id, revision_id, parent_node_id, kind, ordinal, depth, title, "
                "section_path, start_offset, end_offset, content_fingerprint, synthetic, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    node.node_id,
                    node.revision_id,
                    node.parent_node_id,
                    node.kind,
                    node.ordinal,
                    node.depth,
                    node.title,
                    node.section_path,
                    node.start_offset,
                    node.end_offset,
                    node.content_fingerprint,
                    int(node.synthetic),
                    node.summary,
                ),
            )

    def _upsert_items(self, items: list[KnowledgeItem]) -> None:
        for item in items:
            data = item.model_dump()
            evidence_json = json.dumps(data["evidence"], sort_keys=True, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO knowledge_items "
                "(item_id, resource_id, concept, summary, evidence, confidence, concept_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id) DO UPDATE SET "
                "resource_id=excluded.resource_id, concept=excluded.concept, "
                "summary=excluded.summary, evidence=excluded.evidence, "
                "confidence=excluded.confidence, concept_key=excluded.concept_key",
                (
                    item.item_id,
                    item.resource_id,
                    item.concept,
                    item.summary,
                    evidence_json,
                    item.confidence,
                    item.concept_key,
                ),
            )

    def items_for_resource(self, resource_id: str) -> list[KnowledgeItem]:
        """某资源下已入库的 item，按 ``item_id`` 升序（含资源内序号，确定性且稳定）。"""
        cursor = self._conn.execute(
            "SELECT item_id, resource_id, concept, summary, evidence, confidence, concept_key "
            "FROM knowledge_items WHERE resource_id = ? ORDER BY item_id",
            (resource_id,),
        )
        return [_row_to_item(row) for row in cursor.fetchall()]

    def all_items(self) -> list[KnowledgeItem]:
        """**全库**所有 item，按 ``item_id`` 升序——全局 KB 唯一的选题读（不按 resource 过滤）。

        与 dict 版同一顺序契约（选题 replay 命门）：全表按 item_id 排序、复用 ``_row_to_item``。
        """
        cursor = self._conn.execute(
            "SELECT item_id, resource_id, concept, summary, evidence, confidence, concept_key "
            "FROM knowledge_items ORDER BY item_id"
        )
        return [_row_to_item(row) for row in cursor.fetchall()]

    def resource_topics(self) -> list[tuple[str, str]]:
        """全库已抽出 topic 的资源目录：``[(resource_id, topic)]``，按 ``resource_id`` 升序。

        与 dict 版同一目录契约：只列 ``topic IS NOT NULL`` 的资源、稳定按 resource_id 升序
        （目录注入确定性渲染的地基，GKB-S3）。
        """
        cursor = self._conn.execute(
            "SELECT resource_id, topic FROM resources WHERE topic IS NOT NULL ORDER BY resource_id"
        )
        return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]

    def current_revision(self, resource_id: str) -> ResourceRevision | None:
        row = self._conn.execute(
            "SELECT rr.revision_id, rr.resource_id, rr.content_hash, rr.raw_content, rr.trusted "
            "FROM resources AS r JOIN resource_revisions AS rr "
            "ON rr.revision_id = r.current_revision_id WHERE r.resource_id = ?",
            (resource_id,),
        ).fetchone()
        return None if row is None else _row_to_revision(row)

    def get_revision(self, revision_id: str) -> ResourceRevision | None:
        row = self._conn.execute(
            "SELECT revision_id, resource_id, content_hash, raw_content, trusted "
            "FROM resource_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        return None if row is None else _row_to_revision(row)

    def document_outline(self, resource_id: str) -> list[DocumentNode]:
        return [node for node in self.document_nodes(resource_id) if node.kind == "section"]

    def document_nodes(
        self, resource_id: str, *, revision_id: str | None = None
    ) -> list[DocumentNode]:
        revision = (
            self.get_revision(revision_id) if revision_id else self.current_revision(resource_id)
        )
        if revision is None or revision.resource_id != resource_id:
            return []
        cursor = self._conn.execute(
            "SELECT node_id, revision_id, parent_node_id, kind, ordinal, depth, title, "
            "section_path, start_offset, end_offset, content_fingerprint, synthetic, summary "
            "FROM document_nodes WHERE revision_id = ? ORDER BY ordinal, node_id",
            (revision.revision_id,),
        )
        return [_row_to_document_node(row) for row in cursor.fetchall()]

    def _backfill_legacy_revisions(self) -> None:
        rows = self._conn.execute(
            "SELECT resource_id, url, raw_content, content_hash, trusted, status, topic "
            "FROM resources WHERE current_revision_id IS NULL "
            "AND raw_content IS NOT NULL AND content_hash IS NOT NULL AND status = 'read'"
        ).fetchall()
        for row in rows:
            resource = LearningResource.model_validate(
                {
                    "resource_id": str(row[0]),
                    "url": row[1],
                    "raw_content": row[2],
                    "content_hash": row[3],
                    "trusted": bool(row[4]),
                    "status": row[5],
                    "topic": row[6],
                }
            )
            document = build_document_snapshot(resource)
            if document is None:  # pragma: no cover - SQL predicate guarantees both fields
                continue
            with self._db.transaction():
                self._upsert_revision(document.revision)
                self._replace_document_nodes(document.revision.revision_id, document.nodes)
                self._conn.execute(
                    "UPDATE resources SET current_revision_id = ? WHERE resource_id = ?",
                    (document.revision.revision_id, resource.resource_id),
                )

    def close(self) -> None:
        """关闭底层连接（跨会话验收：关闭后用同一 db_path 重开，数据仍在）。"""
        self._db.close()


def _row_to_item(row: Any) -> KnowledgeItem:
    # row 来自 sqlite cursor（动态类型），逐列显式转型再交 pydantic 校验（同 trace.py 的模式）。
    return KnowledgeItem.model_validate(
        {
            "item_id": str(row[0]),
            "resource_id": str(row[1]),
            "concept": row[2],
            "summary": row[3],
            "evidence": json.loads(row[4]),
            "confidence": float(row[5]),
            "concept_key": row[6],
        }
    )


def _row_to_revision(row: Any) -> ResourceRevision:
    return ResourceRevision.model_validate(
        {
            "revision_id": str(row[0]),
            "resource_id": str(row[1]),
            "content_hash": row[2],
            "raw_content": row[3],
            "trusted": bool(row[4]),
        }
    )


def _row_to_document_node(row: Any) -> DocumentNode:
    return DocumentNode.model_validate(
        {
            "node_id": str(row[0]),
            "revision_id": str(row[1]),
            "parent_node_id": row[2],
            "kind": row[3],
            "ordinal": int(row[4]),
            "depth": int(row[5]),
            "title": row[6],
            "section_path": row[7],
            "start_offset": int(row[8]),
            "end_offset": int(row[9]),
            "content_fingerprint": row[10],
            "synthetic": bool(row[11]),
            "summary": row[12],
        }
    )


def _validate_snapshot(resource: LearningResource, items: list[KnowledgeItem]) -> None:
    mismatched = [item.item_id for item in items if item.resource_id != resource.resource_id]
    if mismatched:
        raise ValueError(
            f"快照含不属于资源 {resource.resource_id} 的 KnowledgeItem：{', '.join(mismatched)}"
        )

"""LearningTask / LearningResource / KnowledgeItem 的存储——记账（谁入了库）。

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

from grandquiz.domain.learning.models import KnowledgeItem, LearningResource, LearningTask
from grandquiz.kernel.db import connect, migrate

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 资源状态的枚举（与 LearningResource.status 一致）——协议与实现共用。
ResourceStatus = Literal["pending", "read", "failed"]


class Store(Protocol):
    """任务 / 资源 / 知识点存储的结构化契约（ingest 编排的形参类型）。

    dict 版（``LearningStore``）与 SQLite 版（``SqliteLearningStore``）都结构上满足它，
    故调用方按此协议编程、可无改动地替换实现。方法语义见各实现的 docstring。
    """

    def add_task(self, task: LearningTask) -> None: ...
    def get_task(self, task_id: str) -> LearningTask | None: ...
    def add_resource(self, resource: LearningResource) -> None: ...
    def get_resource(self, resource_id: str) -> LearningResource | None: ...
    def set_resource_status(self, resource_id: str, status: ResourceStatus) -> None: ...
    def add_items(self, items: list[KnowledgeItem]) -> None: ...
    def items_for_resource(self, resource_id: str) -> list[KnowledgeItem]: ...
    def items_for_task(self, task_id: str) -> list[KnowledgeItem]: ...
    def all_items(self) -> list[KnowledgeItem]: ...


class LearningStore:
    """任务 / 资源 / 知识点的进程内账本（测试 / 快速用的内存实现）。dict 保序、确定性、无 I/O。"""

    def __init__(self) -> None:
        self._tasks: dict[str, LearningTask] = {}
        self._resources: dict[str, LearningResource] = {}
        self._items: dict[str, KnowledgeItem] = {}

    # --- tasks -------------------------------------------------------------
    def add_task(self, task: LearningTask) -> None:
        """幂等：同 ``task_id`` 覆盖（重复 ingest 同一 task 不报错、不重复建档）。"""
        self._tasks[task.task_id] = task

    def get_task(self, task_id: str) -> LearningTask | None:
        return self._tasks.get(task_id)

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

    def items_for_resource(self, resource_id: str) -> list[KnowledgeItem]:
        """某资源下已入库的 item，**按 item_id 升序**（与 SQLite 版一致的确定性顺序契约）。"""
        matched = [item for item in self._items.values() if item.resource_id == resource_id]
        return sorted(matched, key=lambda item: item.item_id)

    def items_for_task(self, task_id: str) -> list[KnowledgeItem]:
        """某任务下已入库的 item，**按 item_id 升序**——经 resource_id → task_id 两跳聚合。

        顺序契约须与 SqliteLearningStore 一致：选题 ``select_target`` 用 ``rng.choice`` 按下标选，
        两实现顺序不同则同种子选中不同 item（跨实现 / replay 不对齐）。故两版都按 item_id 排序。
        """
        resource_ids = {r.resource_id for r in self._resources.values() if r.task_id == task_id}
        matched = [item for item in self._items.values() if item.resource_id in resource_ids]
        return sorted(matched, key=lambda item: item.item_id)

    def all_items(self) -> list[KnowledgeItem]:
        """**全库**所有 item，**按 item_id 升序**——全局 KB 读（不按 task / resource 过滤）。

        顺序契约须与 SqliteLearningStore 一致（同 items_for_task 的理由）：选题 ``select_target``
        用 ``rng.choice`` 按下标选，两实现顺序不同则同种子选中不同 item（跨实现 / replay 不对齐）。
        """
        return sorted(self._items.values(), key=lambda item: item.item_id)


class SqliteLearningStore:
    """任务 / 资源 / 知识点的 SQLite 持久化账本（M7 正式实现，满足 ``Store`` 协议）。

    ``db_path`` 是 learning 数据的**独立 db 文件**（与 trace.db 分开）；``__init__`` 打开连接并跑
    ``migrate``（幂等，重复开同一文件不会重复建表；user_version 独立于 trace.db）。模型 ↔ 行经
    ``model_dump()`` / ``model_validate``；list 字段（evidence）存 JSON 文本。``add_*`` 用
    ``INSERT OR REPLACE`` 保持与 dict 版一致的幂等覆盖语义。SQLite 是 I/O 但确定（同操作同状态），
    schema 无时间戳列，故不破坏 replay。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn = connect(db_path)
        migrate(self._conn, _LEARNING_MIGRATIONS_DIR)

    # --- tasks -------------------------------------------------------------
    def add_task(self, task: LearningTask) -> None:
        """幂等：``INSERT OR REPLACE``（同 ``task_id`` 覆盖，同 dict 版语义）。"""
        self._conn.execute(
            "INSERT OR REPLACE INTO tasks (task_id, title, domain, language) VALUES (?, ?, ?, ?)",
            (task.task_id, task.title, task.domain, task.language),
        )
        self._conn.commit()

    def get_task(self, task_id: str) -> LearningTask | None:
        row = self._conn.execute(
            "SELECT task_id, title, domain, language FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return LearningTask.model_validate(
            {"task_id": str(row[0]), "title": row[1], "domain": row[2], "language": row[3]}
        )

    # --- resources ---------------------------------------------------------
    def add_resource(self, resource: LearningResource) -> None:
        """按 ``resource_id`` 存 / 覆盖（``INSERT OR REPLACE``）。``trusted`` 存 0/1。"""
        self._conn.execute(
            "INSERT OR REPLACE INTO resources "
            "(resource_id, task_id, url, raw_content, content_hash, trusted, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                resource.resource_id,
                resource.task_id,
                resource.url,
                resource.raw_content,
                resource.content_hash,
                int(resource.trusted),
                resource.status,
            ),
        )
        self._conn.commit()

    def get_resource(self, resource_id: str) -> LearningResource | None:
        row = self._conn.execute(
            "SELECT resource_id, task_id, url, raw_content, content_hash, trusted, status "
            "FROM resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()
        if row is None:
            return None
        return LearningResource.model_validate(
            {
                "resource_id": str(row[0]),
                "task_id": str(row[1]),
                "url": row[2],
                "raw_content": row[3],
                "content_hash": row[4],
                "trusted": bool(row[5]),
                "status": row[6],
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
                "INSERT OR REPLACE INTO knowledge_items "
                "(item_id, resource_id, concept, summary, evidence, confidence, concept_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
        self._conn.commit()

    def items_for_resource(self, resource_id: str) -> list[KnowledgeItem]:
        """某资源下已入库的 item，按 ``item_id`` 升序（含资源内序号，确定性且稳定）。"""
        cursor = self._conn.execute(
            "SELECT item_id, resource_id, concept, summary, evidence, confidence, concept_key "
            "FROM knowledge_items WHERE resource_id = ? ORDER BY item_id",
            (resource_id,),
        )
        return [_row_to_item(row) for row in cursor.fetchall()]

    def items_for_task(self, task_id: str) -> list[KnowledgeItem]:
        """某任务下已入库的 item——经 ``knowledge_items ⋈ resources`` 两跳聚合，按 item_id 升序。"""
        cursor = self._conn.execute(
            "SELECT ki.item_id, ki.resource_id, ki.concept, ki.summary, ki.evidence, "
            "ki.confidence, ki.concept_key "
            "FROM knowledge_items ki JOIN resources r ON ki.resource_id = r.resource_id "
            "WHERE r.task_id = ? ORDER BY ki.item_id",
            (task_id,),
        )
        return [_row_to_item(row) for row in cursor.fetchall()]

    def all_items(self) -> list[KnowledgeItem]:
        """**全库**所有 item，按 ``item_id`` 升序——全局 KB 读（不 join resources、不按 task 过滤）。

        与 dict 版同一顺序契约（选题 replay 命门）：全表按 item_id 排序、复用 ``_row_to_item``。
        """
        cursor = self._conn.execute(
            "SELECT item_id, resource_id, concept, summary, evidence, confidence, concept_key "
            "FROM knowledge_items ORDER BY item_id"
        )
        return [_row_to_item(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """关闭底层连接（跨会话验收：关闭后用同一 db_path 重开，数据仍在）。"""
        self._conn.close()


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

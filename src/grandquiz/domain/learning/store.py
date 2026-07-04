"""进程内 dict 存储——LearningTask / LearningResource / KnowledgeItem 的记账。

# SKELETON(M7): dict 假装持久化，正式 SQLite 存储见 docs/skeleton-ledger.md #2

竖切先穿透：内部纯 dict、**无任何 I/O**，让 ingest 链路早点在事件脊柱上亮起来；M7 换成
SQLite 支持的存储时，调用方（ingest 编排）签名不变。此刻它只负责"记账"（谁入了库），
真正的持久化不变量（重启后 item 仍在）留给 M7 验收。
"""

from typing import Literal

from grandquiz.domain.learning.models import KnowledgeItem, LearningResource, LearningTask


class LearningStore:
    """任务 / 资源 / 知识点的进程内账本。dict 保序，读取顺序即写入顺序（确定性）。"""

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

    def set_resource_status(
        self, resource_id: str, status: Literal["pending", "read", "failed"]
    ) -> None:
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
        """某资源下已入库的 item（保写入序）。"""
        return [item for item in self._items.values() if item.resource_id == resource_id]

    def items_for_task(self, task_id: str) -> list[KnowledgeItem]:
        """某任务下已入库的 item——经 ``item.resource_id → resource.task_id`` 两跳聚合。"""
        resource_ids = {r.resource_id for r in self._resources.values() if r.task_id == task_id}
        return [item for item in self._items.values() if item.resource_id in resource_ids]

"""跨会话持久化——M7 的命门验收（"重启后仍薄弱优先出题"的地基）。

用 ``tmp_path`` 的**真实 db 文件**（非 :memory:，否则连接一关数据就没了）：写入任务 / 资源 / item +
喂几个薄弱 / 观察中 / 销账概念 → **关闭连接、丢弃对象** → 用同一 ``db_path`` 新开 store / memory →
断言 item 仍在、薄弱点仍在且状态 / 连对 / 判决历史正确、weak_item_ids 一致。store 与 memory 共用
同一 learning db 文件（learning 数据独立于 trace.db）。
"""

from pathlib import Path

from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource, LearningTask
from grandquiz.domain.learning.store import SqliteLearningStore


def _item(resource_id: str, index: int, concept: str) -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id=resource_id,
        index=index,
        concept=concept,
        summary=f"{concept} 的摘要",
        evidence=[Evidence(quote=f"{concept} 的原文证据")],
        confidence=0.8,
    )


def test_items_and_weak_points_survive_close_and_reopen(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"

    # --- 会话 1：写入 -------------------------------------------------------
    store1 = SqliteLearningStore(db)
    memory1 = SqliteLearningMemory(db)
    task = LearningTask.create("React")
    resource = LearningResource.create(task_id=task.task_id, url="https://example.com/react")
    store1.add_task(task)
    store1.add_resource(resource)
    items = [_item(resource.resource_id, i, c) for i, c in enumerate(["闭包", "提升", "事件循环"])]
    store1.add_items(items)
    weak_id, observing_id, discharged_id = (item.item_id for item in items)

    # 喂判决：闭包 → 薄弱；提升 → 观察中（错后对）；事件循环 → 销账（错、对、对）。
    memory1.record_verdict(weak_id, "错")
    memory1.record_verdict(observing_id, "错")
    memory1.record_verdict(observing_id, "对")
    memory1.record_verdict(discharged_id, "错")
    memory1.record_verdict(discharged_id, "对")
    memory1.record_verdict(discharged_id, "对")

    assert memory1.weak_item_ids() == {weak_id, observing_id}

    # --- 关闭连接、丢弃对象（模拟进程退出 / 重启）---------------------------
    store1.close()
    memory1.close()
    del store1, memory1

    # --- 会话 2：用同一 db_path 新开，断言数据仍在 --------------------------
    store2 = SqliteLearningStore(db)
    memory2 = SqliteLearningMemory(db)

    # item 仍在、逐字段一致、仍可经 task 两跳锚定出题。
    reloaded = store2.items_for_task(task.task_id)
    assert reloaded == items
    assert store2.get_task(task.task_id) == task
    assert store2.get_resource(resource.resource_id) == resource

    # 薄弱点仍在、状态 / 连对 / 判决历史正确（"重启后仍薄弱优先出题"的地基）。
    assert memory2.weak_item_ids() == {weak_id, observing_id}
    assert memory2.state_of(weak_id) == "薄弱"
    assert memory2.state_of(observing_id) == "观察中"
    assert memory2.state_of(discharged_id) is None  # 销账后不再追踪，重启仍不追踪

    weak_rec = memory2.record_of(weak_id)
    assert weak_rec is not None
    assert weak_rec.consecutive_correct == 0
    assert weak_rec.verdict_history == ["错"]

    observing_rec = memory2.record_of(observing_id)
    assert observing_rec is not None
    assert observing_rec.consecutive_correct == 1
    assert observing_rec.verdict_history == ["错", "对"]

    # 会话 2 继续记账：观察中 + 对 → 销账，跨会话状态机接续无缝。
    transition = memory2.record_verdict(observing_id, "对")
    assert transition.to_state == "销账"
    assert memory2.weak_item_ids() == {weak_id}

    store2.close()
    memory2.close()

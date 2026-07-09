"""LearningStore 记账测试（缝 2 确定性核心）——纯 dict、无 I/O。

被测：add/get 往返、status 更新、items_for_resource 保序、all_items 全库读（全局 KB，ADR-0005）。
"""

from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.store import LearningStore


def _item(resource_id: str, index: int, concept: str) -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id=resource_id,
        index=index,
        concept=concept,
        summary="摘要",
        evidence=[Evidence(quote="原文片段")],
        confidence=0.8,
    )


def test_add_and_get_resource() -> None:
    store = LearningStore()
    resource = LearningResource.create(url="https://example.com/a")
    store.add_resource(resource)
    assert store.get_resource(resource.resource_id) == resource
    assert store.get_resource("nope") is None


def test_set_resource_status_updates_stored_resource() -> None:
    store = LearningStore()
    resource = LearningResource.create(url="https://example.com/a")
    store.add_resource(resource)
    store.set_resource_status(resource.resource_id, "failed")
    updated = store.get_resource(resource.resource_id)
    assert updated is not None
    assert updated.status == "failed"


def test_items_for_resource_returns_only_that_resource_in_order() -> None:
    store = LearningStore()
    store.add_items([_item("r1", 0, "闭包"), _item("r1", 1, "提升"), _item("r2", 0, "无关")])
    got = store.items_for_resource("r1")
    assert [i.item_id for i in got] == ["r1#000", "r1#001"]
    assert [i.concept for i in got] == ["闭包", "提升"]


# --- all_items：全库全局读（不按 resource 过滤，按 item_id 升序） --------------------


def test_all_items_empty_returns_empty() -> None:
    assert LearningStore().all_items() == []


def test_all_items_single_resource_item_id_sorted() -> None:
    # 单资源：即便乱序入库，all_items 也按 item_id 升序（确定性顺序契约）。
    store = LearningStore()
    store.add_items([_item("r1", 1, "提升"), _item("r1", 0, "闭包")])
    assert [i.item_id for i in store.all_items()] == ["r1#000", "r1#001"]


def test_all_items_spans_all_resources_item_id_sorted() -> None:
    # 全局 KB：跨资源全库读——不按 resource 过滤（修 #2 的读语义；ADR-0005 无 task 分区）。
    store = LearningStore()
    ra = LearningResource.create(url="https://example.com/a")
    rb = LearningResource.create(url="https://example.com/b")
    for r in (ra, rb):
        store.add_resource(r)
    store.add_items([_item(ra.resource_id, 0, "x"), _item(rb.resource_id, 0, "y")])
    got = store.all_items()
    assert {i.concept for i in got} == {"x", "y"}  # 跨资源全收
    assert [i.item_id for i in got] == sorted(i.item_id for i in got)  # 稳定升序

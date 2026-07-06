"""选题测试（缝 2，确定性核心单元缝）——纯函数、直接 TDD。

被测：种子化 rng → 确定性选择（同 seed 同结果、可回放）；空列表 → raise（调用方在空库时
应先走拒答分支，不该走到选题）；薄弱优先候选集（有薄弱概念时新概念不进集，eval case 5）。
"""

import pytest

from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem
from grandquiz.domain.learning.selection import select_target
from grandquiz.kernel.clock import new_rng


def _item(index: int, concept: str) -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id="res",
        index=index,
        concept=concept,
        summary=f"{concept} 的摘要",
        evidence=[Evidence(quote=f"{concept} 的证据")],
        confidence=0.9,
    )


def _items() -> list[KnowledgeItem]:
    return [_item(i, c) for i, c in enumerate(["闭包", "变量提升", "事件循环", "原型链"])]


def test_same_seed_yields_same_choice() -> None:
    # 同 seed 两次独立 rng → 选同一个 item（确定、可回放）。
    items = _items()
    first = select_target(items, rng=new_rng(1234))
    second = select_target(items, rng=new_rng(1234))
    assert first.item_id == second.item_id


def test_choice_is_a_member_of_the_pool() -> None:
    # 选出的 item 必属输入池（不凭空造）。
    items = _items()
    chosen = select_target(items, rng=new_rng(7))
    assert chosen in items


def test_different_seeds_can_select_different_items() -> None:
    # 不同 seed 在足够大的池里能选出不同 item——证明选择确实受 rng 驱动（非恒返首个）。
    items = _items()
    picks = {select_target(items, rng=new_rng(seed)).item_id for seed in range(50)}
    assert len(picks) > 1


def test_empty_pool_raises() -> None:
    # 空库不该进入选题（eval case 2 由 assess_once 的拒答分支拦下）；护栏 raise。
    with pytest.raises(ValueError):
        select_target([], rng=new_rng(0))


# --- 薄弱优先（memory 接入，eval case 5）------------------------------------------


def test_none_memory_selects_from_full_pool() -> None:
    # 未接记忆（M3.2 行为）：从全集选——足够多 seed 能选出多个不同 item。
    items = _items()
    picks = {select_target(items, rng=new_rng(seed), memory=None).item_id for seed in range(50)}
    assert len(picks) > 1


def test_empty_memory_selects_from_full_pool() -> None:
    # 记忆里没有薄弱概念：等价于全集随机（保持 M3.2 行为）。
    items = _items()
    empty = LearningMemory()
    picks = {select_target(items, rng=new_rng(seed), memory=empty).item_id for seed in range(50)}
    assert len(picks) > 1


def test_weak_priority_excludes_new_concepts() -> None:
    # eval case 5：有薄弱概念时，只从薄弱 / 观察中的 item 里选，新概念（不在记忆）被排除。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")  # 薄弱
    memory.record_verdict(items[1].item_id, "错")  # 薄弱 →
    memory.record_verdict(items[1].item_id, "对")  # 观察中（仍在候选集）
    weak_ids = {items[0].item_id, items[1].item_id}
    picks = {select_target(items, rng=new_rng(seed), memory=memory).item_id for seed in range(50)}
    assert picks <= weak_ids  # 只会选到薄弱 / 观察中的 item
    assert items[2].item_id not in picks  # 新概念被排除
    assert items[3].item_id not in picks


def test_discharged_concept_leaves_candidate_set() -> None:
    # 销账后概念退出薄弱集：不再进候选集（连对两次已掌握）。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")
    memory.record_verdict(items[0].item_id, "对")
    memory.record_verdict(items[0].item_id, "对")  # 销账
    memory.record_verdict(items[1].item_id, "错")  # 仅剩 items[1] 薄弱
    picks = {select_target(items, rng=new_rng(seed), memory=memory).item_id for seed in range(50)}
    assert picks == {items[1].item_id}


def test_weak_item_not_in_pool_falls_back_to_full_set() -> None:
    # 兜底护栏：薄弱概念的 item 已不在传入 items 里 → 候选集空 → 回退全集（不 raise、不空转）。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict("不在库里的幽灵 item", "错")
    chosen = select_target(items, rng=new_rng(3), memory=memory)
    assert chosen in items

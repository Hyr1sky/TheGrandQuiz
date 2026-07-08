"""选题测试（缝 2，确定性核心单元缝）——纯函数、直接 TDD。

被测：种子化 rng → 确定性选择（同 seed 同结果、可回放）；空列表 → raise（调用方在空库时
应先走拒答分支，不该走到选题）；**覆盖优先 + 兜底 remediation + 可选 focus**（R1-S7）：

- ``focus="mixed"``（默认）：候选 = 未考过（unasked）若非空 → 否则薄弱 → 否则全集。
  关键：有薄弱 + 有未考过 → 选未考过（**不锁死薄弱**，修 dogfood "6 题锁死同一 item"）。
- ``focus="new"``：未考过若非空 → 否则全集（**不兜底薄弱**）。
- ``focus="weak"``：薄弱若非空 → 否则未考过 → 否则全集（"复习薄弱"）。

断言 mutation 可杀：候选集选错分支（排他 vs 覆盖、兜底薄弱 vs 全集）都能被下方断言逮住。
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


# --- 确定性 / 成员 / 空库护栏（与 focus 无关，保持不变）--------------------------------------


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


def test_none_memory_selects_from_full_pool() -> None:
    # 未接记忆 + 默认 mixed + 无 asked：unasked = 全集 → 从全集选。
    items = _items()
    picks = {select_target(items, rng=new_rng(seed), memory=None).item_id for seed in range(50)}
    assert len(picks) > 1


def test_empty_memory_selects_from_full_pool() -> None:
    # 空记忆 + 默认 mixed + 无 asked：等价于全集随机。
    items = _items()
    empty = LearningMemory()
    picks = {select_target(items, rng=new_rng(seed), memory=empty).item_id for seed in range(50)}
    assert len(picks) > 1


# --- 覆盖优先（mixed，R1-S7 核心）：不锁死薄弱 ------------------------------------------------


def test_mixed_prefers_unasked_over_weak_no_lockdown() -> None:
    # 锁死回归（dogfood e342b709）：1 个薄弱且**已考过**的 item + N 个未考过 → mixed 覆盖优先选
    # 未考过的，**绝不回锁到已考过的薄弱 item**（修 "6 题锁死同一 item ae#003"）。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")  # items[0] 薄弱
    asked = {items[0].item_id}  # 本会话已考过 items[0]
    unasked_ids = {items[1].item_id, items[2].item_id, items[3].item_id}
    picks = {
        select_target(
            items, rng=new_rng(s), memory=memory, asked_item_ids=asked, focus="mixed"
        ).item_id
        for s in range(50)
    }
    assert items[0].item_id not in picks  # 不锁死薄弱（旧排他策略会恒返此项 → 被杀）
    assert picks <= unasked_ids  # 只在未考过里选
    assert len(picks) > 1  # 确实在未考集内随机（非恒返首个）


def test_mixed_falls_back_to_weak_when_all_asked() -> None:
    # 兜底 remediation：全部考过（unasked 空）后 → mixed 兜底到薄弱集（薄弱优先复考）。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")  # 薄弱
    memory.record_verdict(items[1].item_id, "错")  # 薄弱
    all_asked = {it.item_id for it in items}
    weak_ids = {items[0].item_id, items[1].item_id}
    picks = {
        select_target(
            items, rng=new_rng(s), memory=memory, asked_item_ids=all_asked, focus="mixed"
        ).item_id
        for s in range(50)
    }
    assert picks <= weak_ids  # 兜底到薄弱（若误退全集，非薄弱项会现身 → 被杀）
    assert items[2].item_id not in picks
    assert len(picks) > 1


def test_mixed_all_asked_no_weak_falls_back_to_full() -> None:
    # 全部考过且无薄弱 → mixed 最终回退全集（护栏：绝不返回空候选 / raise）。
    items = _items()
    all_asked = {it.item_id for it in items}
    picks = {
        select_target(items, rng=new_rng(s), asked_item_ids=all_asked, focus="mixed").item_id
        for s in range(50)
    }
    assert len(picks) > 1  # 回退全集随机


# --- focus="new"：只考未考过，绝不兜底薄弱 ----------------------------------------------------


def test_new_focus_selects_unasked_ignoring_weak() -> None:
    # focus=new（"考其他的 / 没考过的"）：选未考过，**不管**是否有薄弱。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")  # 薄弱且已考过
    asked = {items[0].item_id}
    picks = {
        select_target(
            items, rng=new_rng(s), memory=memory, asked_item_ids=asked, focus="new"
        ).item_id
        for s in range(50)
    }
    assert items[0].item_id not in picks
    assert picks <= {items[1].item_id, items[2].item_id, items[3].item_id}


def test_new_focus_all_asked_falls_back_to_full_not_weak() -> None:
    # focus=new：unasked 空 → 直接回退全集（**不像 mixed 兜底薄弱**）——区分 new 与 mixed 的兜底。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")  # 薄弱
    all_asked = {it.item_id for it in items}
    picks = {
        select_target(
            items, rng=new_rng(s), memory=memory, asked_item_ids=all_asked, focus="new"
        ).item_id
        for s in range(50)
    }
    non_weak = {items[1].item_id, items[2].item_id, items[3].item_id}
    # 回退全集：非薄弱项也会被选中（若误兜底薄弱 → picks ⊆ {items[0]} → 此断言被杀）。
    assert picks & non_weak


# --- focus="weak"：复习薄弱优先 ---------------------------------------------------------------


def test_weak_focus_selects_weak_ignoring_unasked() -> None:
    # focus=weak（"复习薄弱"）：有薄弱 → 只从薄弱集选，即使有大量未考过的新概念。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")  # 薄弱
    memory.record_verdict(items[1].item_id, "错")  # 薄弱
    weak_ids = {items[0].item_id, items[1].item_id}
    # 无任何 asked（全未考过）：mixed 会选未考过；weak 仍锁薄弱 → 二者分道扬镳。
    picks = {
        select_target(items, rng=new_rng(s), memory=memory, focus="weak").item_id for s in range(50)
    }
    assert picks <= weak_ids  # 若 focus 被无视（退回 mixed）→ 未考过的 items[2] 现身 → 被杀
    assert items[2].item_id not in picks
    assert len(picks) > 1


def test_weak_focus_no_weak_falls_back_to_unasked() -> None:
    # focus=weak 但无薄弱 → 退到未考过（再退全集）——不空转、不 raise。
    items = _items()
    empty = LearningMemory()
    asked = {items[0].item_id}
    picks = {
        select_target(
            items, rng=new_rng(s), memory=empty, asked_item_ids=asked, focus="weak"
        ).item_id
        for s in range(50)
    }
    assert items[0].item_id not in picks  # 无薄弱 → 退到 unasked（排除已考）
    assert picks <= {items[1].item_id, items[2].item_id, items[3].item_id}


def test_discharged_concept_leaves_weak_set() -> None:
    # focus=weak：销账后概念退出薄弱集，不再进候选（连对两次已掌握）。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict(items[0].item_id, "错")
    memory.record_verdict(items[0].item_id, "对")
    memory.record_verdict(items[0].item_id, "对")  # 销账
    memory.record_verdict(items[1].item_id, "错")  # 仅剩 items[1] 薄弱
    picks = {
        select_target(items, rng=new_rng(s), memory=memory, focus="weak").item_id for s in range(50)
    }
    assert picks == {items[1].item_id}


def test_weak_focus_ghost_weak_falls_back() -> None:
    # 兜底护栏：focus=weak 但薄弱概念的 item 已不在传入 items 里（幽灵 id）→ 薄弱候选空 →
    # 退到未考过 / 全集（不 raise、不空转）。
    items = _items()
    memory = LearningMemory()
    memory.record_verdict("不在库里的幽灵 item", "错")
    chosen = select_target(items, rng=new_rng(3), memory=memory, focus="weak")
    assert chosen in items

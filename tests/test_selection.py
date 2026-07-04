"""选题测试（缝 2，确定性核心单元缝）——纯函数、直接 TDD。

被测：种子化 rng → 确定性选择（同 seed 同结果、可回放）；空列表 → raise（调用方在空库时
应先走拒答分支，不该走到选题）。
"""

import pytest

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

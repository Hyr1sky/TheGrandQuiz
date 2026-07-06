"""Learning Memory 测试（缝 2，命门）——三态状态机 + 连对销账，纯确定性单元。

逐条钉死 apply_verdict（纯函数）与 LearningMemory（dict 台账）的状态转移不变量：
错/勉强→薄弱、薄弱+对→观察中、观察中+对→销账（移除）、连对两次才销账、复发打回薄弱、
不在记忆+对→不追踪；weak_item_ids / state_of / verdict_history 的记账正确、按 item_id 锚定。
"""

import pytest
from pydantic import ValidationError

from grandquiz.domain.learning.memory import ConceptRecord, LearningMemory, apply_verdict

_ITEM = "res#000"


def _weak(item_id: str = _ITEM, history: list[str] | None = None) -> ConceptRecord:
    return ConceptRecord(
        item_id=item_id,
        state="薄弱",
        consecutive_correct=0,
        verdict_history=list(history or ["错"]),  # type: ignore[arg-type]
    )


def _observing(item_id: str = _ITEM, history: list[str] | None = None) -> ConceptRecord:
    return ConceptRecord(
        item_id=item_id,
        state="观察中",
        consecutive_correct=1,
        verdict_history=list(history or ["错", "对"]),  # type: ignore[arg-type]
    )


# --- apply_verdict：纯函数逐条转移 -------------------------------------------------


def test_wrong_verdict_enters_weak_from_untracked() -> None:
    rec = apply_verdict(None, "错", item_id=_ITEM)
    assert rec is not None
    assert rec.item_id == _ITEM
    assert rec.state == "薄弱"
    assert rec.consecutive_correct == 0
    assert rec.verdict_history == ["错"]


def test_borderline_verdict_enters_weak_from_untracked() -> None:
    rec = apply_verdict(None, "勉强", item_id=_ITEM)
    assert rec is not None
    assert rec.state == "薄弱"
    assert rec.consecutive_correct == 0
    assert rec.verdict_history == ["勉强"]


def test_weak_plus_correct_goes_observing_with_count_one() -> None:
    rec = apply_verdict(_weak(), "对", item_id=_ITEM)
    assert rec is not None
    assert rec.state == "观察中"
    assert rec.consecutive_correct == 1
    assert rec.verdict_history == ["错", "对"]


def test_observing_plus_correct_discharges_to_none() -> None:
    # 观察中 + 对 → 销账（从记忆移除），apply_verdict 以 None 表达。
    assert apply_verdict(_observing(), "对", item_id=_ITEM) is None


def test_untracked_plus_correct_stays_untracked() -> None:
    # 概念不在记忆 + 对 → 不追踪（正确答非薄弱概念不入记忆）。
    assert apply_verdict(None, "对", item_id=_ITEM) is None


def test_observing_plus_wrong_falls_back_to_weak_resetting_count() -> None:
    rec = apply_verdict(_observing(), "错", item_id=_ITEM)
    assert rec is not None
    assert rec.state == "薄弱"
    assert rec.consecutive_correct == 0
    assert rec.verdict_history == ["错", "对", "错"]


def test_weak_plus_borderline_stays_weak() -> None:
    rec = apply_verdict(_weak(), "勉强", item_id=_ITEM)
    assert rec is not None
    assert rec.state == "薄弱"
    assert rec.consecutive_correct == 0
    assert rec.verdict_history == ["错", "勉强"]


def test_observing_plus_borderline_falls_back_to_weak() -> None:
    # 最危险的误销账场景：观察中 + 勉强 必须回到薄弱、连对归 0（勉强不是部分正确、绝不加计数）。
    rec = apply_verdict(_observing(), "勉强", item_id=_ITEM)
    assert rec is not None
    assert rec.state == "薄弱"
    assert rec.consecutive_correct == 0
    assert rec.verdict_history == ["错", "对", "勉强"]


def test_concept_record_rejects_state_count_mismatch() -> None:
    # 不变量守卫：薄弱 ↔ 连对 0、观察中 ↔ 连对 1；脏数据（如 M7 反序列化）在构造点即失败，
    # 而非被 apply_verdict 静默错误销账。
    with pytest.raises(ValidationError):
        ConceptRecord(item_id=_ITEM, state="薄弱", consecutive_correct=1, verdict_history=["错"])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        ConceptRecord(
            item_id=_ITEM,
            state="观察中",
            consecutive_correct=0,
            verdict_history=["错", "对"],  # type: ignore[list-item]
        )


# --- LearningMemory：dict 台账 + 转移信息 ------------------------------------------


def test_record_wrong_makes_concept_weak() -> None:
    # eval case 4 的单元底座：答错 → 概念按 item_id 入记忆、状态薄弱。
    mem = LearningMemory()
    t = mem.record_verdict(_ITEM, "错")
    assert mem.state_of(_ITEM) == "薄弱"
    assert _ITEM in mem.weak_item_ids()
    assert t.item_id == _ITEM
    assert t.from_state is None
    assert t.to_state == "薄弱"
    assert t.consecutive_correct == 0


def test_two_correct_in_a_row_discharges_but_one_correct_keeps_tracking() -> None:
    # 连对两次才销账：错→薄弱；对→观察中（仍在记忆）；再对→销账（移除）。
    mem = LearningMemory()
    mem.record_verdict(_ITEM, "错")
    assert mem.state_of(_ITEM) == "薄弱"

    t1 = mem.record_verdict(_ITEM, "对")
    assert mem.state_of(_ITEM) == "观察中"  # 答对一次不销账，仍在记忆
    assert _ITEM in mem.weak_item_ids()
    assert (t1.from_state, t1.to_state, t1.consecutive_correct) == ("薄弱", "观察中", 1)

    t2 = mem.record_verdict(_ITEM, "对")
    assert mem.state_of(_ITEM) is None  # 连对两次 → 销账 → 移除
    assert _ITEM not in mem.weak_item_ids()
    assert (t2.from_state, t2.to_state, t2.consecutive_correct) == ("观察中", "销账", 2)


def test_observing_reoffend_returns_to_weak() -> None:
    mem = LearningMemory()
    mem.record_verdict(_ITEM, "错")
    mem.record_verdict(_ITEM, "对")  # 观察中
    t = mem.record_verdict(_ITEM, "错")  # 复发
    assert mem.state_of(_ITEM) == "薄弱"
    assert (t.from_state, t.to_state, t.consecutive_correct) == ("观察中", "薄弱", 0)


def test_discharged_then_wrong_re_enters_weak_fresh() -> None:
    mem = LearningMemory()
    mem.record_verdict(_ITEM, "错")
    mem.record_verdict(_ITEM, "对")
    mem.record_verdict(_ITEM, "对")  # 销账
    assert mem.state_of(_ITEM) is None

    t = mem.record_verdict(_ITEM, "错")  # 销账后再错 → 重新薄弱
    assert mem.state_of(_ITEM) == "薄弱"
    assert t.from_state is None  # 已移除，视作未追踪
    assert t.to_state == "薄弱"
    rec = mem.record_of(_ITEM)
    assert rec is not None
    assert rec.verdict_history == ["错"]  # 全新记录，不含销账前历史


def test_correct_on_untracked_stays_untracked() -> None:
    # 不在记忆 + 对 → 仍不在记忆（transition 报未追踪，from/to 皆 None）。
    mem = LearningMemory()
    t = mem.record_verdict(_ITEM, "对")
    assert mem.state_of(_ITEM) is None
    assert _ITEM not in mem.weak_item_ids()
    assert t.from_state is None
    assert t.to_state is None
    assert t.consecutive_correct == 0


def test_weak_item_ids_includes_weak_and_observing_excludes_discharged() -> None:
    mem = LearningMemory()
    mem.record_verdict("a", "错")  # a: 薄弱
    mem.record_verdict("b", "错")  # b: 薄弱 →
    mem.record_verdict("b", "对")  # b: 观察中
    mem.record_verdict("c", "错")  # c: 薄弱 →
    mem.record_verdict("c", "对")  # c: 观察中 →
    mem.record_verdict("c", "对")  # c: 销账
    assert mem.weak_item_ids() == {"a", "b"}
    assert mem.state_of("a") == "薄弱"
    assert mem.state_of("b") == "观察中"
    assert mem.state_of("c") is None


def test_verdict_history_accumulates_while_tracked() -> None:
    mem = LearningMemory()
    mem.record_verdict(_ITEM, "错")
    mem.record_verdict(_ITEM, "对")
    rec = mem.record_of(_ITEM)
    assert rec is not None
    assert rec.verdict_history == ["错", "对"]


def test_records_are_anchored_by_item_id() -> None:
    # 按 item_id 锚定：不同 item 各记各账，互不串。
    mem = LearningMemory()
    mem.record_verdict("x", "错")
    mem.record_verdict("y", "勉强")
    assert mem.state_of("x") == "薄弱"
    assert mem.state_of("y") == "薄弱"
    mem.record_verdict("x", "对")
    assert mem.state_of("x") == "观察中"
    assert mem.state_of("y") == "薄弱"  # y 不受 x 影响

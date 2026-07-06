"""SqliteLearningMemory 测试（M7）——SQLite 支持的三态状态机，与 dict 版 ``LearningMemory`` 等价。

复用纯函数 ``apply_verdict``（状态机不重写，其逐条转移已在 test_memory.py 钉死），此处验 SQLite：
错→薄弱、薄弱+对→观察中、观察中+对→销账（DELETE 行）、连对两次才销账、复发打回薄弱、
不在记忆+对→不追踪；weak_item_ids / state_of、verdict_history 累积、按 item_id 锚定；Transition
与 dict 版逐字段一致；脏行（薄弱却 cc=1）经 model_validate 反序列化时被不变量 validator 拒。
用 ``:memory:``（单连接内足够；跨会话验收见 test_sqlite_persistence.py）。
"""

from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from grandquiz.domain.learning.grading import VerdictLabel
from grandquiz.domain.learning.memory import LearningMemory, SqliteLearningMemory
from grandquiz.kernel.db import connect

_ITEM = "res#000"


def _sqlite() -> SqliteLearningMemory:
    return SqliteLearningMemory(":memory:")


def test_record_wrong_makes_concept_weak() -> None:
    mem = _sqlite()
    t = mem.record_verdict(_ITEM, "错")
    assert mem.state_of(_ITEM) == "薄弱"
    assert _ITEM in mem.weak_item_ids()
    assert t.item_id == _ITEM
    assert t.from_state is None
    assert t.to_state == "薄弱"
    assert t.consecutive_correct == 0


def test_two_correct_in_a_row_discharges_but_one_correct_keeps_tracking() -> None:
    mem = _sqlite()
    mem.record_verdict(_ITEM, "错")
    assert mem.state_of(_ITEM) == "薄弱"

    t1 = mem.record_verdict(_ITEM, "对")
    assert mem.state_of(_ITEM) == "观察中"  # 答对一次不销账，仍在记忆
    assert _ITEM in mem.weak_item_ids()
    assert (t1.from_state, t1.to_state, t1.consecutive_correct) == ("薄弱", "观察中", 1)

    t2 = mem.record_verdict(_ITEM, "对")
    assert mem.state_of(_ITEM) is None  # 连对两次 → 销账 → DELETE 行
    assert _ITEM not in mem.weak_item_ids()
    assert (t2.from_state, t2.to_state, t2.consecutive_correct) == ("观察中", "销账", 2)


def test_observing_reoffend_returns_to_weak() -> None:
    mem = _sqlite()
    mem.record_verdict(_ITEM, "错")
    mem.record_verdict(_ITEM, "对")  # 观察中
    t = mem.record_verdict(_ITEM, "错")  # 复发
    assert mem.state_of(_ITEM) == "薄弱"
    assert (t.from_state, t.to_state, t.consecutive_correct) == ("观察中", "薄弱", 0)


def test_discharged_then_wrong_re_enters_weak_fresh() -> None:
    mem = _sqlite()
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
    mem = _sqlite()
    t = mem.record_verdict(_ITEM, "对")
    assert mem.state_of(_ITEM) is None
    assert _ITEM not in mem.weak_item_ids()
    assert t.from_state is None
    assert t.to_state is None
    assert t.consecutive_correct == 0


def test_weak_item_ids_includes_weak_and_observing_excludes_discharged() -> None:
    mem = _sqlite()
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
    mem = _sqlite()
    mem.record_verdict(_ITEM, "错")
    mem.record_verdict(_ITEM, "对")
    rec = mem.record_of(_ITEM)
    assert rec is not None
    assert rec.verdict_history == ["错", "对"]


def test_records_are_anchored_by_item_id() -> None:
    mem = _sqlite()
    mem.record_verdict("x", "错")
    mem.record_verdict("y", "勉强")
    assert mem.state_of("x") == "薄弱"
    assert mem.state_of("y") == "薄弱"
    mem.record_verdict("x", "对")
    assert mem.state_of("x") == "观察中"
    assert mem.state_of("y") == "薄弱"  # y 不受 x 影响


@pytest.mark.parametrize(
    "sequence",
    [
        ["错"],
        ["错", "对"],
        ["勉强", "对", "对", "错"],
        ["错", "对", "错", "对", "对"],
        ["对"],  # 未追踪 + 对
        ["错", "对", "对", "错", "对"],  # 销账后再入薄弱
    ],
)
def test_transitions_match_dict_memory(sequence: list[str]) -> None:
    # 同一判决序列喂 dict 版与 SQLite 版：每一步的 Transition 逐字段一致，终态投影也一致。
    dict_mem = LearningMemory()
    sqlite_mem = _sqlite()
    for label in sequence:
        verdict = cast(VerdictLabel, label)
        assert sqlite_mem.record_verdict(_ITEM, verdict) == dict_mem.record_verdict(_ITEM, verdict)
    assert sqlite_mem.state_of(_ITEM) == dict_mem.state_of(_ITEM)
    assert sqlite_mem.weak_item_ids() == dict_mem.weak_item_ids()
    assert sqlite_mem.record_of(_ITEM) == dict_mem.record_of(_ITEM)


def test_dirty_row_violating_invariant_is_rejected_on_read(tmp_path: Path) -> None:
    # 反序列化脏行（薄弱却 cc=1）时，ConceptRecord 的不变量 model_validator 在构造点即失败，
    # 而非被 apply_verdict 静默错误销账。用真实文件 db，另开一条裸连接注入非法行再读。
    db = tmp_path / "learning.db"
    mem = SqliteLearningMemory(db)  # 建表 + 迁移
    raw = connect(db)
    raw.execute(
        "INSERT INTO learning_memory (item_id, state, consecutive_correct, verdict_history) "
        "VALUES (?, ?, ?, ?)",
        (_ITEM, "薄弱", 1, '["错"]'),
    )
    raw.commit()
    raw.close()
    with pytest.raises(ValidationError):
        mem.state_of(_ITEM)
    with pytest.raises(ValidationError):
        mem.record_of(_ITEM)
    mem.close()

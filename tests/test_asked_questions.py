"""已问过去重台账测试（skeleton-ledger.md #8）——dict ↔ SQLite parity + 跨会话留存。

三簇断言：
- **基本读写**：未问过 → 空列表；record 后能读回，按插入序累积（不覆盖）。
- **dict ↔ SQLite parity**：同一 record 序列喂两种实现，``asked_before`` 逐 item 一致。
- **跨会话留存**：SQLite 版 record → 关闭连接、丢弃对象 → 同一 db_path 重开 → 历史仍在
  （这正是 #8 要修的那个真实 bug 的验收信号：关掉 CLI 重开，已问过的题不会被遗忘）。
"""

from pathlib import Path

from grandquiz.domain.learning.asked_questions import (
    AskedQuestionsLedger,
    DictAskedQuestionsLedger,
    SqliteAskedQuestionsLedger,
)

# --- 基本读写 -----------------------------------------------------------------------


def test_asked_before_empty_when_never_recorded() -> None:
    ledger = DictAskedQuestionsLedger()
    assert ledger.asked_before("item-1") == []


def test_record_then_asked_before_returns_it() -> None:
    ledger = DictAskedQuestionsLedger()
    ledger.record_asked("item-1", "什么是闭包？")
    assert ledger.asked_before("item-1") == ["什么是闭包？"]


def test_multiple_records_accumulate_in_insertion_order() -> None:
    ledger = DictAskedQuestionsLedger()
    ledger.record_asked("item-1", "第一题")
    ledger.record_asked("item-1", "第二题")
    assert ledger.asked_before("item-1") == ["第一题", "第二题"]


def test_different_items_do_not_cross_contaminate() -> None:
    ledger = DictAskedQuestionsLedger()
    ledger.record_asked("item-1", "闭包题")
    ledger.record_asked("item-2", "事件循环题")
    assert ledger.asked_before("item-1") == ["闭包题"]
    assert ledger.asked_before("item-2") == ["事件循环题"]


# --- dict ↔ SQLite parity ------------------------------------------------------------


def test_dict_sqlite_parity() -> None:
    ops = [("item-1", "问题A"), ("item-2", "问题B"), ("item-1", "问题C")]
    dict_ledger: AskedQuestionsLedger = DictAskedQuestionsLedger()
    sqlite_ledger: AskedQuestionsLedger = SqliteAskedQuestionsLedger(":memory:")
    for item_id, question in ops:
        dict_ledger.record_asked(item_id, question)
        sqlite_ledger.record_asked(item_id, question)
    for item_id in ("item-1", "item-2", "item-missing"):
        assert dict_ledger.asked_before(item_id) == sqlite_ledger.asked_before(item_id)


# --- 跨会话留存（#8 的核心验收信号）----------------------------------------------------


def test_asked_questions_survive_close_and_reopen(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"

    ledger1 = SqliteAskedQuestionsLedger(db)
    ledger1.record_asked("item-1", "什么是闭包？")
    ledger1.close()
    del ledger1

    ledger2 = SqliteAskedQuestionsLedger(db)
    assert ledger2.asked_before("item-1") == ["什么是闭包？"]  # 跨会话仍在，不是空白重来
    ledger2.close()


def test_asked_questions_keep_accumulating_across_sessions(tmp_path: Path) -> None:
    # 模拟"今天问了一题、关掉 CLI、明天再开一个新会话又问了一题"——两条都该留着，不是后者覆盖前者。
    db = tmp_path / "learning.db"

    ledger1 = SqliteAskedQuestionsLedger(db)
    ledger1.record_asked("item-1", "第一天问的题")
    ledger1.close()

    ledger2 = SqliteAskedQuestionsLedger(db)
    ledger2.record_asked("item-1", "第二天问的题")
    result = ledger2.asked_before("item-1")
    ledger2.close()

    assert result == ["第一天问的题", "第二天问的题"]

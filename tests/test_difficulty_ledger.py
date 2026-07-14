"""难度台账测试（SE-S1）——默认档兜底 + dict ↔ SQLite parity + 跨会话留存。

照 ``test_asked_questions.py`` 的路数，四簇断言：
- **默认档兜底**：从没记录过的 item_id 读到默认档 3（不抛、不 None）——决策 2：难度生命周期
  "只要考过就一直在"，但没考过的概念也得有个确定的起点档。
- **基本读写 + 幂等覆盖**：``set_tier`` 后能读回；重复 ``set_tier`` 后写覆盖（幂等，取最新值）。
- **dict ↔ SQLite parity**：同一串 set/读操作喂两种实现，``tier_of`` 逐 item 一致。
- **跨会话留存**：SQLite 版写档 → 关连接、丢弃对象 → 同一 db_path 重开 → 读到同一档
  （User Story 11：今天练到 4 档，明天重开 CLI 它还是 4 档，不会重置）。
"""

from pathlib import Path

from grandquiz.domain.learning.difficulty import (
    DEFAULT_TIER,
    DictDifficultyLedger,
    DifficultyLedger,
    DifficultyTier,
    SqliteDifficultyLedger,
)

# --- 默认档兜底 ---------------------------------------------------------------------


def test_default_tier_constant_is_standard_middle() -> None:
    # 5 档中间的标准档。硬编码 3 以锁死默认档语义（mutation：改成别的值应被这条杀掉）。
    assert DEFAULT_TIER == 3


def test_tier_of_returns_default_when_never_recorded_dict() -> None:
    ledger = DictDifficultyLedger()
    assert ledger.tier_of("item-1") == DEFAULT_TIER


def test_tier_of_returns_default_when_never_recorded_sqlite() -> None:
    ledger = SqliteDifficultyLedger(":memory:")
    assert ledger.tier_of("item-1") == DEFAULT_TIER
    ledger.close()


# --- 基本读写 + 幂等覆盖 --------------------------------------------------------------


def test_set_then_tier_of_returns_it_dict() -> None:
    ledger = DictDifficultyLedger()
    ledger.set_tier("item-1", 4)
    assert ledger.tier_of("item-1") == 4


def test_set_tier_is_idempotent_overwrite_dict() -> None:
    ledger = DictDifficultyLedger()
    ledger.set_tier("item-1", 4)
    ledger.set_tier("item-1", 2)  # 后写覆盖，不累积
    assert ledger.tier_of("item-1") == 2


def test_set_tier_is_idempotent_overwrite_sqlite() -> None:
    ledger = SqliteDifficultyLedger(":memory:")
    ledger.set_tier("item-1", 4)
    ledger.set_tier("item-1", 2)
    assert ledger.tier_of("item-1") == 2
    ledger.close()


def test_different_items_do_not_cross_contaminate_dict() -> None:
    ledger = DictDifficultyLedger()
    ledger.set_tier("item-1", 5)
    ledger.set_tier("item-2", 1)
    assert ledger.tier_of("item-1") == 5
    assert ledger.tier_of("item-2") == 1


# --- dict ↔ SQLite parity ------------------------------------------------------------


def test_dict_sqlite_parity() -> None:
    # 含覆盖（item-1 先 4 后 5）与从未记录（item-missing → 默认档）。
    ops: list[tuple[str, DifficultyTier]] = [("item-1", 4), ("item-2", 1), ("item-1", 5)]
    dict_ledger: DifficultyLedger = DictDifficultyLedger()
    sqlite_ledger: DifficultyLedger = SqliteDifficultyLedger(":memory:")
    for item_id, tier in ops:
        dict_ledger.set_tier(item_id, tier)
        sqlite_ledger.set_tier(item_id, tier)
    for item_id in ("item-1", "item-2", "item-missing"):
        assert dict_ledger.tier_of(item_id) == sqlite_ledger.tier_of(item_id)


# --- 跨会话留存（User Story 11 的核心验收信号）---------------------------------------


def test_tier_survives_close_and_reopen(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"

    ledger1 = SqliteDifficultyLedger(db)
    ledger1.set_tier("item-1", 4)
    ledger1.close()
    del ledger1

    ledger2 = SqliteDifficultyLedger(db)
    assert ledger2.tier_of("item-1") == 4  # 跨会话仍是 4 档，不是重置回默认
    ledger2.close()


def test_overwrite_survives_across_sessions(tmp_path: Path) -> None:
    # 第一天升到 4，第二天新会话降到 2——留存的是最新值（覆盖语义跨会话成立）。
    db = tmp_path / "learning.db"

    ledger1 = SqliteDifficultyLedger(db)
    ledger1.set_tier("item-1", 4)
    ledger1.close()

    ledger2 = SqliteDifficultyLedger(db)
    ledger2.set_tier("item-1", 2)
    ledger2.close()

    ledger3 = SqliteDifficultyLedger(db)
    result = ledger3.tier_of("item-1")
    ledger3.close()

    assert result == 2

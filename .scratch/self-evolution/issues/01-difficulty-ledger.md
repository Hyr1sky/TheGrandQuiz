# SE-S1 — 难度台账（DifficultyLedger：新表，独立于 Learning Memory）

Status: ready-for-agent
Type: AFK

## Parent
[PRD: 自进化第一阶段](../PRD.md)

## What to build

一张**难度台账**：锚定 `item_id`、维护每个 KnowledgeItem 的离散 5 档难度，**独立于 Learning Memory
薄弱台账**、生命周期"只要考过就一直在"（不随销账删除）。这是自进化的最底层地基——先建、可独立
单测，后续 S3 写它、S5/S6 读它。

## 锁定设计（不留给实现猜）

- **5 档难度枚举**：定一个 `DifficultyTier`（拟 `Literal[1, 2, 3, 4, 5]`，`3` 为默认/标准档；具体
  用整数还是命名字符串由实现选，但**必须离散**——PRD 决策 1 硬约束，不做连续分）。定义放
  `domain/learning/` 下合适模块（新 `difficulty.py` 或与台账同文件，实现定）。
- **`DifficultyLedger` 协议 + 两实现**，照 `asked_questions.py` 的 Protocol + Dict + Sqlite 三段式：
  - 方法（拟）：`tier_of(item_id) -> DifficultyTier`（未记录过 → 返回默认档 `3`，不抛）、
    `set_tier(item_id, tier) -> None`（幂等写）。方法名/签名实现定，但语义须是"读默认兜底 + 写覆盖"。
  - `DictDifficultyLedger`：进程内 dict，测试/快速用。
  - `SqliteDifficultyLedger`：SQLite 持久，跨会话留存。含 `close()`（同族台账惯例）。
- **migration `0006_difficulty.sql`**：建 `difficulty` 表，列拟 `seq`(自增主键，给插入序)、
  `item_id`(唯一)、`tier`(整数)。**无时间戳列**（determinism 纪律，排序靠 `seq`）。承接
  `0005_asked_questions.sql`，复用 kernel 参数化 `migrate`。
- **确定性**：纯 I/O，无 clock/random/time。默认档兜底是纯逻辑。

## Acceptance criteria

- [ ] `DifficultyTier` 离散 5 档枚举定义 + 默认档常量
- [ ] `DifficultyLedger` 协议 + `DictDifficultyLedger` + `SqliteDifficultyLedger` 均实现读默认兜底 + 写覆盖
- [ ] migration `0006_difficulty.sql` 建表（无时间戳列，`seq` 自增主键，`item_id` 唯一）
- [ ] **Dict↔Sqlite parity 测试**：同一串 set/读操作两实现结果逐条相等
- [ ] **跨会话持久验收**：Sqlite 写档 → 关连接重开 → 读到同一档（照 `test_asked_questions.py`）
- [ ] **默认档兜底**：从没记录过的 item_id 读到默认档（不抛、不 None）
- [ ] TDD：读默认/写覆盖/幂等/parity/持久，各 mutation 可杀
- [ ] 五门全绿（含 lint-imports）

## Files (owner, 可能漂)
`domain/learning/difficulty.py`(新，枚举 + 台账三段式)、
`domain/learning/migrations/0006_difficulty.sql`(新)、`tests/test_difficulty_ledger.py`(新)。

## Blocked by
None（基线 main `770c971`；纯新增、不碰既有表）。

# AD-S2 — 收拢 Learning persistence owner 与 transaction seam

Status: done
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

建立一个拥有 LearningDatabase 与五类 SQLite Adapter 生命周期的 deep Module，让生产调用者只关闭一次，并让
判决原子提交使用显式 transaction owner。各领域账本的 Interface 和 Dict/SQLite Adapter 继续独立。

## Acceptance criteria

- [x] LearningDatabase 的生产 owner 唯一且可一次关闭
- [x] CLI quiz/react 不再解包位置敏感五元组或逐个关闭五个 Adapter
- [x] 一次判决不再通过私有属性反射推断 transaction owner
- [x] 多个不共享 LearningDatabase 的 SQLite Adapter fail closed
- [x] Dict 成功/回滚/重试语义与 SQLite parity
- [x] 新增账本不要求扩散生命周期接线到多个调用者
- [x] 现有 schema、数据与考核行为保持不变

## Blocked by

- [AD-S1](01-deepen-assessment-loop.md)

## Evidence

- `LearningPersistence` 具名拥有 store、memory、preferences、asked_questions、difficulty 与唯一
  `LearningDatabase`；生产入口只调用一次 `persistence.close()`。
- `TransactionParticipant.transaction_owner` 替代 `_learning_database` 私有反射；不同 owner 的
  SQLite Adapter 在 `LearningStateWriter` 构造时 fail closed。
- 红灯 1：新生命周期测试最初因 `build_learning_persistence` 不存在而 collection failed。
- 红灯 2：显式 owner 测试最初因 SQLite Adapter 没有 `transaction_owner` 而失败。
- 绿灯：受影响 persistence、CLI、SQLite 测试 `92 passed`。
- 静态验证：受影响文件 Ruff、format check、Pyright 全绿；没有 schema 或 migration 改动。

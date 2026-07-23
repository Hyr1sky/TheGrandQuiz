# AD-S2 — 收拢 Learning persistence owner 与 transaction seam

Status: ready-for-agent
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

建立一个拥有 LearningDatabase 与五类 SQLite Adapter 生命周期的 deep Module，让生产调用者只关闭一次，并让
判决原子提交使用显式 transaction owner。各领域账本的 Interface 和 Dict/SQLite Adapter 继续独立。

## Acceptance criteria

- [ ] LearningDatabase 的生产 owner 唯一且可一次关闭
- [ ] CLI quiz/react 不再解包位置敏感五元组或逐个关闭五个 Adapter
- [ ] 一次判决不再通过私有属性反射推断 transaction owner
- [ ] 多个不共享 LearningDatabase 的 SQLite Adapter fail closed
- [ ] Dict 成功/回滚/重试语义与 SQLite parity
- [ ] 新增账本不要求扩散生命周期接线到多个调用者
- [ ] 现有 schema、数据与考核行为保持不变

## Blocked by

- [AD-S1](01-deepen-assessment-loop.md)


# SH-S5 — 判决与学习状态原子提交

Status: ready-for-agent
Type: AFK

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

复用知识快照建立的 transaction seam，把一次判决产生的 Learning Memory 转移、难度变化及相关持久状态
作为一个提交结果。任一步失败时不得留下已销账但未升档等不可恢复半状态。

## Acceptance criteria

- [ ] 一次判决的相关领域写入共享同一事务语义
- [ ] 任一写入注入失败后所有状态回滚，重试仍能读取原 verdict history
- [ ] 领域事件只描述已提交状态；回滚路径不发成功状态转移
- [ ] Dict / SQLite 对成功、回滚和重试结果 parity
- [ ] 复用 SH-S1 transaction seam，不新增第二套事务 helper
- [ ] 多连接或调用顺序不再是调用方必须知道的 interface 细节
- [ ] 五门全绿

## Blocked by

- [SH-S1](02-stable-resource-snapshot.md)

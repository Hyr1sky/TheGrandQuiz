# SH-S8 — 一路答对的难度演化

Status: done（难度激活真实 cassette + 离线回放通过，五门全绿）
Type: AFK

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

补齐自进化 User Story 12：从未进入薄弱台账、持续答对的 KnowledgeItem 也能积累确定性掌握证据并逐步
升档，同时保持“难度生命周期长于薄弱状态”的设计。

## Acceptance criteria

- [x] 新概念直接答对会记录可持续的难度演化信号
- [x] 连续答对满足规则后升档，单次蒙对不立即冲到高档
- [x] 答错 / 勉强会重置或抵消顺畅掌握证据，规则可解释
- [x] 与薄弱销账升降路径共享一套纯函数，不产生冲突双写
- [x] 难度变化事件只在已提交真跨档时发出
- [x] 跨会话持久与 Dict / SQLite parity 成立
- [x] 难度激活真实 cassette 与 eval 用例通过
- [x] 五门全绿

## Blocked by

- [SH-S5](06-atomic-learning-state.md)

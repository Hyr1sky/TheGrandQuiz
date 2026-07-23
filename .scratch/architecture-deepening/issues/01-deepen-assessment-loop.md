# AD-S1 — Deepen 多题考核循环 Module

Status: done
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

让 CLI quiz 与 ReAct `start_quiz` 通过同一个考核循环 Module 使用稳定依赖、会话内已问题目和确定性种子推进。
保留 `assess_once` 的单题 workflow、入口特有呈现和 RecoveryPolicy 语义，但调用者不再重复组装整套单题依赖。

## Acceptance criteria

- [x] CLI 与 ReAct tool 通过同一公开 Interface 发起单轮考核
- [x] 会话内 recently asked 与种子推进由考核循环 Module 持有
- [x] scope、focus、题型、Preference Memory、AskedQuestions 与 Difficulty 行为保持不变
- [x] 取消作答不产生判决或记忆写入
- [x] CLI 的逐轮 RecoveryPolicy 与 ReAct 的工具错误传播语义保持不变
- [x] 现有 Provider messages、tool schema、事件顺序与 cassette 保持不变
- [x] 新行为测试经红—绿循环通过，受影响测试全绿

## Blocked by

None - can start immediately

## Evidence

- 新增 `AssessmentSession`，稳定持有单题 workflow 依赖、会话覆盖台账和确定性种子序列。
- CLI quiz 与 ReAct `start_quiz` 均委托 `AssessmentSession.assess`；各自的展示、取消和 RecoveryPolicy
  仍留在 Adapter。
- 红灯：目标测试最初因 `assessment.session` 不存在而 collection failed。
- 绿灯：`test_assessment_session_owns_multi_round_coverage_state` 通过。
- 回归：assessment、ReAct quiz tool、CLI quiz 与 CLI ReAct 共 `89 passed`。
- 静态验证：受影响文件 Ruff、format check、Pyright 全绿。

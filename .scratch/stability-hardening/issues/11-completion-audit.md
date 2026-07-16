# SH-S10 — 稳定性加固完成审计

Status: ready-for-agent
Type: HITL

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

按 PRD 的每条显式要求逐项收集当前代码、测试、trace、cassette、数据库和真机行为证据；完成备份后清库
重建与真实 dogfood，并把所有权威文档收口到相同事实。

## Acceptance criteria

- [ ] S1-S9 每条 acceptance criterion 有直接证据，不以“未发现问题”代替证明
- [ ] learning DB 备份可打开，新库从真实材料重建并完成考核闭环
- [ ] 全部受影响 cassette 已重录或明确废弃，无旧工具契约假绿
- [ ] Ruff、format、Pyright、import-linter、全量 pytest 全绿
- [ ] 全部 eval 与关键真机 trace 通过，成本 / token /错误信息完整
- [ ] README、CONTEXT、architecture、ADR、PRD、issue、skeleton ledger 状态一致
- [ ] 残余风险和明确 Out of Scope 形成最终报告

## Blocked by

- [SH-S1](02-stable-resource-snapshot.md)
- [SH-S2](03-fail-closed-quiz-scope.md)
- [SH-S3](04-streaming-web-fetch.md)
- [SH-S4](05-replay-execution-fingerprint.md)
- [SH-S5](06-atomic-learning-state.md)
- [SH-S6](07-durable-trace-processor.md)
- [SH-S7](08-provider-request-budget.md)
- [SH-S8](09-direct-correct-difficulty.md)
- [SH-S9](10-real-approval-gate.md)

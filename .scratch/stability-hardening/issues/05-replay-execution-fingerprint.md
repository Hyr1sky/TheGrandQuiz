# SH-S4 — Replay 执行指纹

Status: implementation-done / HITL cassette pending
Type: HITL

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

让 Replay key 覆盖真正影响模型决策的执行契约，而不只覆盖 messages、role 与 model。工具集合、description、
schema 或公开生成参数变化后，旧 cassette 必须大声失效。

## Acceptance criteria

- [x] 指纹确定性覆盖 messages、role、model 与规范化 tool specs
- [x] 工具顺序不影响语义时规范化结果稳定，schema / description / 集合变化必改变 key
- [x] 指纹不包含 API Key、Authorization 或 secret value
- [x] Recording 与 Replay 使用同一指纹实现，无重复序列化规则
- [x] 旧纯文本路径的迁移策略和 cassette 清单明确
- [x] fake provider 回放测试覆盖工具契约变化导致 ReplayMiss
- [ ] 真实模型 cassette 重录并跑通 ReAct eval
- [ ] 五门全绿

## Blocked by

- [SH-S0](01-authoritative-doc-baseline.md)

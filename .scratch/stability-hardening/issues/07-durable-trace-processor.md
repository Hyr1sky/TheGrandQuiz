# SH-S6 — Durable Trace 失败语义

Status: ready-for-agent
Type: AFK

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

区分必须持久成功的 event processor 与允许失败隔离的展示 observer。TraceStore 写失败时 turn 必须大声
失败或进入明确的持久告警状态，不能继续向用户报告一条不完整 trace。

## Acceptance criteria

- [ ] durable processor 与 best-effort observer 有不同且明确的失败契约
- [ ] CLI printer 失败仍被隔离，不影响 TraceStore 和业务
- [ ] TraceStore 注入写失败时 turn 不报告成功，用户不收到误导 trace 位置
- [ ] 失败本身可被上层观察且不递归触发无限事件发布
- [ ] 正常事件顺序、span 配对与 processor 扇出不回归
- [ ] 五门全绿

## Blocked by

- [SH-S0](01-authoritative-doc-baseline.md)

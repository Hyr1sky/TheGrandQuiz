# SH-S7 — 完整 Provider 出站预算

Status: ready-for-agent
Type: AFK

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

把预算 interface 移到真正的 Provider 出站请求处，使初始上下文、tool specs、循环追加的工具调用与结果、
持久已问题目历史全部受同一硬上限约束。

## Acceptance criteria

- [ ] 每次 Provider 调用前按完整 messages + tool specs 检查预算
- [ ] 工具循环追加大结果后可压缩或大声失败，不把超限请求交给远端
- [ ] 已问题目历史有确定性保留上限，不随跨会话使用无限增长 prompt
- [ ] 分区软预算不会按 hash 排序静默丢弃更重要的薄弱概念
- [ ] 预算事件 / 错误含可解释的各部分 token 估算
- [ ] Replay 对同一预算输入保持确定性
- [ ] 五门全绿

## Blocked by

- [SH-S4](05-replay-execution-fingerprint.md)

# R1-S3 — ContextBuilder（M5 共建）+ 多步轨迹 + 记忆注入

Status: blocked（待 S1/S2 落地；ContextBuilder 分区/预算策略先给默认版再定）
Type: AFK

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build（待细化）

与 M5 共建 `ContextBuilder`：分区（system / persona / memory / knowledge / history）拼装 + 每区 token 预算 +
**丢弃工具调用中间过程（只留最终结果进 ReAct 上下文）** + Learning/Preference 记忆注入。打通多步轨迹。

## Acceptance criteria（草稿）
- [ ] `ContextBuilder`：分区装配 + 每区 token 预算（纯函数、可 TDD）；工具中间过程被裁、只留最终结果
- [ ] Learning + Preference 记忆注入 memory 分区（薄弱概念、语言/难度偏好）
- [ ] 竖切：多步"入库这篇然后考我薄弱点"链 ingest→start_quiz、上下文有界不膨胀
- [ ] 五门全绿
- [ ] 设计点（发 issue 时定）：分区集与每区预算策略——先给基于现有 memory/knowledge 的默认版

## Blocked by
[S1 — tool 循环](01-tool-calling-loop-replay.md)、[S2 — 考官子代理](02-examiner-as-subagent.md)

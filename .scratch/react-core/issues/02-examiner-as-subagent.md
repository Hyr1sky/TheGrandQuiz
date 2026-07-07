# R1-S2 — 考官作隔离子代理工具

Status: blocked（待 S1 定的 Completion/tool 形状落地后细化 spec）
Type: AFK

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build（待细化）

抽通用 `kernel/subagent.py`（隔离上下文 + 结构化输出契约 + 有界重试，泛化 Reader 的内联模式，**销台账 #4**）；
把 `ingest` / `start_quiz` 包成 tool，内部把**确定性考官作为隔离子代理**跑——考官 span 子树嵌在 `tool_call` span
之下、但**与 ReAct 上下文隔离**（ReAct 只收结构化结果，看不到考官内部 span/token churn）。S1 留的 `before_tool`
挂点接上注入防护 / 审批；工具报错走 M6。

## Acceptance criteria（草稿）
- [ ] `kernel/subagent.py` 通用执行器（零 import domain）；Reader 复用它（#4 销账、grep SKELETON 计数对齐）
- [ ] `start_quiz` / `ingest` 作为 tool，内部考官作隔离子代理；子树嵌 tool_call 下、ReAct 上下文不含其内部 span
- [ ] 不变量：ReAct **绝不触判卷/记账**；考官内核（assess_once/ingest_resource）字节级不受影响
- [ ] 竖切/replay：脚本化"考我"→ ReAct→start_quiz 子代理→确定性 assessment→结果回，整轨迹零 token replay
- [ ] 五门全绿

## Blocked by
[S1 — Replay-safe tool 循环](01-tool-calling-loop-replay.md)

# R1-S2b — 交互式考核：next_question / submit_answer（对话回合驱动，不需 suspend/resume）

Status: blocked（方向已认可；待 S2 落地后细化 spec）
Type: AFK

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## Why（设计背景）

一次 ReAct `tool_call` 同步、不能中途等用户作答；交互考核多轮。用 **对话回合边界当天然暂停点**，把
`assess_once` 的"出题→答→判卷"拆成两个同步工具，**不需要 suspend/resume(#6)**：
- `next_question()`：选题 + 路由 + 出题（LLM enrich 槽）→ 返回题 + 持久化"当前待答题"状态。ReAct 把题问给用户。
- `submit_answer(answer)`：判卷（LLM basic 槽 / MC 代码）+ 记账 + 更新记忆 → 返回判决 + 追问。

确定性内核（选题/路由/判卷/记账）原样保留，只是跨两个可 replay 的工具步。这也是"自由 ReAct 驱动、确定性内核
被拆成可回放工具步"的最佳 demo。

## Acceptance criteria（草稿）
- [ ] `next_question` / `submit_answer` 两工具；"当前待答题"状态持久化（跨两次工具调用/跨对话回合）
- [ ] 复用确定性 selection/routing/grading/记账（不重写；`assess_once` 的组成部分抽出复用或包装）
- [ ] 不变量：ReAct 绝不触判卷/记账；两工具步整轨迹零 token replay
- [ ] 竖切：脚本化"考我"→ next_question→（用户答）→ submit_answer→判决，跨回合可 replay
- [ ] 五门全绿

## Blocked by
[S2 — 非交互工具](02-examiner-as-subagent.md)

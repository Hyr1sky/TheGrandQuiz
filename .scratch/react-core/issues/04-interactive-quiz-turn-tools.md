# R1-S2b — 交互式考核：next_question / submit_answer（对话回合驱动，不需 suspend/resume）

Status: done（merge 至 main bdabb45；五门全绿 351 passed ×3；assessment.py 空 diff；kernel↛domain KEPT）
Type: AFK

> 终审记：_QuizSession 持待答态（pending/recently_asked，进程内会话作用域），两工具闭包共享；rng 走
> new_rng(session.next_seed())（seed+计数器确定推进，禁墙上时钟/random）；复用 select_target/route/generate/
> grade/record/_compose_solution/_resolve_language（零逻辑重复、assess_once 空 diff）；跨回合两工具步零 token
> replay。4 类 mutation 全杀。非阻塞 concern：交互工具用 _resolve_language(task, None) 不读 question_language
> 偏好（assess_once 支持）→ 折进 S3 记忆/偏好注入对齐。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## Why（设计背景）
一次 ReAct `tool_call` 同步、不能中途等用户作答；考核多轮。用**对话回合边界当天然暂停点**，把 `assess_once`
的"出题→答→判卷"拆成两个同步工具，**不需 suspend/resume(#6)**。确定性内核（选题/路由/判卷/记账）原样复用，
只是跨两个可 replay 的工具步——正是"自由 ReAct 驱动、确定性内核被拆成可回放工具步"的最佳 demo。

## 锁定设计
- 在 S2 的 `register_learning_tools` 上再加两个 domain 工具（context-aware，走 S2 的 ctx 拿 emitter+parent_span）：
  - **`next_question(task)`**：`select_target` + `route_question_type` + 分型出题（`generate_multiple_choice` / `generate_question` probe/开放，LLM enrich 槽）→ 发 `QUESTION_ASKED` → 返回题 + options。**持久化"待答态"**（target_item_id / question_text / question_type / **mc 对象**（MC 判卷要用）/ asked_evidence）到会话状态。
  - **`submit_answer(task, answer)`**：读待答态 → 判卷（`grade_multiple_choice` if mc else `grade_answer`，LLM basic 槽 / MC 代码）→ 代码算 weak_item_id → `record_verdict` → 发 `ANSWER_JUDGED`/`CONCEPT_STATE_CHANGED` → 勉强/错则 `FOLLOWUP_GIVEN`（`_compose_solution`）→ 清待答态 → 返回判决 + 追问。
- **复用确定性内核、零逻辑重复**：next_question/submit_answer **组合现有子函数**（selection/routing/question/grading/memory/_compose_solution），**不重写**；`assess_once` 保持不变（CLI 仍用它；子函数是两条路径的唯一真相）。
- **待答态持久化**：会话作用域（进程内、按 task 键），与 S2 的工具闭包同一套会话依赖（store/memory/provider + 新增 pending + recently_asked）。replay 可重建（next_question 在轨迹里被录，回放同样 LLM 输出重建待答态）。
- **确定性**：rng 种子化（会话计数器确定推进，别用墙上时钟/全局 random）；MC 判卷走代码。
- **不变量**：ReAct **绝不触判卷/记账**（判卷 + record_verdict 在 submit_answer 的确定性代码里，非 ReAct LLM 决定）。

## Acceptance criteria
- [ ] `next_question` / `submit_answer` 两工具注册；待答态跨两次工具调用/跨对话回合正确传递
- [ ] 复用 selection/routing/question/grading/memory 子函数（不重写）；`assess_once` 空 diff
- [ ] 事件序：next_question 发 QUESTION_ASKED；submit_answer 发 ANSWER_JUDGED→CONCEPT_STATE_CHANGED→(FOLLOWUP_GIVEN)
- [ ] 判决/记账与 `assess_once` 对同输入逐字段一致（子函数共享的实证）
- [ ] 不变量测试：ReAct LLM 不产 verdict/weak_item_id（代码记账）；MC 判卷无 LLM 调用
- [ ] 竖切/replay：脚本化"考我"→ next_question →（用户答）→ submit_answer →判决，跨回合整轨迹零 token replay
- [ ] 五门全绿（含 lint-imports；kernel/tools.py 仍零 domain）

## Files (owner)
`domain/learning/tools.py`（+两工具 + 会话待答态）、必要的会话状态结构、新 `tests/test_react_quiz_tools.py`。
`assessment.py` 子函数**只 import 复用、不改**。

## Blocked by
[S2 — 非交互工具](02-examiner-as-subagent.md)（done）

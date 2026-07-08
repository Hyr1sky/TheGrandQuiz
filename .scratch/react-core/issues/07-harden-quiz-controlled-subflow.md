# R1-S6 — 硬化交互考核为受控子流程（start_quiz 替掉软工具）

Status: AFK done（merge 至 main 108e32c；五门全绿 367 passed ×3；assessment/ingest 空 diff）；**HITL 再 dogfood 待用户**
Type: AFK 建 + HITL 再 dogfood

> 终审记：start_quiz 复用 assess_once×N + 注入 Responder（_ScopedEmitter 重挂 assessment 根 span 到 TOOL_CALL）；
> MC 走 InteractiveResponder(questionary.select) → 逐字选项 → grade_multiple_choice 逐字命中（#2 从根修，mutation:
> 放松成子串匹配即杀 test_mc_prefixed_correct_option_grades_wrong）；删 next_question/submit_answer/_QuizSession；
> 语言偏好经 start_quiz→assess_once 透传（补 S2b/S4 欠账）；react_system.md 重写触发式 + 专业语气（不复述/不判卷/
> 不编题，修 #1/#3）。start_quiz 仅在注入 responder 时注册（test_react_tools 无 responder 不受影响）。内核空 diff。

> 缘起（真机 dogfood f0bf345）：S2b 的 next_question/submit_answer 软工具把逐轮编排压给 LLM，deepseek 守不住
> ——编题（没调 next_question 就出题）、串题、把答案加 "B. " 前缀毁掉 MC 逐字判卷（#2）、题目双重渲染（#1）、
> 被乱象逼得 confabulate（#3）。用户定架构方向：**硬化成一问一答受控子流程，MC 像 Claude Code 选择后 submit。**
> guard 全程顶住、记账未被污染——架构底子对，错在暴露方式。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build

把交互考核从"LLM 逐轮调工具"改成"**LLM 触发、runtime 受控驱动**"：

- **`start_quiz(count?)` 工具**（domain/learning/tools.py，context-aware）：内部跑**受控一问一答循环**——就是老
  `run_quiz` 那套 `assess_once × N`（复用！），用**注入的 Responder** 逐题作答；共享 emitter，内部 span 嵌
  TOOL_CALL 之下；返回一段结构化小结（考了几题、暴露哪些薄弱点）给 LLM。**LLM 不进逐题循环。**
- **MC 走选择器**：真机用 `InteractiveResponder`（questionary select，"Claude Code 式选择"）→ 提交**逐字选项文本**
  → `grade_multiple_choice` 逐字比对天然命中 → **#2 从根消失**（不再有 LLM 加前缀毁答案）。replay/测试用 ScriptedResponder。
- **移除 `next_question`/`submit_answer`** 出 LLM registry（软工具，失败方案）；保留 `ingest` + `query_weak_concepts`，
  新增 `start_quiz`。`_QuizSession` 跨工具待答态随之删除（受控循环同步跑、无跨工具 pending）。
- **语言偏好接入**：start_quiz 把 `SqlitePreferenceMemory` 透传给 assess_once（assess_once 本就支持 `preferences`
  → 偏好 > task > 中文）——顺带补齐 S2b/S4 欠的语言偏好。
- **重写 `react_system.md`**：LLM 职责收窄为"**触发** start_quiz(count)/ingest/query_weak + 报结果"，**不复述题目、不自己判卷、不编题**；语气**专业严谨、克制、不 emoji 刷屏、不道歉编故事**（仿 Claude Code/Codex）。version 进 trace。
- **react 会话装配**：把 InteractiveResponder 接进 registry 供 start_quiz 用；QuizEventPrinter 照旧呈现 Q&A（#1 因 LLM 不复述而解决）。

## Acceptance criteria
- [ ] `start_quiz` 工具跑受控 `assess_once × count` 循环（复用 assess_once，一行不改内核）；注入 Responder；内部 span 嵌 TOOL_CALL；返回结构化小结
- [ ] MC：InteractiveResponder 选择器 → 逐字选项文本 → 判卷正确（真机再 dogfood 验；ScriptedResponder 测覆盖 MC 选对/选错）
- [ ] 移除 next_question/submit_answer 出 registry；删 _QuizSession 待答态；ingest/query_weak 保留
- [ ] 语言偏好经 start_quiz→assess_once 生效（preferences 透传）
- [ ] react_system.md 重写（触发式编排 + 专业语气）；version 进 trace
- [ ] `assess_once`/`ingest_resource` 空 diff（复用不改内核）
- [ ] 竖切/replay：脚本化"考我 3 题"→ start_quiz→受控循环（ScriptedResponder）→ 小结，整轨迹零 token replay
- [ ] 测试改：删 next_question/submit_answer 测，加 start_quiz 受控循环测（含 MC 选对/选错 + 多题 + 语言偏好）
- [ ] 五门全绿（含 lint-imports；kernel↛domain）

## 人机边界（AFK 建完交回）
再 dogfood：确认 MC 选择器体验（选项列表、选中提交）、判题正确、一问一答顺、语气专业。

## Files (owner)
`domain/learning/tools.py`（start_quiz + 删软工具/待答态 + preferences 透传）、`interfaces/cli/app.py`（react 装配接 InteractiveResponder + registry 改）、`domain/learning/prompts/react_system.md`（重写）、`tests/test_react_quiz_tools.py`（改成 start_quiz 测）、必要时 `tests/test_cli_react.py`。**不碰** assessment.py/ingest.py 内核。

## Blocked by
[S5 — function-calling](06-real-provider-function-calling.md)（done）。之后 Slice B（路径穿越守卫）+ S3（ContextBuilder，dogfood 后）。

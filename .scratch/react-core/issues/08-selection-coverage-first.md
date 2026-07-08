# R1-S7 — 选题覆盖优先 + 兜底 remediation + start_quiz focus（修选题锁死）

Status: done（merge 至 main 572c3d6；五门全绿 376 passed；grading/ingest 空 diff；golden cassette 未伪造）
Type: AFK

> 终审记：select_target 三 focus（mixed=unasked→weak→all / new / weak），命门"薄弱+未考过→选未考过"
> mutation 已杀；eval case5/6 改成新策略断言（非弱化永真）；cassette fixtures diff 空。verify 诚实抓到一处
> concern：assess_once 把 recently_asked 串成 asked_item_ids 这条链原未被测（mutation 传 set() 未被杀）。
> **主循环补 test_multi_round_threads_asked_for_coverage_not_repeat**（同 seed 连考两轮共享 recently_asked
> → 第二轮避开第一轮 item；断开串联即同 seed 同全集重复 → 实测被杀），补齐该链。376 passed。

> 缘起（真机 dogfood e342b709）：6 道题（2 次 start_quiz）全锁在同一 item ae#003（10 个知识点只考 1 个），
> 全追问、全错、`薄弱=>薄弱` 死循环；用户两次要"考其他的概念"被无视。根因：`select_target` 薄弱优先**排他**
> ——有薄弱就候选集={薄弱∪观察中}、**排除新概念** → 一题答错就永久锁死那个概念。用户定策略：**覆盖优先 +
> 兜底 remediation + 可选 focus**。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build（动确定性核心 select_target + start_quiz + eval）

- **`select_target` 策略改**（selection.py，纯函数）：新增 `asked_item_ids`（本会话已考的 item 集）+ `focus` 参数：
  - `focus="mixed"`（默认，覆盖优先）：候选 = **未考过的（unasked）**若非空 → 否则**薄弱**若非空 → 否则全集。
  - `focus="new"`：候选 = unasked 若非空 → 否则全集。
  - `focus="weak"`：候选 = 薄弱若非空 → 否则 unasked → 否则全集。
  - 候选内仍 `rng.choice` 确定性选。**关键行为**：mixed 下"有薄弱 + 有没考过的" → 选**没考过的**（不再锁死薄弱）。
- **`assess_once` 传参**：把 `recently_asked` 的 keys 作 `asked_item_ids`、把 `focus` 下传 `select_target`（新增 focus 形参，默认 mixed → 向后兼容）。这是选题路径的合理改动（S6 的"assess_once 空 diff"不再适用于本 issue）。
- **`start_quiz(count, focus="mixed")`**：加可选 focus，透传 assess_once。ReAct 按用户意图设（"考其他的/没考过的"→new，"复习薄弱"→weak，默认 mixed）。react_system.md 补一句工具用法（focus 何时用）。
- **eval 用例更新**（case5/6 选题）：现有断言编码的是**旧排他策略**——更新成断言**新策略**（覆盖优先 + 薄弱兜底 + focus），断言 mutation 可杀；**不是"改到测试过"，是断言新的正确行为**。
- **题型变化自动恢复**：覆盖到新概念 → None→选择题/开放，题型不再全追问（无需改 routing）。

## Acceptance criteria
- [ ] `select_target` 三 focus 策略正确；mixed 下"薄弱 + 未考过"选未考过（**锁死 bug 回归测**：构造 1 薄弱 + N 未考，断言选中未考的，mutation 放回排他即红）
- [ ] `focus="weak"` 选薄弱、`focus="new"` 选未考过；确定性（同 seed 同 asked 同选）
- [ ] `assess_once` 传 asked_item_ids + focus 下传；默认 mixed 向后兼容
- [ ] `start_quiz(count, focus)` 透传；react_system.md 补 focus 用法（"考其他的"→new 等）
- [ ] eval case5/6 更新为新策略断言，全绿；**golden cassette（test_assess_replay/ingest_replay）仍绿**——若选题变化影响录制场景的 replay_key，调测试 setup 使命中（**不许伪造 cassette**；必要时说明如何保命中）
- [ ] 竖切：脚本化 start_quiz 多题覆盖多个 item（distinct item > 1），focus 生效
- [ ] 五门全绿（含 lint-imports）；`ingest_resource` 空 diff

## Files (owner)
`domain/learning/selection.py`（策略）、`domain/learning/assessment.py`（传 asked+focus 给 select_target——**仅选题调用处，勿动判卷/记账**）、`domain/learning/tools.py`（start_quiz focus 透传）、`domain/learning/prompts/react_system.md`（focus 用法）、`evals/cases/case5*.yaml`+`case6*.yaml` 及 `graders/rules.py` 对应断言、`tests/test_selection.py`（+新策略/回归测）、必要时 `tests/test_react_quiz_tools.py`。

## Blocked by
[S6 — 受控考核子流程](07-harden-quiz-controlled-subflow.md)（done）。之后 Slice B（路径守卫）+ S3。

# GKB-S6 — start_quiz 工具装配 + react/quiz CLI 端到端接线

Status: done（merge 至 main `e4a2ab7`，五门全绿 498 passed；start_quiz 描述统一+2 工具输入示例、question_type 参数级透传断言、端到端 scripted 轨迹（多资源库→scope honor + 题型 honor），3 mutation 全杀。发现 S3-S5 已接好运行管道、本 slice 只补缺口。真机体验属用户 dogfood。）
Type: AFK（真机体验属你 dogfood；建绿走 scripted/replay provider，轨迹 cassette 我录）

## Parent
[PRD: 全局 KB 重构](../PRD.md)

## What to build

把 S3（目录）/ S4（scope）/ S5（题型）在 ReAct 端到端串起来：`start_quiz` 工具 description 教 LLM **从注入的库存
清单里挑 exact resource_id 做 scope、并抽用户题型意图**；`run_react` / `run_quiz` 走 S2 的无-task 装配 + S3 的
目录注入。这是把"一个持久全局库 + 自然语言切材料/定题型"真正跑通的汇合竖切。

## 锁定设计（不留给实现猜）

- **`start_quiz` 工具 description 重写**：教 LLM——
  - scope：从上下文里的**库存清单**（S3 注入的 `{resource_id → topic}`）认出用户意图对应哪个/哪些材料，填其
    **exact resource_id** 进 `resource_ids`；用户没指定材料 → 不填（默认全库）。给 1-2 个工具输入示例
    （如 用户"考代理通信协议的简答题" → `{resource_ids:[<该主题 resource_id>], question_type:"简答"}`）。
  - question_type：只抽用户意图短语填 `question_type`；没提 → 不填（走自动路由）。
  - 仍守：不复述题目、不自己判卷、不编题。
- **`run_react` 装配**（承 S2/S3）：无 LearningTask；ContextBuilder 的 memory 分区 provider 注入库存清单 +
  薄弱/偏好（S3 的 `learner_context_provider`）；`register_learning_tools` 无 task 线程；`start_quiz` 收
  resource_ids/question_type 透传。
- **`run_quiz` / CLI**：title 可选横幅；quiz 子命令仍可跑（无-task），空库预检走 `all_items()`。
- **确定性/replay**：LLM 的 scope/题型选择是被录进 completion 的 tool_call → 走同一 ReplayProvider、轨迹可回放；
  `test_cli_react` 用 scripted/replay provider 驱动（脚本化 tool_call 或录制轨迹），CI 零 token。

## Acceptance criteria

- [ ] `start_quiz` description 教 LLM 从库存清单挑 exact resource_id 填 `resource_ids` + 抽 `question_type` 意图；含 1-2 工具输入示例
- [ ] `run_react` 无-task 装配 + 库存清单注入 + resource_ids/question_type 端到端透传
- [ ] `run_quiz`/CLI title 降为可选横幅；空库预检走 all_items
- [ ] `test_react_quiz_tools`：resource_ids/question_type 经工具透传到 assess_once（参数级断言）
- [ ] `test_cli_react`：scripted/replay provider 驱动一条"选材料+定题型"的 react 轨迹、零 token 回放、轨迹一致
- [ ] TDD/回放：工具装配、CLI 装配、轨迹 replay，各 mutation 可杀
- [ ] 五门全绿（含 lint-imports）；真机轨迹 cassette 录制（我录）

## Files (owner, 可能漂)
`domain/learning/tools.py`(start_quiz description + register 装配)、`interfaces/cli/app.py`(run_react/run_quiz/子命令)、
`tests/test_react_quiz_tools.py`、`tests/test_cli_react.py`、（如需）react 轨迹 cassette。

## Blocked by
GKB-S3、GKB-S4、GKB-S5（三者的目录/scope/题型都要在，才能端到端接线）。

# R1-S3 — ContextBuilder（M5）：分区装配 + 学情记忆注入（薄弱+偏好）

Status: done（merge 至 main d2ded87；五门全绿 405 passed ×3；内核四文件空 diff；kernel↛domain KEPT）
Type: AFK

> 终审记：kernel/context.py ContextBuilder（Partition=名字+provider(str|callable)+可选 budget，空分区跳过，
> CompressionPolicy 钩子默认恒等=压缩接缝）；domain/learning/context.py learner_context_provider 闭包捕获引用
> （非快照）→ 反映记忆变化，薄弱按 item_id 升序确定、偏好经 _PREFERENCE_LABELS 可扩展；run_agent_turn 用
> builder（向后兼容，run_turn 不碰）；react CLI 装配 system+memory 分区。内核（assessment/selection/grading/ingest）
> 空 diff、golden cassette 不受影响（ContextBuilder 只在 ReAct 决策槽）。3 mutation 全杀。**R1 收官。**

> R1 收尾。用户定：memory 注入=薄弱+偏好（可扩展、后续按需补）；token 预算/上下文压缩**暂缓但留好接缝**
> （用户要后续动手做 context compression 学习巩固）。dogfood 未暴露上下文膨胀，故 YAGNI 到需要时再压缩。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build

把 ReAct 上下文从"system + history 临时拼装"升级成**分区装配**，并注入学情记忆——让 agent **不调工具**就知道
学习者薄弱点 + 偏好，从而更聪明编排（这是"记忆互通复用"的兑现）。

- **`kernel/context.py` `ContextBuilder`（领域无关机制）**：有序分区，每个分区 = 名字 + 内容 provider（`str` 或
  `Callable[[], str]`，callable 每次 build 现取 → 学情随考核推进刷新）。`build(history, user_message) -> list[Message]`
  按序装配：system 分区 → 注入分区（如 memory）→ history → user。**扩展性要足**：分区是可增列表（日后加
  persona/knowledge 分区零改机制）；**给"每分区 token 预算 / 压缩策略"留清晰接缝**（如分区带可选 budget 字段、
  或 build 预留 policy 钩子）——**本 issue 不实现压缩**，只把缝留对（下一程 context compression 往这插）。
  kernel↛domain：机制只认"名字 + 字符串 provider"，不认识领域语义。
- **`run_agent_turn` 经 ContextBuilder 装配 messages**（替 ad-hoc system+history）；保工具循环 + replay 不破
  （messages 随 memory 状态确定 → replay 对齐）。向后兼容：无 ContextBuilder 时退回原 system+history（run_turn/既有测试不破）。
- **domain 学情分区 provider**：domain 侧函数，把当前薄弱概念（Learning Memory）+ 偏好（语言，结构可扩展到难度）
  渲成一段紧凑"学情"文本。**可扩展**：新增偏好/字段是加渲染项、不改结构。react CLI 组装点把它（闭包捕获 memory+preferences）
  作 memory 分区 provider 传给 ContextBuilder（domain→kernel 合法，同 M6/M4 套路）。
- **react CLI（app.py）**：建 ContextBuilder = system 分区（react_system.md）+ memory 分区（domain provider），接进 Runner。

## Acceptance criteria
- [ ] `kernel/context.py` ContextBuilder：分区有序装配（纯函数可测）；provider 支持 str/callable（callable 每 build 现取）；**扩展点清晰**（加分区不改机制；预算/压缩接缝在但未实现）。kernel↛domain（lint-imports 绿）
- [ ] run_agent_turn 经 ContextBuilder 装配；无 builder 时向后兼容退回原路径；工具循环/replay 不破
- [ ] domain 学情 provider：渲染薄弱概念 + 语言偏好；memory 变化后 provider 现取反映（测：先无薄弱→注入空/占位，record_verdict 后→注入含该概念）
- [ ] react 会话把学情注入 messages（测：脚本化会话，断言注入分区出现在第二次 model 调用的 messages 里）
- [ ] 竖切/replay：脚本化 react 会话整轨迹零 token replay（含注入的学情分区，确定）
- [ ] `assess_once`/`ingest`/`selection`/`grading` 空 diff；golden cassette 不受影响（ContextBuilder 只在 ReAct 路径、不碰 assess_once 出题/判卷 messages）
- [ ] 五门全绿

## 明确暂缓（留缝、非遗漏）
- token 预算 / 历史滑窗 / 老轮摘要压缩 = **下一程 context compression**（用户要动手学）——本 issue 只留接缝，不实现。
- knowledge/persona 分区：结构支持，本 issue 不建（YAGNI）。

## Files (owner)
新 `kernel/context.py`、`kernel/runner.py`（run_agent_turn 用 ContextBuilder + 向后兼容）、新 domain 学情 provider
（`domain/learning/` 下，如 context.py 或并入 tools.py）、`interfaces/cli/app.py`（run_react 装配）、新 `tests/test_context.py`（+必要 test_cli_react）。**不碰** assess_once 出题/判卷路径、selection、grading、ingest 内核。

## Blocked by
[S2b](04-interactive-quiz-turn-tools.md)、[S6](07-harden-quiz-controlled-subflow.md)、[S7](08-selection-coverage-first.md)（均 done）。R1 最后一块。

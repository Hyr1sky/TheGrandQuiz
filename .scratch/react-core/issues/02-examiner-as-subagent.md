# R1-S2 — 非交互考官/记忆工具接入 ReAct 循环（ingest + query_weak）

Status: done（merge 至 main d56712f；五门全绿 344 passed ×3；ingest/assessment 空 diff；kernel↛domain KEPT）
Type: AFK

> 终审记：巧思 `_ScopedEmitter`（把无父根 span 重挂到 TOOL_CALL）让 ingest_resource 一行不改即嵌进工具边界、
> ReAct 上下文只收结构化 JSON（隔离，mutation 改透传即红）。整轨迹（选工具+Reader+final）零 token replay。
> 两非阻塞 concern：(1) 工具尚未接真机 CLI（→ S4 live react CLI，R1 收尾）；(2) _ScopedEmitter 只覆写 3 方法、
> 日后 ingest 调别的 emitter 方法会 AttributeError（fail-loud 且测试可捕，下次碰这块顺手加固）。

> 重切记（S1 落地后）：交互式考核（start_quiz）撞上同步 tool 调用 ⊥ 缓办的 suspend/resume(#6)——拆到 S2b
> 走 next_question/submit_answer。`kernel/subagent.py`(#4) 提取按 YAGNI 暂缓（仅 Reader 一个用户，隔离已在工具边界达成）。
> 本 issue 只做**非交互工具**——干净的同步 tool，证明"考官/记忆插进 ReAct 循环 + 工具边界隔离 + 端到端 replay"。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## What to build

在 S1 的 `ToolRegistry` 上注册两个 domain 工具（在组装点/domain 层，**不进 kernel**），让 ReAct 主体能调：
- `ingest(url)`：handler 调 `ingest_resource(...)` → 返回结构化结果（如入库知识点数 + 概念名列表）。内部 span
  （fetch / Reader model / item_created）**嵌在本次 `TOOL_CALL` span 之下**、进 trace；ReAct 的消息上下文
  **只收到结构化结果字符串**，看不到考官内部 span / 消息（隔离在工具边界）。
- `query_weak_concepts()`：只读——读 Learning Memory（薄弱 item + 状态）+ store（概念名）→ 返回薄弱概念摘要。
  无 LLM、确定性。给 ReAct 一个"我哪些概念薄弱"的会话能力，也是 S3 记忆注入的种子。

## 锁定设计
- **工具执行上下文**：dispatch 要把 `emitter` + 当前 `parent_span_id`（即 `TOOL_CALL` span）传给 handler，
  好让 domain 工具把内部事件**挂在 TOOL_CALL 之下**。先查 S1 的 Tool/handler 签名——若未传执行上下文，
  **最小扩展**成传一个 kernel-generic 的 ctx（emitter + parent_span_id）；kernel 侧 ctx 不认识领域语义。
- **domain 工具住 domain 层**（如 `domain/learning/tools.py`，import kernel 的 Tool + domain 函数——domain→kernel 合法）；
  `kernel/tools.py` 保持零 import domain（lint-imports 绿）。注册在组装点（CLI/react 装配）。
- **考官内核不改**：`ingest_resource` / `assess_once` 签名逻辑一行不动（工具是 wrap，不是改写）。
- **隔离不变量**：一次 ingest 工具调用后，ReAct 的 messages 只含工具**结果**、不含 ingest 内部 model 调用消息。
- **replay**：一次"ReAct 调 ingest"的 turn，脚本化/回放 provider 同时喂 ReAct 的工具选择 LLM 与 ingest 内部
  Reader LLM → 整轨迹零 token replay。

## Acceptance criteria
- [ ] `ingest` / `query_weak_concepts` 两工具注册进 S1 的 ToolRegistry；组装点在 domain/interface，`kernel/tools.py` 仍零 domain（lint-imports 绿）
- [ ] 工具 dispatch 把执行上下文（emitter + parent_span_id）传给 handler；ingest 内部 span 嵌在 TOOL_CALL 之下（trace 树验证）
- [ ] 隔离不变量测试：ingest 工具调用后 ReAct messages 只含结果、不含考官内部 model 消息
- [ ] `ingest_resource` / `assess_once` 空 diff（工具 wrap 不改内核）
- [ ] 竖切/replay：脚本化"帮我入库这篇"→ ReAct→ingest 工具→入库→结果回，整轨迹零 token replay（inner.calls 不变）
- [ ] TDD：工具注册/dispatch、结果结构、隔离不变量、span 嵌套、replay 一致，各 mutation 可杀
- [ ] 五门全绿（含 lint-imports）

## Files (owner)
新 `src/grandquiz/domain/learning/tools.py`（两 domain 工具）、组装点（`interfaces/cli/app.py` 或新 react 装配）、
必要时最小扩展 `kernel/tools.py` / `kernel/runner.py`（传执行上下文 ctx）、新 `tests/test_react_tools.py`（+必要 replay 测试）。

## Blocked by
[S1 — tool 循环](01-tool-calling-loop-replay.md)（done）。串行下一步 S2b（交互考核）+ S3（ContextBuilder）。

# PRD: 考核竖切 MVP（Assessment Vertical Slice）

Status: ready-for-agent
Created: 2026-06-15

> 一条穿透全部 runtime 能力的最小考核循环。本 PRD 覆盖 MVP 的目标、领域模型与 8 个验收用例；
> 实现按 architecture.md 搭建顺序 M1→M8 分增量落地，每增量一个独立可验收 issue（见 `issues/`）。
> 术语以根目录 [CONTEXT.md](../../CONTEXT.md) 为准；遵循 ADR-0002（概念同一性）/ 0003（记忆两库）/ 0004（循环是 workflow）。

## Problem Statement

我（用户 #1，学习者）学新技术时会读文章 / 文档 / 仓库，但**没有可靠手段判断自己真正掌握了什么、哪些只是"看过觉得懂"**。现有工具（Claude、NotebookLM）擅长总结和答疑，却不会：反复拷问我、跨会话记住我哪些概念薄弱、并在下次优先重考这些薄弱点。重读材料感觉很充实，但不暴露盲区。我想要的是"被考核"——被追问学过的内容、薄弱概念被记住、下次先考薄弱点。

## Solution

一个**考核驱动**的学习工具。我喂给它一个资源（URL），它深读并抽取出一批 KnowledgeItem 候选；我在**审批门**剔除垃圾 item 后才入库。我说"考我"，它就**逐题交互**地拷问我——出题锚定材料、答得勉强或错就**追问**深挖。它对每次回答给出**判决**（对 / 勉强 / 错），把薄弱概念按具体 KnowledgeItem 写入 **Learning Memory**，复考时**薄弱优先**。一个薄弱概念要**连续答对两次**才销账。整条链路落 trace，可确定性回放、可 eval。

## User Stories

1. 作为学习者，我想创建一个 LearningTask（如"React"），以便把某个主题的资源、知识、薄弱点都归到一个考核范围下。
2. 作为学习者，我想手动喂一个资源 URL 到某个 LearningTask，以便用我自己挑的材料建知识库（MVP 不做自动发现）。
3. 作为学习者，我想让 agent 深读这个资源并抽取出 KnowledgeItem 候选清单（概念名 + 摘要 + 原文证据 + 置信度），以便把长材料压成可考核的最小知识单元。
4. 作为学习者，我想在 KnowledgeItem 入库前看到候选清单预览，以便剔除抽错 / 无价值的 item，防止垃圾 item 污染后续考核。
5. 作为学习者，我想审批通过后这些 KnowledgeItem 才正式入库，以便知识库只包含我认可的内容。
6. 作为学习者，我想随时对某个 LearningTask 说"考我"，以便按需触发考核（无主动提醒、无复习排期）。
7. 作为学习者，当知识库为空时我说"考我"，我希望 agent 拒绝出题并引导我先喂资源，而不是凭空编题。
8. 作为学习者，我想让出的每道题都锚定一个真实存在的 KnowledgeItem 且带其原文证据，以便考核内容可追溯、不跑题。
9. 作为学习者，我想首次接触的概念用选择题热身、默认开放问答、薄弱概念复考用追问深挖（题型路由），以便难度与我的状态匹配。
10. 作为学习者，我想逐题作答——一题一判一反馈，而不是一次性做一套卷，以便像被面试一样被追问。
11. 作为学习者，当我答得勉强或答错时，我想被追问或给出正解，以便当场把盲区挖出来。
12. 作为学习者，我想让每次回答得到结构化判决（对 / 勉强 / 错 + 指认的薄弱 KnowledgeItem + 所引证据），以便判卷有依据、可复查。
13. 作为学习者，我想让答错 / 答勉强的概念按 KnowledgeItem id 写入 Learning Memory，以便系统记住我哪里弱。
14. 作为学习者，我想在复考时让选题优先来自"薄弱优先"候选集，以便先补最该补的。
15. 作为学习者，我想让一个薄弱概念在我连续答对两次后才销账（答对一次只转"观察中"），以便防止蒙对或刚看完的假掌握过早消失。
16. 作为学习者，我想让一个"观察中"或已销账的概念若再答错就被打回"薄弱"，以便掌握判定经得起反复。
17. 作为学习者，我想随时中止一次"考我"会话，以便不被一整套题绑住；半成品状态应落 trace。
18. 作为学习者，我想让我的偏好（题型偏好、追问强度、语言）被记住（Preference Memory），以便考官逐渐贴合我的风格。
19. 作为学习者，我想在 CLI 里完成上述全部交互，以便开发期就有可用的主力界面（不依赖 Web）。
20. 作为 runtime 开发者，我想让 runner 在每个生命周期节点发射结构化 AgentEvent，以便 trace / hook / 流式输出 / eval 共享同一条事件流。
21. 作为 runtime 开发者，我想让领域事件（ResourceApproved / ItemCreated / AnswerJudged / ConceptStateChanged）经 kernel `emit()` 上同一条脊柱，以便 eval 能在 trace 上断言领域行为，同时 kernel 保持领域无关。
22. 作为 runtime 开发者，我想把一次考核会话完整录制后逐字节回放一致，以便 eval 不烧 token、完全确定。
23. 作为 runtime 开发者，我想让所有外部 I/O（LLM / fetch / 时钟 / 随机）都可记录回放，以便回放是事件流回放而非仅 LLM 回放。
24. 作为 runtime 开发者，我想让审批门是"可挂起 / 可恢复的 turn"（发 ApprovalRequested 事件 + 持久化待决状态 + 凭 token 恢复），以便接口形状第一天就能跨 SSE / HTTP。
25. 作为 runtime 开发者，我想让出题 / 判卷 / Reader 的输出经 pydantic schema 强制校验、失败自动重试（ModelRetry），以便"输出可验证"。
26. 作为 runtime 开发者，我想让"出题锚定存在的 KnowledgeItem 且 evidence 非空"成为运行时校验门（不只是 eval 断言），以便幽灵题在到达我之前被挡。
27. 作为 runtime 开发者，我想让薄弱状态机转移、选题候选集构造、Learning Memory 写入全由确定性代码完成，以便 eval 可断言、replay 可对齐（LLM 判卷，代码记账）。
28. 作为 runtime 开发者，我想让 LLM 只在"出题""判卷"两个有界槽里被调用，自由 ReAct 仅用于开放编排，以便核心路径可复现。
29. 作为 runtime 开发者，我想用两个命名 LLM 角色（basic 走 deepseek、enrich 走 qwen）配置 provider，以便不同任务用不同模型，并为后续意图 / 模型路由预留动态选择。
30. 作为 runtime 开发者，我想让每个 turn 的 token 用量进 trace，以便 eval 报告带成本列。
31. 作为 runtime 开发者，我想让 prompt 模板独立于代码存放、trace 记 prompt 版本号，以便 eval 回归可归因。
32. 作为 runtime 开发者，我想让抓取的网页内容被打"不可信"标记并受 fetch 层大小 / 超时 / 域名限制约束，以便抵御注入。
33. 作为 runtime 开发者，我想让深读 fetch 失败时资源被标记失败、不产生幽灵 KnowledgeItem，以便错误不污染知识库。
34. 作为 runtime 开发者，我想让确定性核心（状态机 / 选题 / 销账 / 事件信封）走 TDD，以便守住 eval 命门不变量。

## Implementation Decisions

**架构基线（来自 architecture.md / ADR）**

- **核心考核循环是确定性 workflow**（ADR-0004）：选题 → 出题 → 答 → 判卷 → 状态转移 → 下一题。LLM 仅在"出题""判卷"两槽被调用；自由 ReAct 只用于开放编排。
- **LLM 判卷，代码记账**：薄弱状态机转移、选题候选集构造、Learning Memory 写入是确定性代码；出题 / 判卷是 LLM 工具。
- **事件是信封**：`AgentEvent` = `type`（字符串）+ 元数据 + 不透明 payload。kernel 泛型分发 / 持久化、不认识具体类型；domain 在 domain 层定义领域事件经 kernel `emit()` 发射。`kernel/` 禁止 import `domain/`。
- **回放是事件流回放**：外部 I/O（LLM / fetch / 时钟 / 随机）经工具、结果作为事件落脊柱；Record/Replay Provider 是其中一个特例。时钟 / 随机走注入。

**模块（按搭建顺序 M1→M8，逐增量交付）**

- `kernel/events.py` — AgentEvent 信封类型体系（先定，全系统数据契约）。
- `kernel/runner.py` — 事件化的执行循环（自 scholarmate 移植 + 事件化改造）。
- `kernel/trace.py` — TraceStore，订阅事件落 SQLite，span 成树（turn → model_call → tool_call → subagent）。
- `kernel/clock.py` — Clock 抽象 + 种子化 RNG（确定性地基）。
- `kernel/hooks.py` — HookManager：interceptor（`before_*` 可改参 / 阻断）与 observer（`on_*` / `after_*` 只读）两类；hook 抛异常被隔离。
- `kernel/context.py` — ContextBuilder：分区拼装（system / persona / memory / knowledge / history）+ token 预算 + 跨轮裁剪（只留最终 assistant 回答）+ 渐进披露。
- `kernel/memory.py` — Memory 抽象（store / recall / policy）。
- `kernel/recovery.py` — ErrorClass 分类法 + RecoveryPolicy。
- `kernel/subagent.py` — Subagent 执行器（隔离上下文 + 结构化输出契约）。MVP 唯一 subagent 是 Reader。
- `kernel/approval.py` — 审批门原语：发 ApprovalRequested 事件 + 持久化待决状态 + 凭 token 恢复（CLI MVP 可用阻塞 prompt 实现，接口形状按 suspend/resume 定）。
- `providers/llm.py` — OpenAICompatProvider（移植）+ DemoEchoProvider；支持多命名角色（`basic` / `enrich`），从 `.env` 读取（`LLM_*` / `ENRICH_LLM_*`）。
- `providers/replay.py` — Record/Replay Provider（按 messages 哈希录放）。
- `providers/usage.py` — token 用量 / 成本核算。
- `domain/learning/` — 领域模型与领域事件（见下）；出题 / 判卷工具；考核 workflow 编排；薄弱状态机；选题。
- `interfaces/cli/` — REPL，订阅事件流呈现。
- `evals/` — cases（YAML）+ graders（规则断言 / LLM-judge）+ harness + 报告。

**领域模型（ADR-0002 / 0003）**

- `LearningTask`（主题容器 / 考核范围）→ 资源 → `KnowledgeItem`（概念名 + 摘要 + 原文证据 + 置信度，资源内唯一；概念同一性边界，MVP 不跨资源归并，预留 `concept_key`）。
- `Learning Memory`：薄弱概念（锚定 KnowledgeItem id）+ 三态（薄弱 / 观察中 / 已销账）+ 连对计数 + 判决历史。**唯一选题数据源**。
- `Preference Memory`：题型偏好 / 追问强度 / 语言，带 confidence，挂 `after_turn` 由 LLM 判断写入。
- Resource Memory 并入 KnowledgeItem；Session Memory 是 kernel 会话历史，非 domain 记忆。

**关键契约 / 交互**

- 薄弱状态机：`错|勉强 → 薄弱`；`薄弱 +答对 → 观察中`；`观察中 +答对 → 销账（移出表）`；任一态 `+错|勉强 → 薄弱`。
- 选题：代码查 Learning Memory 构造**薄弱优先候选集**（有薄弱概念时新概念不进集）；LLM 在候选集内挑题。
- 出题工具产出后经确定性校验门（锚定的 KnowledgeItem 存在 + evidence 非空），不达标 → ModelRetry / 拦截。
- 判决 schema：`{verdict: 对|勉强|错, weak_item_id, cited_evidence}`；选择题确定性比对，开放问答 / 追问 LLM 判且必须引 evidence。
- SQLite 迁移：版本号 + 顺序 SQL 文件，不上 alembic。

## Testing Decisions

好测试只断言外部行为，不耦合实现细节。三条缝（已与开发者确认）：

- **缝 1 — 事件 / trace 流（最高主缝）**：用 Replay Provider（确定性 LLM）驱动 Runner 跑脚本化用户输入，断言发射出的 AgentEvent 流（含领域事件 ResourceApproved / ItemCreated / AnswerJudged / ConceptStateChanged）。8 个 eval 用例都活在这条缝上。结构对标 inspect_ai 的 Solver（跑）+ Scorer（断言）。**被测模块**：runner + domain workflow + trace，端到端经事件观察。
- **缝 2 — 确定性核心单元缝**：纯函数直接 TDD（红-绿-重构）——薄弱状态机、候选集构造、销账记账、事件信封。**被测模块**：`domain/learning` 的状态机 / 选题、`kernel/events`。这些是 eval 命门不变量。
- **缝 3 — 结构化输出契约缝**：喂畸形 / 未锚定的回放响应，断言 validator 拒绝并触发 ModelRetry（如题引用不存在的 KnowledgeItem 在到达用户前被挡）。**被测模块**：出题 / 判卷 / Reader 工具的 schema 校验。
- 确定性核心走缝 2，LLM 槽走缝 1+3。Prior art：当前仅 `tests/test_smoke.py`；本 PRD 的测试是仓库首批真实测试，缝 1 同时奠定 eval harness 形状（M8）。

**8 个验收用例（缝 1，跑在 trace 上）**：① 未审批 item 不得入库；② 空库"考我"拒绝出题并引导喂资源；③ 出题锚定存在的 KnowledgeItem 且 evidence 非空；④ 答错 → 薄弱按 item id 入记忆；⑤ 出的题 ∈ 薄弱优先候选集；⑥ 答对一次转观察中、连对两次销账；⑦ 深读 fetch 失败 → 资源标记失败、不产生幽灵 item；⑧ 题型路由（首次→选择题，薄弱复考→追问）。

## Out of Scope

- 资源自动发现 / 搜索（`search_learning_resources`）——手动喂 URL（mock 资源源）即可。
- 学习路线规划（Planner）、总结 / 笔记（Summarizer / `generate_summary`）。
- 面试 subagent 与语音（ASR）——绑定面试场景门后等候；persona 仅 system prompt 语气、不做形象。
- 间隔重复 / 复习排期（无排期概念，纯按需触发）。
- 跨资源概念归并（预留 `concept_key`，二期做）。
- 多用户 / 鉴权、Web 前端、FastAPI+SSE 通道（接口形状预留，MVP 只做 CLI）、向量数据库。
- 质量 eval（LLM-judge + golden set 测判卷准不准）——MVP 只做行为 eval（规则断言）。

## Further Notes

- **增量路线**：M1 事件脊柱+runner → M2 Trace+Replay → M3 考核竖切（走骨架，可拆 3-5 子 issue）→ M4 HookManager → M5 ContextBuilder → M6 RecoveryPolicy → M7 Memory 正式化（SQLite 替换 M3 的 dict）→ M8 Eval harness。M3 里可用 dict 假装 memory、阻塞 prompt 假装审批门，M4-M7 再换正式实现。**不在竖切跑通前打磨任何 kernel 层。**
- **对标仓库（学习 by 模仿，详见 docs/reference-map.md）**：tracing→openai-agents-python（span 字段 / processor 管线）；hooks→claude-agent-sdk-python（PreToolUse/PostToolUse / matcher / 可阻断）；eval→inspect_ai（Task/Solver/Scorer 分离）；结构化输出→pydantic-ai（output_type + ModelRetry 自动重试）。各取一瓢，不整体采纳框架。
- **LLM 配置**：`.env` 已定 basic（deepseek-v4-flash）/ enrich（qwen3.7-plus）两角色，均 OpenAI-compat；后续加意图路由 + 模型路由时扩展环境变量做动态选择。
- 移植不是照搬：runner 进新仓库时同步事件化改造；移植前查 reference-map 的"不要带过来的旧坑"。

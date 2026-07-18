# PRD：自然材料问答与 Agentic Search 成本收口

Status: approved（2026-07-19；进入 AFK 实施）
Triage: ready-for-agent
Decision: 延伸 [ADR-0008](../../docs/adr/0008-revisioned-document-tree-and-grounded-knowledge-graph.md)，不重开 DS-S5

## Problem Statement

TheGrandQuiz 已经具备 current ResourceRevision、DocumentNode 树、FTS5、受限正文读取和精确 citation，真实
dogfood 也证明显式要求 Agent 执行 selected search → bounded read → exact citation 时能够通过联合审计。但是，
普通用户只用自然语言询问材料内容时，开放 ReAct 仍可能把文档节点编号当作引用后直接结束，没有形成可逐字解析的
citation；用户必须知道并暗示底层工具链，产品能力才可靠出现。

当前自由 ReAct 还需要模型逐步决定 scope、搜索、展开、读取、修正 citation 参数和最终作答。真实 trace 基线显示，
普通材料问题首轮需要 8 次模型调用、10 次工具调用和 82,581 tokens，最终仍为 0 条精确 citation；显式 grounded
问题需要 11 次模型调用、11 次工具调用和 132,403 tokens。每轮把不断增长的工具 JSON 历史重新放回 prompt，导致
单次 prompt 从约 4k 增长到 15k tokens。这个成本与可靠性都不适合作为自然问答入口。

用户需要一个不要求了解工具名的开放提问接口：直接给出 query 和明确材料范围，就能在预算内搜索、读取、回答并
返回精确证据；同一能力既可作为 ReAct 工具暴露给外层 LLM，也可由 CLI、API 或未来独立问答界面直接调用。它必须
复用既有文档结构与事件脊柱，不能削弱 exact scope、read-before-cite、逐字证据、注入防护或核心考核 workflow。

## Solution

建立 `GroundedDocumentAnswer` 深模块，封装一次完整而有界的材料问答 workflow。调用者提供 query、精确资源范围与
候选/读取/token 预算；代码确定性执行 scope 解析、FTS 候选搜索和受限正文读取，只在一个结构化 LLM 槽中完成证据
选择与答案组织，再由代码验证 quote/span 并渲染 revision、node、section_path citation。找不到证据、预算不足或
引用不可唯一验证时 fail closed，返回结构化状态而非无依据答案。

该模块提供两个入口但只有一份实现：开放 ReAct 注册一个高层 `answer_from_documents` 工具，自然材料问题优先路由到
它；CLI/API/未来独立 ask 入口可直接调用模块，避免外层 ReAct 循环。现有 outline/search/expand/read/cite 原子工具
继续保留，用于复杂探索和调试。所有搜索、读取、内部模型调用、citation 成功/拒绝和预算指标继续进入既有
`AgentEvent` 事件流。

以真实 trace 建立可执行成本门：自然问题无需显式工具名也必须得到至少一条精确 citation，且 ReAct 路径不超过
4 次模型调用、45,000 累计 tokens，读取不超过目标 revision 正文的 25%。若组合 workflow 达标，不新增通用工具
历史压缩器；只有可复现 trace 证明仍未达标时，才另立受限的上下文投影改进。

## User Stories

1. 作为学习者，我希望直接用自然语言询问已 ingest 的材料，而不必知道任何搜索或引用工具名。
2. 作为学习者，我希望回答自动附带材料、修订、章节路径和逐字引文，以便回到原文复习。
3. 作为学习者，我希望指定一份材料后只在该材料中查找，不因没有命中而静默扩大到全局 KB。
4. 作为学习者，我希望问题涉及多份明确材料时，系统只在我选定的资源集合内综合回答。
5. 作为学习者，我希望系统找不到足够原文证据时明确说明，而不是凭模型常识补全。
6. 作为学习者，我希望回答区分“材料明确陈述”“由多段原文归纳”和“材料未覆盖”。
7. 作为学习者，我希望 citation 的 section_path 可读，即使底层 node_id 不适合直接展示。
8. 作为学习者，我希望长材料问答只读取相关章节，不把整篇文档塞进上下文。
9. 作为学习者，我希望同一个问题在相同 revision 与 Replay 下得到可复现的搜索范围和引用验证结果。
10. 作为学习者，我希望材料中的 prompt injection 文本始终被当作不可信内容，不能改变 Agent 的系统约束。
11. 作为 ReAct 调用者，我希望用一个高层工具完成有依据的材料问答，减少多轮工具规划和参数修复。
12. 作为 CLI/API 调用者，我希望直接调用同一问答模块，避免为了得到答案而启动一个外层自由 ReAct 循环。
13. 作为未来界面开发者，我希望得到结构化答案、citations、已搜索/已读取节点和预算指标，而不是解析自然语言日志。
14. 作为维护者，我希望 exact scope 在任何搜索和读取前由代码解析，无法解析时零读取并 fail closed。
15. 作为维护者，我希望候选数、单节点读取量、总读取量和模型 token 都有显式上限。
16. 作为维护者，我希望 LLM 只能从代码已读取的窗口选择证据，不能自报任意 node、revision 或 span。
17. 作为维护者，我希望每条 quote/span 都由代码逐字验证，重复 quote 或越界位置不能成为 citation。
18. 作为维护者，我希望无证据、预算耗尽、scope 无效和引用验证失败是稳定的结构化结果。
19. 作为维护者，我希望组合 workflow 复用 Document Structure 深模块，不复制 FTS、树遍历或 citation 解析逻辑。
20. 作为维护者，我希望现有原子工具仍可用于复杂开放探索和故障诊断，不被高层工具删除。
21. 作为维护者，我希望搜索、读取、模型调用、citation 和拒绝事件沿同一事件脊柱落 trace。
22. 作为维护者，我希望 kernel 保持领域无关，不能为了问答 workflow 反向 import learning domain。
23. 作为评测者，我希望自然问题的 Replay 用例能断言无需显式工具名、精确 citation、严格 scope 和读取预算。
24. 作为评测者，我希望报告模型调用次数、工具调用次数、累计 tokens、最大 prompt 和读取占比，以便比较基线。
25. 作为评测者，我希望真实模型 cassette 由授权录制产生，tool schema 或 prompt 改变后不能手工修补指纹。
26. 作为项目作者，我希望把现有 Agentic Search 基座收口为可用产品能力，而不是再叠加一个通用 RAG 框架。
27. 作为项目作者，我希望成本优化以可执行质量门为前提，不能通过移除证据、扩大 scope 或降低验证强度换取。
28. 作为项目作者，我希望 DS-S5 KnowledgeRelation 保持关闭，除非未来出现独立的多跳/前置关系产品证据。

## Implementation Decisions

### GroundedDocumentAnswer 深模块

- `GroundedDocumentAnswer` 是 learning domain/application workflow，不是 kernel 原语、Reader subagent 或第二个自由
  ReAct loop。它封装复杂度并提供稳定的小接口。
- 输入包含 query、非空 exact resource ids、候选节点上限、总读取字符/token 预算和回答模型预算。第一版不接受
  unresolved 自然语言 scope，也不自动回退全库。
- 输出是结构化结果：answer、verified citations、searched/read node ids、scope、usage/读取指标，以及
  answered、no_evidence、invalid_scope、budget_exhausted、citation_rejected 等状态。
- workflow 固定为 exact scope → FTS 候选 → 代码控制的 bounded reads → 一次结构化 LLM 证据选择与作答 →
  代码 quote/span 验证 → citation 渲染。模型不规划循环，不计算数据库身份。
- 候选读取第一版采用确定性策略，在 FTS 排序和预算内投影最相关节点；不引入 reranker、embedding 或向量库。
- LLM 只看最小必要的 query、scope 标签和已读取 evidence windows。工具内部结果不以逐轮 assistant/tool history
  回灌，从结构上消除当前 prompt 线性增长。

### 双入口与 ReAct 路由

- 模块本身是可直接调用的公共 application service；直接调用目标为一次回答模型调用。
- learning tool registry 暴露一个高层 `answer_from_documents` 工具，参数与返回契约投影自同一模块，不复制流程。
- ReAct system prompt 将普通“根据材料回答/解释/总结并给出处”的请求路由到高层工具；底层原子工具保留给需要
  自主浏览、比较搜索路径或用户明确要求逐步探索的场景。
- 外层 ReAct 路径预计为路由调用、模块内部回答、外层转述三次模型调用；验收上限设为四次，以容纳一次可恢复重试。
- 外层最终回复不得把 node id 或 search excerpt 当 citation。高层工具成功时返回可直接转述的已验证答案与引用；
  失败时返回可直接解释的结构化拒绝。

### Grounding、安全与可观测性

- 复用 Document Structure module 的 current revision、FTS、read budget 与 citation resolver；不建立平行索引。
- read-before-cite 是运行时不变量：模型只能引用本次 workflow 已读取窗口，验证通过前不生成成功 citation 事件。
- 原文和标题继续标记为 untrusted；系统指令与工具 schema 明确禁止执行材料内指令。
- 复用现有搜索、读取、模型和 citation 事件，并增加一个 workflow 级完成/拒绝投影，记录 scope、候选、读取占比、
  调用数、tokens、状态和 citation 数。trace 仍是权威审计来源。
- 不修改 SQLite schema。除非实现中发现现有 trace 无法表达已批准指标，否则不新增迁移。

### 成本与回归门

- 冻结两条现有生产 trace 为问题基线：自然问题首轮 8 model / 10 tool / 82,581 tokens / 0 citation；显式
  grounded 问题 11 model / 11 tool / 132,403 tokens / 2 citations。
- 新自然问答 Replay 必须达到：无需工具名、至少 1 条 exact node citation、模型调用 ≤4、累计 tokens ≤45,000、
  读取字符 ≤目标 revision 的 25%，并保持 exact selected scope。
- 成本以 trace 中真实 usage 累计计算，不用字符数估算替代模型 tokens；读取占比以 current revision 原文长度计算。
- 只有高层 workflow 在真实 Replay 下仍超门，才评估通用工具历史压缩。该压缩不属于默认实施范围。
- tool schema 与 ReAct prompt 改变会更新 Replay 执行指纹；受影响 cassette 必须真实重录或明确废弃。

## Testing Decisions

- 测试只断言公共模块结果、事件、持久化可解析证据和用户可见行为，不绑定私有 helper、具体 SQL 或模型措辞。
- `GroundedDocumentAnswer` 的确定性骨架走 TDD：scope、候选投影、读取预算、无证据、预算耗尽、注入文本、重复 quote、
  越界 span、read-before-cite 与事件顺序均用 fake provider 验证。
- Dict/SQLite 不新增新的问答 store abstraction；搜索、读取和 citation 继续使用既有 SQLite Document Structure
  集成测试作为 prior art，组合模块用真实 SQLite fixture 穿透验证。
- 结构化 LLM 槽用 fake/Replay provider 测契约校验、一次可恢复重试和 fail closed，不对自由文本措辞做脆弱断言。
- learning tool registry 测试高层工具与直接模块调用产生同等 grounding 结果；同时证明底层六个原子工具仍注册可用。
- ReAct 路由用一个新的自然材料问答 eval case，用户消息不包含工具名；规则 grader 断言高层工具调用、selected scope、
  search → read → exact citation 顺序、最终 citation、调用/tokens/读取门。
- 现有 case14 因 tool schema 指纹变化由真实模型重录；新增自然问答 cassette 同样只允许通过授权录制脚本生成。
- 真机验收复用 production learning/trace 联合审计，并扩展 `audit-doc` 或等价只读审计，使成本与 grounding 门可复算。
- 每个竖切跑受影响测试；最终跑 Ruff check、Ruff format check、Pyright、import-linter、全量 pytest 和 Tier-1 eval。

## Proposed Vertical Slices

1. **GAS-S1 自然问答基线与验收契约**（AFK）
   - 冻结失败 Replay/trace 基线，建立不含工具名的自然问题和可执行 grounding/成本断言。
2. **GAS-S2 GroundedDocumentAnswer 有界 workflow**（AFK，blocked by GAS-S1）
   - 交付双入口共享的深模块，完成 selected search、bounded read、单槽回答与代码验证 citation。
3. **GAS-S3 自然 ReAct 路由与成本门**（AFK，blocked by GAS-S2）
   - 注册高层工具并让普通材料问题自然触发；达到 ≤4 model calls、≤45k tokens、≤25% read。
4. **GAS-S4 真实 Replay、生产 dogfood 与收口**（AFK，blocked by GAS-S3）
   - 真实重录受影响 cassette，完成生产联合审计、五门、开发记录和规范化 git 收口。

## Out of Scope

- DS-S5 KnowledgeRelation、知识图谱、多跳关系推理、CanonicalConcept 或 Learning Memory 迁移。
- embedding、向量数据库、cross-encoder/reranker、GraphRAG、外部搜索服务或新的检索 adapter。
- PDF/Office/OCR/多模态解析，以及 DocumentNode parser、revision 或 evidence schema 的重设计。
- unresolved 自然语言资源名自动猜测、隐式全库回退和跨用户权限模型。
- 用高层问答 workflow 替代核心 quiz workflow、Reader ingest 或全部开放 ReAct 原子工具。
- 通用对话历史压缩器；仅在组合 workflow 经真实 trace 仍不能达标时另立 issue。
- 新 Web UI、语音入口或独立 ask CLI 命令。模块直接调用契约为这些入口留出空间，但本 PRD 不交付界面。

## Further Notes

- 本 PRD 是 ADR-0008 的产品化收口，不改变“核心考核是 workflow、自由 ReAct 只用于开放编排”。开放材料问答中，
  值得稳定 eval 的 grounding 子路径也采用确定性 workflow，而外层仍可自由决定何时调用它。
- 成本目标来自现有真实 trace，第一目标是同时修复 0 citation 和调用膨胀。任何只降 token 却丢失 exact citation 的
  方案均不算通过。
- 用户已授权后续真实模型考核、必要 cassette 录制以及通过 trace.db 完成验收；如需发送新的非既有授权材料或写入
  新生产数据，仍按现有审批边界执行。

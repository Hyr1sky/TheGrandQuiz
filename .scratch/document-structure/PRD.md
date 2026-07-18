# PRD：修订化文档树、精确溯源与渐进式 Agentic Search

Status: in-progress（DS-S1–S2 done；DS-S3–S4 replay-complete / ready-for-human dogfood；DS-S5 deferred, eval-gated）
Triage: ready-for-human
Decision: [ADR-0008](../../docs/adr/0008-revisioned-document-tree-and-grounded-knowledge-graph.md)

## Problem Statement

TheGrandQuiz 当前把完整学习材料保存在 LearningResource 中，再由 Reader 通过一次性 token 分块产出扁平的
KnowledgeItem。这个模型已经能完成考核，但用户和 Agent 无法把知识点稳定地放回某个内容修订、章节和原文
位置；Reader、ReAct 与未来 Summarizer 也没有可共享的“大纲 → 章节 → 正文”导航基座。

真实长文重建已证明临时 token 分块能守住 Provider 预算，却不能成为长期文档模型：分块不持久、没有
section hierarchy、不能被搜索、跨片重复依赖人工审批清理，Evidence locator 仍为空。重 ingest 覆盖正文后，
历史 trace 的 quote 也缺少可解析的 revision。

用户需要一个既服务当前长文深读和精确引用、又能自然演进到稀疏检索与知识关系图的基础层，同时不能偏离
“全局 KB、KnowledgeItem 是概念身份、核心考核是 workflow、SQLite + 事件脊柱”的既有架构。

## Solution

把每个获批 LearningResource 内容保存为不可变 ResourceRevision，并确定性解析为可导航的 DocumentNode 树。
KnowledgeItem 继续作为学习和考核身份，但其 Evidence 必须引用 revision、node、section_path 与精确 source span。

Reader ingest 使用自然节点批次确定性覆盖全文，替代任意 token chunk；开放 ReAct 获得查看大纲、稀疏搜索、
展开节点和读取精确正文的有界工具。SQLite adjacency rows、recursive CTE 与 FTS5 承载当前规模，不引入向量库、
图数据库或外部检索运行时。

在这条确定性 grounding 链稳定后，以独立 eval 门控实验让 Reader 提出 KnowledgeItem 间的 prerequisite、
related、contradicts 关系。跨资源 CanonicalConcept 与 Learning Memory 归并继续推迟。

## User Stories

1. 作为学习者，我希望每条知识证据显示材料、内容版本、章节路径和原文位置，以便沿路径回到原文复习。
2. 作为学习者，我希望点击或复制 citation 后能看到包含该引文的完整章节上下文，而不是孤立 quote。
3. 作为学习者，我希望材料更新后仍能解释旧考核 trace 当时引用的是哪个版本。
4. 作为学习者，我希望系统深读长材料时不会因一次请求超过 Provider 预算而失败。
5. 作为学习者，我希望长材料按自然章节处理，减少同一概念因任意 token 切点被重复抽取。
6. 作为学习者，我希望新材料完成审批前不会覆盖当前可用知识和引用。
7. 作为学习者，我希望重 ingest 失败时当前 revision、DocumentNode 和 KnowledgeItem 快照全部保持可用。
8. 作为学习者，我希望可以先看一份材料的大纲，再决定展开哪一章。
9. 作为学习者，我希望用自然语言搜索全局 KB 时，Agent 能先定位相关章节，再读取受限正文。
10. 作为学习者，我希望搜索结果按材料和 section_path 展示，能够区分多个资源中的相似概念。
11. 作为学习者，我希望“只在这份材料里找”严格限制搜索范围，不能静默扩大到全库。
12. 作为学习者，我希望无标题或结构较差的纯文本仍能被完整导航，不因缺少 Markdown 标题丢内容。
13. 作为学习者，我希望代码块、表格和列表保留原始顺序与章节归属，以便题目引用时不失真。
14. 作为维护者，我希望相同 revision 重建总能得到相同节点身份、顺序和 source span，保证 Replay 可断言。
15. 作为维护者，我希望 Dict 与 SQLite store 对 revision、树、搜索和原子切换给出一致结果。
16. 作为维护者，我希望每条 Evidence 在提交前由代码验证 quote 确实位于声明的 revision/node/span 中。
17. 作为维护者，我希望 locator 校验失败时系统 fail closed 并留下结构化错误，而不是写入看似精确的假引用。
18. 作为维护者，我希望搜索 trace 记录 query、scope、候选节点、选中节点、预算和最终 citations，便于复盘。
19. 作为维护者，我希望现有生产资源和 KnowledgeItem 身份在迁移后保留，不因增加树结构再次清库。
20. 作为维护者，我希望旧 evidence 能唯一匹配原文时自动回填 locator，不能匹配时明确进入审计。
21. 作为 Agent Runtime 评测者，我希望比较临时 token 分块与节点批次在覆盖率、重复率、成本和 grounding 上的差异。
22. 作为 Agent Runtime 评测者，我希望证明 Agentic Search 不读取全文也能找到目标证据，并能拒绝无证据回答。
23. 作为未来功能开发者，我希望稀疏检索行为有稳定模块接口，以后可在真实需要时增加 reranker 或第二个 adapter。
24. 作为未来功能开发者，我希望 KnowledgeItem 关系是类型化、带置信度和 provenance 的行，而不是不可查询的 metadata JSON。
25. 作为未来功能开发者，我希望跨资源概念归并不会覆盖 source-grounded KnowledgeItem，也不会静默迁移学习状态。
26. 作为项目作者，我希望这套能力强化“可观测、可恢复、可评测的学习 Agent Runtime”叙事，而不是变成另一个重型 RAG 壳。

## Implementation Decisions

### 领域身份与生命周期

- LearningResource 保持 locator-addressed 身份，只指向当前获批 ResourceRevision。
- ResourceRevision 是不可变获批内容版本，身份由 resource_id 与 content_hash 确定性派生；旧 revision 默认保留，
  但不参与当前搜索、Reader ingest 或考核候选池。
- DocumentNode 绑定单一 revision；node_id 由 revision 与确定性结构/内容指纹生成。section_path 可读、可重复、
  可随修订变化，不作为身份。
- KnowledgeItem 继续是资源内概念身份和 Learning Memory 锚点。DocumentNode 不替代 KnowledgeItem；一个 item
  可引用多个 node，一个 node 可支持多个 item。
- 当前 revision 的切换与 resource、tree、items、evidence 快照提交共享既有 transaction seam；审批前和失败
  路径都不能改变当前快照。

### Document Structure module

- 建立一个深模块，集中负责确定性解析、节点身份、树查询、source span、FTS 索引、预算内读取与 citation 解析。
  Reader、ReAct、Summarizer 和 eval 只依赖其行为，不各自理解 SQLite、Markdown 切分或 locator 格式。
- 第一版解析 Markdown 与纯文本。标题建立 section hierarchy；段落、列表、表格和代码块保持顺序与 source span。
- 文档必须有 synthetic root。无标题材料和超过节点读取预算的 section 按完整段落生成 synthetic children；正文
  不丢失、不重叠，空白规范化不改变 source offsets。
- 节点摘要是可选 LLM 派生物，不参与结构身份。没有摘要时，标题、section_path 与有界正文仍可被 FTS 和 Reader 使用。
- 当前只有 SQLite 实现，不建立检索 adapter seam；先稳定模块行为，出现第二个真实后端时再抽 adapter。

### Schema 与迁移

- 顺序 SQLite migration 增加 resource revisions、document nodes、节点 FTS 与规范化 evidence 关联；继续显式开启
  foreign keys，不引入 Alembic。
- resources 保存 current_revision_id；revision 保存不可变 raw content 与 content_hash；document nodes 使用
  adjacency rows，并缓存 depth、ordinal 和可读 section_path。
- FTS5 索引当前 revision 节点的 title、section_path、summary 与正文投影。排序必须有确定性 tie-break，不把
  SQLite 未指定行序暴露给 Agent 或 Replay。
- 现有获批资源迁移为初始 revision，保留 resource_id、item_id 与所有学习状态。schema migration 不调用 LLM；
  结构回填可重入、事务化，失败不影响既有考核。
- 旧 quote 在 raw content 中唯一匹配时确定性回填 node/span；多处匹配或未匹配时保留 quote、标记 unresolved，
  进入审计报告。迁移不得凭相似度猜测 locator。

### Evidence 与 citation

- Evidence 的持久模型包含 revision_id、node_id、section_path、start/end offset、可选 page/block、quote 与 quote hash。
- 提交前验证 node 属于 revision、span 落在 node/raw content 内、quote 规范化后与 span 一致；任何失败都阻止新快照提交。
- Reader 结构化输出优先返回 node-local offsets，代码转换为 revision-global offsets；模型不生成 node_id 以外的
  任意数据库身份。
- citation renderer 输出稳定资源标签、section_path、位置和 quote；历史 citation 显式解析指定 revision，不默默
  跳到 current revision。
- KnowledgeItem fingerprint 仍由概念名与稳定 quote 集合决定；locator 和 section_path 更新不单独改变 item_id。

### Reader 节点化深读

- ingest 先确定性建树，再按自然 leaf/section 节点组成有界批次；Provider 的完整请求预算门保持 fail closed。
- ingest 是覆盖型 traversal：代码枚举全部可考节点，Reader 不得因 LLM 自由选择而静默跳过章节。每批沿用结构化
  校验、ModelRetry、model span 与取消语义，代码聚合 KnowledgeItem 和 topic。
- 节点批次替代当前任意 token chunk；超大单节点由 parser 的 synthetic children 解决，不在 Reader 内发明第二套分块。
- Reader 输出必须引用提供给它的 node key 与 node-local evidence；跨节点 item 可返回多条 evidence。
- 第一阶段仍在一次 ingest 中形成完整候选 KnowledgeItem 快照并经过现有审批门；延迟生成部分知识、后台队列和
  跨进程 checkpoint 不在本 PRD 内伪装为已交付。

### Agentic Search 与 ReAct

- 提供查看资源大纲、稀疏搜索节点、展开子节点、读取有界正文和解析 citation 的受控学习工具。
- 搜索默认只查 current revision；支持全库与 exact resource scope，显式 unresolved scope 必须 fail closed。
- 第一阶段用 FTS5/BM25；结果包含 resource、revision、node、section_path、match excerpt 与 score，并以稳定字段
  打破同分。
- LLM 决定开放问题中“读哪一节”，代码控制候选数量、展开深度、总字符/token、允许的 resource scope 与不可信
  输入标记。核心 quiz workflow 不调用自由 Agentic Search 替代确定性选题。
- Agent 只有在读取到可验证 source span 后才能生成 grounded citation；未找到证据时必须诚实返回无证据结果。

### KnowledgeRelation 实验

- 关系存为普通 SQLite 行，端点限定为现有 KnowledgeItem，类型首批限定 prerequisite、related、contradicts。
- 每条边保存 confidence、evidence node/item、extraction method、prompt/model version、trace id、review status。
- section hierarchy 不自动生成知识语义边；metadata 只保存候选 aliases、tags、difficulty hint 和抽取版本等软标注。
- 关系实验独立于树/搜索交付。以“前置知识感知选题或多跳查询”对当前基线的 eval 增益决定保留、修改或删除。
- 不创建 CanonicalConcept，不跨资源 same-as 自动归并，不迁移 Learning Memory。

### 事件、恢复与安全

- parser 开始/结束、revision staged/committed、节点搜索、节点选择/读取、citation 校验、关系抽取与拒绝原因通过
  领域事件进入现有事件脊柱；kernel 保持不知道具体事件类型。
- trace 可还原每次搜索和 Reader 批次的 parent span、revision/node ids、预算、token、错误和最终 citations。
- 网页/文件正文与节点内容继续视为不可信输入；结构化解析和 section 标题不能提升其指令优先级。
- revision/tree 写入失败回滚；FTS 与主表必须同事务一致，不能出现树已切换但搜索仍命中旧索引。

## Testing Decisions

- 测试只通过模块行为与持久化结果验证，不断言私有 helper 或具体 SQL 字符串；Document Structure module 的接口
  同时是生产调用面和主要测试面。
- 确定性 parser 使用 golden Markdown/纯文本夹具，覆盖嵌套标题、重复标题、无标题、超大 section、表格、列表、
  代码块、Unicode 与空文档；断言全文覆盖、不重叠、稳定 node_id、稳定顺序和 source span。
- Dict/SQLite store parity 覆盖 revision 提交、current 切换、旧 revision 可解析、树遍历、证据校验与回滚；
  FTS5 是 SQLite Document Structure module 的内部实现，只通过同一搜索行为做确定性集成测试，不为测试复制 BM25。
- 原子性测试注入 resource/revision/node/FTS/item/evidence 各阶段失败，断言旧当前快照和搜索结果完全不变、成功事件
  不提前发射。
- 迁移测试从当前 schema 和真实形状夹具起步，证明 IDs 与学习状态保留、初始 revision 可复算、回填可重入；
  ambiguous/unmatched quote 进入 unresolved 审计而不是猜测。
- Reader 用 fake provider 做 TDD 式节点批次覆盖、预算、重试、取消与聚合测试；真实模型使用 Record/Replay cassette
  验证 node-local locator 和跨节点 evidence，不能手工伪造 cassette。
- Agentic Search eval 至少包含：大纲导航、精确词稀疏命中、同名章节稳定排序、selected scope、unresolved scope、
  预算耗尽、无证据拒答和“无需读取全文找到目标证据”。
- citation property tests 验证任意获批 evidence 都能解析到对应 revision/node/span，quote hash 与正文一致。
- KnowledgeRelation 先用固定 item 图验证类型、置信门、循环/重复处理和 deterministic query；真实 LLM 边只进入
  Replay + 对照 eval，不把模型措辞写成脆弱 unit assertion。
- 每个 slice 跑 Ruff、format check、Pyright、import-linter 和全量 pytest；触及 prompt/tool schema 的 slice 明确
  列出需要重录或废弃的 cassette。

## Proposed Vertical Slices

1. **DS-S1 ResourceRevision + DocumentNode 当前快照**（AFK，done 2026-07-17）
   - 确定性 parser、revision/tree schema、迁移回填、原子 current 切换和 store parity。
2. **DS-S2 精确 Evidence 与可解析 citation**（done 2026-07-17）
   - evidence 正规化、locator 校验、旧 quote 回填审计、Reader node-local 输出和引用展示。
3. **DS-S3 Reader 节点化覆盖型深读**（replay-complete 2026-07-17，真实筛选/citation dogfood 待执行）
   - 用自然节点批次替换临时 token chunk，保持预算、重试、审批、快照原子性和真实 cassette。
4. **DS-S4 FTS5 + 渐进式 Agentic Search**（replay-complete 2026-07-17，开放搜索 dogfood 待执行）
   - 大纲、搜索、展开、读取工具，严格 scope、预算、trace 与检索 eval。
5. **DS-S5 KnowledgeRelation eval 门控实验**（HITL，暂缓）
   - 类型化语义边、provenance、前置知识/多跳对照 eval；由证据决定保留，不推进全局概念归并。

## Implementation checkpoint（2026-07-17）

- DS-S1–S4 的本地代码、迁移、确定性测试和 Agentic Search capstone 已实现；静态四门全绿。
- 生产 `learning.db` 已在 SHA256 可核对备份后从 schema v9 迁移到 v11。3 resources / 88 items /
  3 revisions / 1551 nodes、Learning Memory、Asked Questions 与 Difficulty 均无差异；135 条旧 evidence
  确定性回填为 83 resolved / 52 unresolved，FTS 有 1551 current-only rows，完整性检查通过。
- Reader 与 ReAct case14 已经 `.env` 真实模型重录，禁止手工改写的 Replay 执行指纹现已更新。真录暴露模型能
  稳定返回 node/start/quote、但不能可靠计算 `end_offset`；代码仅在 quote 从声明起点逐字匹配时，以 Python
  字符长度确定性规范化右边界，不做模糊搜索或位置迁移。静态四门全绿，全量 pytest 为 `768 passed`。
- Reader 同一材料旧/新基线已冻结：105/105 个可考节点 exactly-once 覆盖，12 个候选、0 重复，单次真实请求
  8715 prompt tokens，低于 32k Provider 门。case14 真录只调用一次 `start_quiz(count=3, question_type=选择题)`。
- 新增只读 `grandquiz audit-doc`：交叉核对 learning/trace 两库，只有真实 `human_cli` 审批、Reader batch
  exactly-once、预算/usage、current exact evidence，以及 selected search → bounded read → node citation 全部成立才通过。
- DS-S5 暂不建表、不抽关系、不接生产消费路径。先完成至少一次真实筛选/citation 与开放搜索 dogfood；再预注册基线、
  数据集、相关性/grounding/token/latency 指标，交由 HITL 决定是否启动可删除实验。

## Out of Scope

- PDF、Office、图片 OCR、表格视觉理解、page bbox parser adapter 和 MinerU/VLM 集成。
- 向量数据库、embedding 默认检索、cross-encoder、图数据库、GraphRAG 社区检测与 Knowhere 运行栈。
- 全局 CanonicalConcept、跨资源 same-as 自动归并、Learning Memory reconciliation 或改锚点。
- 延迟/按需生成部分 KnowledgeItem、后台 worker、跨进程 Reader checkpoint 与持久化任务队列。
- Web 搜索供应商、浏览器 fallback、MCP 动态挂载；这些继续属于 Web Acquisition 范围。
- 以 document hierarchy 自动生成 prerequisite/broader/narrower，或让语义图替代当前考核候选集。
- 为假想第二检索后端预建 adapter seam。

## Further Notes

- 本 PRD 是稳定性加固后第一项知识基座深化，不是对核心考核循环的改写。quiz 的选题、判卷、Learning Memory
  与 Difficulty 仍遵守“LLM 判卷，代码记账”。
- DS-S1–S4 构成必须交付的结构与搜索基座；DS-S5 是可删除实验。即使语义边无收益，revision、DocumentNode、
  exact evidence 与 FTS 仍独立成立。
- 现有生产库包含真实长文与 88 个 KnowledgeItem。迁移目标是保留它们并确定性回填，不再次以“早期数据不重要”
  为由清库；任何真实 DB 操作前仍须备份、quick_check、foreign_key_check 和可恢复性验证。
- 实施过程中若发现不可逆身份或 Learning Memory 语义需要改变，必须新增/修订 ADR，不能在 issue 内暗自决定。

# 修订化文档树与精确检索基座开发记录

> 记录日期：2026-07-17
> 范围：ADR-0008 / `.scratch/document-structure/` 的 DS-S1–S4；DS-S5 仅做门控决策。
> 当前边界：代码、生产 schema v11 迁移与两份真实模型 cassette 已完成，五门全绿；生产筛选/citation 与开放搜索 dogfood 待用户在独立终端执行。

## 1. 为什么做这轮改造

原系统已经能把材料深读成 KnowledgeItem 并进入考核循环，但材料仍主要表现为 `raw_content` 加临时 token
分块。它能守住 Provider 请求上限，却无法稳定回答“这个知识点来自哪个内容版本、哪一节、哪段原文”，也不能
让 Reader、ReAct 和未来 Summarizer 共享一条渐进式导航路径。

本轮把 source-of-truth 加深为四层，同时保持既有领域身份不变：

```text
LearningResource（稳定 locator）
  → ResourceRevision（不可变内容版本）
    → DocumentNode（确定性原文结构树）
      → Evidence（revision/node/精确 span）
        → KnowledgeItem（学习与考核身份）
```

DocumentNode 只表达作者组织原文的结构；KnowledgeItem 仍是考核与 Learning Memory 的锚点。开放 ReAct 可以
渐进搜索文档，但核心 quiz 仍是确定性 workflow，没有被改成自由 RAG/ReAct。

## 2. DS-S1：不可变修订与确定性文档树

- 新增 `ResourceRevision`、`DocumentNode` 与 `LearningResource.current_revision_id`。
- Markdown/纯文本由代码确定性解析为 synthetic root、section、paragraph、list、table、code 等节点；节点保存
  ordinal、depth、section_path 与原文 source span，同一内容可复算相同身份。
- schema v9 把现有 resource 原文回填为初始 revision/tree；旧 revision 保留给历史 trace/citation，默认搜索和
  考核只使用 current revision。
- revision、tree、resource current 指针与 KnowledgeItem snapshot 共享事务；失败不会暴露半个新版本。
- parser 与 commit 生命周期继续发领域事件，经 `AgentEvent` 脊柱进入 trace。

对应提交：`d558de2 feat: add revisioned document structure`、`d07e15f docs: close document tree foundation`。

## 3. DS-S2：精确 Evidence 与可解析 citation

- `EvidenceLocator` 保存 revision_id、node_id、section_path、全局 start/end、quote hash，以及可选 page/block。
- schema v10 用带外键的 `knowledge_item_evidence` 普通行保存一对多 evidence；对外仍按 ordinal 稳定还原列表。
- 新 snapshot 在提交前逐条验证 node/revision 归属、node/raw content 边界、逐字 quote 与 hash；任一失败整体
  fail closed。错误事件只记录分类和公开 fingerprint，不泄漏本地路径或原文。
- citation renderer 输出资源、明确 revision、section_path、位置与 quote；resolver 读取 locator 声明的历史
  revision，并返回有界上下文，不会静默跳转 current。
- v9→v10 迁移不调用 LLM：quote 在当前正文中唯一出现才回填；重复或缺失保留为 unresolved 审计。旧 unresolved
  item 仍可考，新 Reader 产出的 unresolved 则禁止入库。

## 4. DS-S3：Reader 改为自然节点覆盖型深读

- Reader production path 接收 `DocumentSnapshot`；代码枚举所有可考自然正文节点，纯导航节点跳过，每个基础
  source span 恰好进入一次批次。
- 批次预算同时计算 prompt、node key/path、正文与结构化输出 reserve；Provider 完整请求硬门仍 fail closed。
  旧 `_split_content` 等任意 token chunker 已删除，超大节点只由 Document Structure parser 生成 synthetic child。
- 模型只返回本批 node key、node-local start/end 和 quote；代码解析 node 身份并转换为全局 locator。未知 node、
  越界、改写 quote 会走有界 ModelRetry，耗尽后抛结构化 `ReaderEvidenceError`。
- 每个自然节点批次是 `learning.reader_batch.started/ended` span，model span 作为子 span；审批和 revision commit
  仍在同一 ingest workflow 中。候选证据验证发生在 HITL 之前。
- 保留兼容入口 `Reader.read(resource, content)`，但它只确定性建树后委托同一节点路径，不再维护第二套切分逻辑。

## 5. DS-S4：FTS5 与渐进式 Agentic Search

- schema v11 增加 `document_nodes_fts`。Store 只索引 current revisions；revision 切换、tree/items/evidence 与 FTS
  更新在同一事务。打开 v10 库可确定性重建索引，FTS 写失败会使整个新 snapshot 回滚。
- 第一版检索使用 FTS5/BM25，加 resource/node 稳定 tie-break。CJK 用 unicode61 与确定性一/二元字符投影支持
  稀疏命中，不引入外部 tokenizer、embedding、向量库或第二个 adapter seam。
- `DocumentSearch` 深模块统一提供 outline、search、expand、read 与 cite；搜索默认 current-only，selected scope
  必须解析 exact resource ids，点名失败零读取、不得退回全库。
- ReAct 注册六个受控工具：资源大纲、节点搜索、节点展开、节点读取、item citation、node citation。正文继续标记
  untrusted；每节点和每 trace 的累计读取预算由代码强制。
- node citation 实施 read-before-cite：只有本 turn 实际读取过且完全包含目标 span 的正文才能引用。无匹配、未读
  或预算耗尽返回结构化错误，LLM 不能凭搜索摘要伪造 grounded citation。
- 搜索/拒绝/展开/读取/citation 继续发领域事件。capstone 在合成长文中读取少于全文 10% 即找到目标证据并返回
  可解析 revision/section/span。

## 6. 生产数据库迁移与审计

迁移前备份：

`~/.grandquiz/learning.db.backup-20260717-pre-exact-search`

备份与迁移前原库 SHA256 一致：

`b93551f67ae16b1066f3bcad345ac031cf488148abda4aeaf4659e4de110c7e3`

迁移结果：

| 检查项 | 结果 |
|---|---:|
| schema | v11 |
| SQLite quick_check | ok |
| foreign_key_check | 0 rows |
| resources / items | 3 / 88 |
| revisions / nodes | 3 / 1551 |
| evidence | 135（83 resolved / 52 unresolved） |
| current-only FTS rows | 1551 |
| FTS orphan / historical-current violation | 0 / 0 |

对备份做双向差分后，resource、item 身份字段、Learning Memory、Asked Questions 与 Difficulty 均为 0 差异。
迁移全程未调用 LLM。

## 7. 测试与真实回放收口

确定性覆盖新增了：parser/store migration、历史 citation、quote/hash 篡改、重复/缺失/跨节点 evidence、Reader
节点 exactly-once 与预算、未知 node/越界/改写 quote 重试、current-only FTS、严格 scope、稳定排序、事务回滚、
渐进读取预算、read-before-cite、CJK 搜索及 Agentic Search capstone。

当前结果：

- Ruff check：绿
- Ruff format check：绿
- Pyright strict：绿
- import-linter：绿，`kernel` 仍不 import `domain`
- pytest：`768 passed`

两份受影响 cassette 均由 `.env` 配置的真实模型重录，没有手工改写 request key 或输出：

1. Reader 同一材料的 105 个可考 DocumentNode 全部 exactly-once 进入一个预算内批次；产出 12 个候选、12 条
   精确 evidence、0 重复，实际为 8715 prompt / 9417 completion tokens，低于 32k Provider 门。旧临时 chunk
   基线为 16 候选、0 重复，因此正文覆盖与重复率不退化。
2. 真录首次暴露 `deepseek-v4-flash` 能稳定返回正确 node/start/quote，但 70 条 evidence 中只有 7 条右边界算术正确。
   Reader 现仅在 quote 从声明 start 逐字匹配时，用 Python Unicode 字符长度规范化 end；不搜索其他位置。错误
   node、越界 start、改写 quote 仍有界重试并 fail closed，新增回归测试锁定该真实模式。
3. ReAct case14 真录只调用一次 `start_quiz`，参数为 `scope=all`、`count=3`、`question_type=选择题`；五次模型
   调用总计 9866 prompt / 645 completion tokens。原四个 Replay/eval/report 红灯全部转绿。

Reader 纯回放额外断言 batch node ids 与获批 revision 的全部可考节点完全相等、无重复，且 model span 数与
cassette 调用数一致。最终 Ruff check、Ruff format check、Pyright strict、import-linter 和 `768` 项 pytest 全绿。

## 8. DS-S5 决策与后续 HITL

KnowledgeRelation 暂缓，不建表、不抽边、不接生产选题/查询。原因不是实现困难，而是当前真实 Reader/ReAct
回放与搜索 dogfood 尚未完成；此时引入语义图变量，无法可信区分增益来自 grounding/search 还是关系边。

继续步骤：

1. 在单独终端 dogfood 一次真实长文 ingest/筛选/citation，以及一次开放搜索；需要复盘时直接读取 `trace.db`。
2. 只有上述基线稳定后，预注册 DS-S5 数据集、无关系基线、相关性/grounding/token/latency 指标和最低收益阈值，
   再由 HITL 决定“保留 / 修改 / 删除”实验能力。

这轮没有改变 KnowledgeItem 或 Learning Memory 身份语义，也没有引入 CanonicalConcept、same-as 自动归并、
向量库或图数据库。

## 9. 完成性反证审计补强

提交后的逐条 PRD 审计没有把“已有测试通过”直接当成完成证据，而是补出了以下边界：

- Grounding 接受模型等价的 Unicode 空白序列，但最终 Evidence 始终保存 revision 中的逐字 quote、source span 与
  hash；重叠出现的 quote 仍判 ambiguous，不会因普通正则非重叠扫描而误认唯一。
- 确定性生成测试覆盖 CJK、emoji、组合字符、阿拉伯文，以及自然正文节点的首尾可见边界。
- 一个 Reader KnowledgeItem 可按模型给定顺序保留两个自然节点的 evidence，并分别解析全局 locator。
- 故障注入到最后的 evidence INSERT 阶段，证明已暂存的新 revision/tree/item/FTS 会整体回滚到旧快照。
- FTS 对重复标题、同分正文使用稳定 node_id tie-break；只有标点/emoji、没有可检索词的 query 在 domain 边界拒绝。
- outline/search/expand 的标题、路径和 excerpt 与正文一样显式标记 untrusted；成功和耗尽的读取事件都记录
  `budget_used/budget_limit`，拒绝事件另记 requested 数量。
- ReAct 的 read-before-cite、quote mismatch 与 unresolved item citation 都发结构化 `citation_rejected`；事件不保存
  原 quote，只在 node quote 拒绝时保存 SHA256 fingerprint。

这些补强与真实 cassette 已完成收口，没有扩大 DS-S5 范围；剩余工作是产品 dogfood，不再是自动测试红灯。

## 10. Dogfood 证据自动审计

完成性审计发现原 `approval.decided` 只记录结果，无法区分真实 CLI 人工筛选与 `ScriptedApprovalGate`；仅凭旧 trace
不能证明 HITL。事件现增加 `decision_source=human_cli|scripted`，保持事件信封与 kernel 领域无关，auditor 明确拒绝
scripted 审批。

新增 `grandquiz audit-doc` 只读命令，以 ingest/search 两个 trace id 交叉核对 `trace.db` 与 `learning.db`：

- DS-S3：必需事件和提交顺序、Reader batch span 配对、可考节点 exactly-once、估算/真实 token 门、human CLI
  审批计数、current revision/item 数与全部 exact evidence。
- DS-S4：selected exact scope、search → successful bounded read → `source=node_read` citation 顺序、read 覆盖 citation
  span、budget used/limit、current revision/node 解析和累计读取比例（默认不超过全文 25%）。
- 报告为逐项 JSON，任一 check 失败即整体失败并返回非零退出码；命令以 SQLite read-only URI 开库，不迁移、不写数据。

集成测试通过正式 `run_ingest`、`CliApprovalGate`、SQLite Store、Document Search tools 与 TraceStore 生成生产形状
证据；反证覆盖 scripted approval、用 item citation 冒充 Agentic Search、读取比例超门。真实 dogfood 尚未发生，
因此该工具强化的是“如何证明完成”，没有把合成 trace 冒充产品验收。

对生产库现有旧 ingest/search trace 实跑得到 `passed=false`，明确列出缺少 document/batch/human approval/
node-read citation 等证据；命令前后 `learning.db` 与 `trace.db` 的 mtime/size 完全不变，验证只读边界。

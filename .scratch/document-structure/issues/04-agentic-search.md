# DS-S4 — FTS5 + 渐进式 Agentic Search

Status: ready-for-human（真实 ReAct cassette 已完成；开放搜索 dogfood 待执行）
Type: AFK

## Parent

[PRD：修订化文档树、精确溯源与渐进式 Agentic Search](../PRD.md)

## What to build

在 current ResourceRevision 的 DocumentNode 树上交付第一条查询竖切：用户可以让 ReAct 查看资源大纲、以
FTS5 稀疏搜索候选节点、展开子树、读取预算内正文，并返回 DS-S2 可解析的 citations。搜索支持全局 KB 和
exact resource scope；点名无法解析时 fail closed，不得退回全库。

LLM 只决定开放问题中“下一步看哪一节”，Document Structure module 负责确定性排序、scope、预算、深度、
不可信内容标签和引用校验。核心 quiz workflow 与 KnowledgeItem 选题不改为自由检索。

覆盖 PRD User Stories：2、8–11、18、22、23、26。

## Acceptance criteria

- [x] FTS5 索引 current revision 节点的 title、section_path、summary 与正文投影，revision 切换和索引更新同事务
- [x] 搜索排序以 BM25/明确权重为主，并用稳定 resource/node 字段打破同分；SQLite 集成夹具重跑结果稳定
- [x] 默认搜索不命中历史 revision；显式历史 citation 解析与当前搜索语义分开
- [x] 提供查看大纲、搜索节点、展开节点、读取有界正文和解析 citation 的受控学习工具
- [x] 工具返回 resource/revision/node/section_path、match excerpt、score 与可继续展开的最小信息，不一次倾倒全文
- [x] all scope 搜索全库 current revisions；selected scope 只命中 exact ids；unresolved scope 在搜索前拒绝且零读取
- [x] 代码限制候选数、展开深度、每节点字符/token 与一次 turn 累计读取预算，工具循环增长仍受 Provider 总预算门
- [x] 网页/文件节点内容保持 untrusted 标记，标题或正文中的 prompt injection 不能改变 system/tool 约束
- [x] Agent 只有读取并校验 source span 后才能返回 grounded citation；无匹配或预算耗尽时给出结构化、诚实结果
- [x] 搜索/展开/读取事件进入事件脊柱，trace 记录 query、scope、候选/选中节点、预算、latency 与 citations
- [x] 规则 eval 覆盖大纲导航、精确词、同名章节、跨资源同概念、selected/unresolved scope、无证据拒答和预算耗尽
- [x] capstone eval 证明 Agent 不读取全文也能通过“大纲/搜索 → 章节 → 正文”找到目标引文并返回可解析路径
- [x] quiz 既有 scope、选题、判卷、Learning Memory 与 Difficulty 行为无回归；kernel 仍不 import domain
- [x] tool schema 进入 Replay 执行指纹，所有受影响 ReAct cassette 由真实模型重录或明确废弃
- [x] 五门、全量 pytest、全部 Tier-1 eval 与 Agentic Search capstone 全绿
- [ ] 至少一次对生产 current revision 的开放搜索 dogfood 完成渐进读取并返回可解析 citation，真实 trace 证明未倾倒全文

## Completion evidence（2026-07-17）

- `0011_document_node_fts.sql` 与 Store 将 current revision 的索引切换纳入同一事务；v10 打开时可确定性重建，
  FTS 写失败会回滚 revision/tree。中文用 unicode61 + 确定性一/二元投影，不增加外部 tokenizer。
- Document Structure 深模块与六个 ReAct 工具提供 outline/search/expand/read/item citation/node citation；selected
  scope 严格解析，跨工具按 trace 累计读取预算，node citation 强制 read-before-cite。
- capstone 在长文中读取少于 10% 正文找到精确 quote；生产 FTS 1551 rows，全部指向 current revision。
- 完成性审计补充同名章节稳定 tie-break，并让 outline/search/expand 的标题、路径、excerpt 显式携带 untrusted
  标记；成功/拒绝读取事件记录累计预算，node/item citation 拒绝记录结构化分类与安全 fingerprint。
- case14 已用真实模型重录；模型只调用一次 `start_quiz`，参数为 all scope、3 道选择题。目标回放、全部 Tier-1
  eval、HTML report、静态四门与全量 pytest `764 passed`。

## Dogfood evidence protocol

在 DS-S3 dogfood 已提交的 current revision 上启动真实 `grandquiz react`，让 Agent 回答一个必须查材料原文的问题，
并明确要求给出可回溯 citation。完成后从 `trace.db` 核对并把结果写回本 issue：

- trace 至少包含 `learning.document_outline_viewed` 或 `learning.document_nodes_searched`，随后包含
  `learning.document_node_read` 和 `learning.citation_resolved`；citation 事件必须是 `source=node_read`，且此前已有
  覆盖该 span 的成功 read，不能用既有 KnowledgeItem citation 代替 Agentic Search 证据。
- 搜索 scope 与用户请求一致；若指定材料，resource id 必须 exact match，不能出现 unresolved scope 后退回全库。
- 读取事件的 `budget_used <= budget_limit`，读取字符数显著小于 revision 全文；不得把搜索 excerpt 当已读 citation。
- 最终 citation 的 revision/node/span/quote 可在 `learning.db` 逐字解析，且指向当时的 current revision。

终端入口：

```bash
.venv/bin/dotenv run -- .venv/bin/grandquiz react \
  --db ~/.grandquiz/learning.db --materials-dir /path/to/materials
```

## Blocked by

- [DS-S2](02-exact-evidence-citations.md)
- DS-S3 可并行开发，但合并前必须用至少一个节点化 Reader 真实 revision 完成 capstone

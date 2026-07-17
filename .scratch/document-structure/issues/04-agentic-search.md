# DS-S4 — FTS5 + 渐进式 Agentic Search

Status: ready-for-agent
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

- [ ] FTS5 索引 current revision 节点的 title、section_path、summary 与正文投影，revision 切换和索引更新同事务
- [ ] 搜索排序以 BM25/明确权重为主，并用稳定 resource/node 字段打破同分；SQLite 集成夹具重跑结果稳定
- [ ] 默认搜索不命中历史 revision；显式历史 citation 解析与当前搜索语义分开
- [ ] 提供查看大纲、搜索节点、展开节点、读取有界正文和解析 citation 的受控学习工具
- [ ] 工具返回 resource/revision/node/section_path、match excerpt、score 与可继续展开的最小信息，不一次倾倒全文
- [ ] all scope 搜索全库 current revisions；selected scope 只命中 exact ids；unresolved scope 在搜索前拒绝且零读取
- [ ] 代码限制候选数、展开深度、每节点字符/token 与一次 turn 累计读取预算，工具循环增长仍受 Provider 总预算门
- [ ] 网页/文件节点内容保持 untrusted 标记，标题或正文中的 prompt injection 不能改变 system/tool 约束
- [ ] Agent 只有读取并校验 source span 后才能返回 grounded citation；无匹配或预算耗尽时给出结构化、诚实结果
- [ ] 搜索/展开/读取事件进入事件脊柱，trace 记录 query、scope、候选/选中节点、预算、latency 与 citations
- [ ] 规则 eval 覆盖大纲导航、精确词、同名章节、跨资源同概念、selected/unresolved scope、无证据拒答和预算耗尽
- [ ] capstone eval 证明 Agent 不读取全文也能通过“大纲/搜索 → 章节 → 正文”找到目标引文并返回可解析路径
- [ ] quiz 既有 scope、选题、判卷、Learning Memory 与 Difficulty 行为无回归；kernel 仍不 import domain
- [ ] tool schema 进入 Replay 执行指纹，所有受影响 ReAct cassette 由真实模型重录或明确废弃
- [ ] 五门、全量 pytest、全部 Tier-1 eval 与 Agentic Search capstone 全绿

## Blocked by

- [DS-S2](02-exact-evidence-citations.md)
- DS-S3 可并行开发，但合并前必须用至少一个节点化 Reader 真实 revision 完成 capstone

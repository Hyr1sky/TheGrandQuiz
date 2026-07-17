# DS-S5 — KnowledgeRelation eval 门控实验

Status: deferred（eval-gated；等待 DS-S1–S4 真实回放与 dogfood）
Type: HITL

## Parent

[PRD：修订化文档树、精确溯源与渐进式 Agentic Search](../PRD.md)

## What to build

在结构树、精确 evidence 与 Agentic Search 已稳定后，做一个可删除的 KnowledgeRelation 实验：Reader 只能在
已获批 KnowledgeItem 集合内提出 prerequisite、related、contradicts 类型边，每条边带 confidence、证据节点、
抽取/Prompt 版本、trace 和 review status。用真实材料比较“前置知识感知选题或多跳查询”与当前基线；结果决定
关系能力保留、修改或删除。

本 slice 不创建 CanonicalConcept、不做跨资源 same-as 自动归并、不改变 Learning Memory 锚点，也不把文档
section 父子关系当作知识关系。HITL 只用于审核真实边样本和接受 eval 结论，代码与自动测试仍应可由 agent 完成。

覆盖 PRD User Stories：18、21、24–26。

## Gate decision（2026-07-17）

暂不启动实现，也不预建 relation schema。DS-S3 Reader 与 DS-S4 ReAct 的真实 cassette 尚待重录，生产
Agentic Search 也尚未 dogfood；此时添加关系抽取会把 grounding 基线与语义图变量混在一起，无法给出可信的
增益结论。启动本实验前必须先锁定：真实材料样本、无关系基线、相关性/grounding/token/latency 指标、最低收益
阈值和 HITL 抽样方法。未达到门槛时默认删除实验路径，KnowledgeItem 与 Learning Memory 身份语义不变。

## Acceptance criteria

- [ ] 普通 SQLite 关系行端点只允许现有 KnowledgeItem，类型限定 prerequisite、related、contradicts
- [ ] 每条边保存 confidence、支持 evidence/node、extraction method、model/prompt version、trace id 与 review status
- [ ] 重复边、反向边、自环、端点删除与 revision 更新有确定性规则，并在 Dict/SQLite adapter 中 parity
- [ ] DocumentNode contains/parent 关系不会自动写成 KnowledgeRelation；无 LLM 调用的结构 ingest 产生零语义边
- [ ] metadata 只保存 aliases/tags/difficulty hint/candidate concept key 等软标注，权威关系不藏 JSON
- [ ] Reader 只可引用本次提供的已获批 item ids 与 evidence nodes；未知端点、无证据边、非法 relation 触发重试/拒绝
- [ ] 低置信或未审核边不进入生产选题/查询；confidence threshold 与 review status 的后果由代码确定
- [ ] 关系抽取、接受/拒绝和消费全部进入 trace，可按 prompt/model/trace 精确废弃一批派生边
- [ ] 固定小图测试覆盖 prerequisite traversal、循环、重复、contradicts、删除与排序，不依赖 LLM 措辞
- [ ] 真实模型 cassette 至少覆盖一组高置信 prerequisite、一组拒绝边和一组证据不足输出，不能手工伪造
- [ ] 对照 eval 预先锁定基线、数据集和指标，至少比较相关性、grounding、额外 token/latency 与薄弱概念解决路径
- [ ] HITL 审核抽样真实边，记录 precision、主要错误类型和是否接受产品化；结论写回 PRD/issue
- [ ] 若未达到预设收益，默认路径保持关闭并删除未证明必要的消费代码；不得因 schema 已存在宣称图能力成功
- [ ] 不新增 CanonicalConcept、same-as 自动归并、Learning Memory reconciliation、向量库或图数据库
- [ ] 五门、全量 pytest、既有 eval 与关系实验 eval 全绿；最终报告明确“保留 / 修改 / 删除”决策及证据

## Blocked by

- [DS-S3](03-node-aware-reader.md)
- [DS-S4](04-agentic-search.md)

# DS-S5 — KnowledgeRelation eval 门控实验

Status: wontfix（2026-07-18 gate closed；本 PRD 不启动，未来独立产品证据可重开）
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

暂不启动实现，也不预建 relation schema。DS-S3 Reader 与 DS-S4 ReAct 的真实 cassette 已重录、五门全绿，
但生产筛选/citation 与开放 Agentic Search 尚未 dogfood；此时添加关系抽取仍会把尚未验收的 grounding/search
基线与语义图变量混在一起。启动本实验前必须先完成 dogfood，并锁定真实材料样本、无关系基线、相关性/
grounding/token/latency 指标、最低收益阈值和 HITL 抽样方法。未达到门槛时默认删除实验路径，KnowledgeItem
与 Learning Memory 身份语义不变。

## Final gate decision（2026-07-18）

DS-S3/4 的生产 dogfood 已通过：真实问题仅靠 selected FTS search、3 次 bounded read 与 2 条 exact node citation
完成，读取正文 13.33%，没有出现 prerequisite-aware selection 或多跳知识关系才能解决的缺口。该 turn 为处理深层
工具链已累计 132403 model tokens；在没有预注册关系数据集、无关系基线、最低相关性/grounding 增益阈值与 HITL
precision 样本前增加关系抽取，会扩大成本和变量，却不能证明产品收益。

因此按本 issue 原定 gate 的默认分支关闭实验：不建 relation schema、不抽边、不接选题/查询消费路径，也不创建
CanonicalConcept 或迁移 Learning Memory。这里的 `wontfix` 仅表示本 PRD 不行动，不否定未来能力；只有出现独立、
可复现的 prerequisite 或 multi-hop 产品失败，并先预注册对照 eval 后，才以新 PRD 重开。

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

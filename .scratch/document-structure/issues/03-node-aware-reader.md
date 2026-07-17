# DS-S3 — Reader 节点化覆盖型深读

Status: ready-for-human（code complete；真实 Reader cassette / dogfood 待执行）
Type: AFK

## Parent

[PRD：修订化文档树、精确溯源与渐进式 Agentic Search](../PRD.md)

## What to build

让 ingest Reader 以 DocumentNode 自然节点为唯一持久结构单位，替换现有仅按 token/段落生成的临时 chunk。
代码确定性枚举全部可考节点、组成预算内批次并聚合 Reader 输出；Reader 不能自由跳过章节。每条候选
KnowledgeItem 必须经过 DS-S2 的精确 evidence 校验，再进入现有 keep/reject/cancel 审批与原子快照提交。

这个 slice 保持完整 ingest workflow、32k Provider fail-closed 门、结构化重试、取消语义和短文行为，不引入
查询期 Agentic Search，也不把“单次进程内渐进处理”冒充跨进程 checkpoint。

覆盖 PRD User Stories：4–7、13、16–18、21。

## Acceptance criteria

- [x] ingest 在任何 Reader 调用前确定性建立候选 revision/tree，并只从 DocumentNode 生成 Reader 批次
- [x] traversal 覆盖所有符合规则的可考节点；每个 source span 恰好进入一个基础批次，空/纯导航节点有明确跳过规则
- [x] 批次预算包含 prompt、节点标题/path、正文和结构化输出余量；Provider 完整请求硬门保持 fail closed
- [x] 超大节点由 Document Structure parser 的 synthetic children 处理，Reader 内不存在第二套任意 token chunker
- [x] 每个批次沿用有界 ModelRetry、MODEL_STARTED/ENDED span、取消和错误分类；失败不提交候选 revision
- [x] Reader 输出只能引用本批 node key；跨节点概念通过多 evidence 聚合，未知/越界/改写 quote 走结构化重试
- [x] 代码聚合 topic、item 与 evidence；重复 fingerprint 保留确定性首项，重复概念审计不依赖人工从跨片噪声猜测
- [x] 短文只产生一个等价节点批次时，不增加无意义多轮调用；已有短文行为和审批展示保持稳定
- [x] keep/reject/cancel 分别证明获批 snapshot、筛选后 snapshot、旧 current snapshot 的正确结果
- [x] 任一批次、approval 或 transaction 失败后，旧 current revision/tree/items/evidence 与 FTS 可见状态不变
- [x] trace 可重建 traversal 顺序、批次 node ids、预算估算、model token、retry、审批决定和最终 revision commit
- [x] 对同一长文比较旧临时 chunk 基线与节点批次：正文覆盖不下降、重复候选不增加、无请求超过 Provider 门
- [ ] fake provider 全覆盖；使用 `.env` 真实模型重录受影响 Reader cassette，不能手工伪造请求 key 或输出
- [ ] 至少一份真实长文完成 ingest → 筛选 → current revision/tree/item/evidence 写入，并从 citation 返回原文
- [ ] 五门与全部 eval 全绿；真实 trace 记录 token/成本和节点批次，不以提高预算掩盖超限

## Completion evidence（2026-07-17）

- Reader production path 接收 `DocumentSnapshot`，确定性枚举自然正文节点并按完整请求预算组批；旧任意 token
  chunker 已删除，超大正文只由 parser synthetic nodes 处理。
- 模型只看批内稳定 node key 和 node-local offset；代码转换为全局 locator，并对 unknown node、越界、改写
  quote 做有界重试后 fail closed。`reader_batch` span 包住嵌套 model spans。
- fake provider 与原子 ingest 测试已覆盖；旧 Reader cassette 因契约正确变更而 ReplayMiss，必须真实重录。
- 真实长文 ingest → HITL 筛选 → citation dogfood 尚未执行，故 issue 保持 `ready-for-human` 而非 done。

## Blocked by

- [DS-S2](02-exact-evidence-citations.md)

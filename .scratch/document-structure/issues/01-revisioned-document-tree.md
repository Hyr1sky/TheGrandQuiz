# DS-S1 — ResourceRevision + DocumentNode 当前快照

Status: ready-for-agent
Type: AFK

## Parent

[PRD：修订化文档树、精确溯源与渐进式 Agentic Search](../PRD.md)

## What to build

交付第一条完整结构竖切：一份获批 LearningResource 内容成为不可变 ResourceRevision，由确定性 parser 建成
DocumentNode 树，并与 current revision 指针在既有 transaction seam 中原子提交。现有生产形状可无 LLM 地
迁移为初始 revision；新树提交失败时，旧当前正文、知识快照和搜索可见状态完全不变。

这一 slice 只建立 source-of-truth 与树查询，不改变 Reader prompt、不要求精确 evidence locator，也不引入
FTS/Agent 工具。完成后，调用方已经可以稳定读取当前或指定历史 revision 的有序大纲和节点正文。

覆盖 PRD User Stories：3、6、7、12–15、19、26。

## Acceptance criteria

- [ ] 顺序 SQLite migration 建立不可变 ResourceRevision、DocumentNode 与 current revision 约束，显式外键检查通过
- [ ] LearningResource 的稳定 locator 身份与现有 resource_id 不变；revision_id 可由 resource_id + content_hash 确定性复算
- [ ] Markdown/纯文本 parser 覆盖嵌套标题、重复标题、无标题、空文档、列表、表格、代码块和超大 section
- [ ] 每个文档有 synthetic root；所有非空原文被节点 source span 完整覆盖且不重叠，节点顺序和 node_id 重跑一致
- [ ] section_path 只用于展示；重复/缺失标题仍产生不同且稳定的 node_id
- [ ] 超大自然 section 按完整段落生成 synthetic children，单节点读取预算受控且正文不丢失
- [ ] 候选 revision/tree 在审批前不替换 current；提交成功后 resource、revision、tree 与当前知识快照共享事务语义
- [ ] 注入 revision/node/current-pointer 任一写失败时事务回滚，旧 current revision、旧 KnowledgeItem 和成功事件保持不变
- [ ] 已获批旧 revision 保留、默认查询只返回 current；显式 revision 查询仍能读取旧大纲与原文
- [ ] 当前 schema 夹具迁移后 resource_id、item_id、Learning Memory、AskedQuestions、Difficulty 与 Preference 均保留
- [ ] 现有 raw_content/content_hash 确定性回填为初始 revision，迁移/回填可重入且不调用 Provider
- [ ] Dict 与 SQLite adapter 对 revision 提交、树遍历、current 切换、历史读取、失败回滚的可观察结果 parity
- [ ] parser 与 snapshot 事件进入事件脊柱，成功事件只在事务提交后发射，trace 能记录 revision/node 数与失败原因
- [ ] 五门全绿；Provider messages、tool schema 与现有 cassette 无无关变化

## Blocked by

None - can start immediately.

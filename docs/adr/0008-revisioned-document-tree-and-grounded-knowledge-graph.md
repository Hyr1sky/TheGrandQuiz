# ADR-0008: 文档修订以结构树索引，知识证据锚定 DocumentNode，语义知识图分层演进

- 状态：已接受
- 日期：2026-07-17

> 实施状态（2026-07-17）：DS-S1–S4 代码与生产 schema v11 迁移已完成；两份受 Reader prompt / ReAct tool
> schema 影响的真实 cassette 待重录，完成前不声称五门全绿。DS-S5 KnowledgeRelation 仍按本 ADR 的 eval 门控
> 原则关闭，未创建关系 schema 或生产消费路径。

## 背景

当前 ingest 把学习材料的完整正文保存到 `LearningResource.raw_content`，Reader 再把正文编译为扁平的
`KnowledgeItem[]`。长文稳定性加固虽然用确定性 token 估算把 Reader 请求切到 Provider 预算内，但这些片段
只是一次执行中的临时边界：它们不持久化、不表达原文层级，也不能被 Reader、ReAct 或 Summarizer 再次导航。

现有 `Evidence` 已预留 `locator`，`CONTEXT.md` 与 roadmap 也预留了 `section_path`、资源内结构和
PageIndex 式“先读大纲、再选章节”，但生产 Reader 仍输出空 locator。因此系统目前可以证明“这段引文来自
某个资源”，却不能稳定回答“来自该资源哪个修订、哪一节、哪一段”；重 ingest 后，历史 trace 中的引用也可能
失去可解析的原文版本。

这同时造成四类压力：

1. Reader 只能面对整篇正文或无语义 token 片段，长文覆盖、重复概念和预算控制互相牵制。
2. 开放 ReAct 只能看到资源目录，不能执行“大纲 → 候选章节 → 精确正文”的渐进式搜索。
3. 出题、判卷与总结虽携带 quote，却缺少可供用户沿原文复习的稳定路径。
4. 未来的资源内知识关系与跨资源归并没有可靠 provenance；若直接把文档父子层级当概念层级，会把作者的
   排版结构误当成知识语义。

ADR-0002 已规定概念同一性仍以资源内 `KnowledgeItem` 为边界；ADR-0005 已规定所有资源进入同一个全局 KB；
ADR-0007 已规定 `LearningResource` 按稳定 locator 定位、`content_hash` 表达内容 revision，并要求重 ingest
原子替换当前获批知识快照。本决策深化这些既有约束，不重新引入 LearningTask，也不改变 Learning Memory
当前锚定 `KnowledgeItem` 的规则。

## 决策

### 1. ResourceRevision 是获批原文的不可变版本

- `LearningResource` 继续表达稳定 locator 身份，并指向一个 `current_revision_id`；正文不再被视为资源身份
  的一部分。
- 每个获批内容版本生成不可变 `ResourceRevision`，身份由 `resource_id + content_hash` 确定性派生，保存原文、
  内容 hash、获取元数据和创建时间。
- 新 ingest 在审批前只形成候选 revision；审批与知识快照提交成功后，才原子切换 `current_revision_id`。
- 已获批旧 revision 保留但不进入当前选题、搜索和摘要默认范围；它只用于历史 trace、引用解析与显式版本审计。
- 后续若真实存储压力出现，可增加显式 GC；不得在当前 revision 切换时隐式删除仍可能被 trace 引用的旧版本。

### 2. 每个 revision 确定性生成 DocumentNode 树

- `DocumentNode` 是原文结构节点，不是知识概念。节点至少携带 revision、父节点、顺序、层级、kind、标题、
  可读 `section_path`、原文 source span、摘要与内容指纹。
- 第一阶段针对 Markdown / 纯文本做确定性解析：标题形成 section 层级；段落、表格、列表和代码块保留有序
  source span；无标题或超大 section 以段落边界生成 synthetic node，仍不得丢失或重叠正文。
- `node_id` 绑定具体 revision 与确定性结构/内容指纹；`section_path` 是展示和导航字段，不作为身份，因为标题
  可能重复、缺失或改名。
- LLM 不参与父子结构、顺序、offset 或节点身份的决定；LLM 可生成节点摘要，但摘要是可重建派生物，必须记录
  prompt/model 版本并受 Replay 与预算门约束。

### 3. Evidence 精确锚定 revision、node 与 source span

- `KnowledgeItem` 继续是资源内概念身份；它通过一条或多条 evidence 引用 `ResourceRevision` 和
  `DocumentNode`，并保存 quote、精确 offset（有页码时同时保存 page/block 信息）与 quote hash。
- `section_path` 作为 denormalized citation path 随 evidence 返回，便于 LLM 和用户阅读；可解析身份仍是
  `revision_id + node_id + source span`。
- 提交前由代码验证 source span 落在 node 和 revision 正文内，且 quote 与对应原文规范化后一致。校验失败必须
  触发现有结构化输出重试或拒绝提交，不能伪造 locator。
- `KnowledgeItem` 指纹继续使用规范化概念名与稳定 evidence 引文集合；locator、summary 或节点摘要变化本身不
  改变 item 身份，保持 ADR-0007 的“宁可丢失状态、不能串账”原则。

### 4. 文档结构树与语义知识图严格分层

- `DocumentNode --contains--> DocumentNode` 只表示原文结构。
- `KnowledgeItem --supported_by--> Evidence --located_in--> DocumentNode` 表示可确定性校验的 grounding。
- `KnowledgeItem --prerequisite|related|contradicts--> KnowledgeItem` 才表示知识语义；这些边由 Reader 在已抽取
  item 集合内提出，存为带 confidence、provenance、prompt version、trace id 和 review status 的一等关系行。
- 不把关系藏在 `KnowledgeItem.metadata` JSON 中。metadata 可保存 aliases、tags、difficulty hint、候选
  concept key 与抽取版本等实验标注，但可查询、可门控、会影响选题的关系必须有类型化 schema。
- 不根据 section 父子关系自动推导 prerequisite、broader 或 narrower；文档排版不是概念本体。

### 5. 跨资源全局概念仍是后续可撤销投影

- ADR-0002 继续有效：当前概念身份和 Learning Memory 锚点都是 `KnowledgeItem`，同一概念出现在不同资源时
  仍是不同 item。
- `concept_key` 与 metadata 可作为未来候选匹配信号，但本阶段不创建全局 `CanonicalConcept`，不自动迁移
  Learning Memory，不执行 LLM same-as 合并。
- 若后续 eval 与 dogfood 证明跨资源关系有价值，可新增 `CanonicalConcept`，以 `represented_by` 关联多个
  source-grounded `KnowledgeItem`。它是可重建聚合投影，不能覆盖或删除原 item 与 evidence。

### 6. Agentic Search 建在深的 Document Structure module 后

- 模块对 Reader、ReAct、Summarizer 与 eval 提供一致的行为：查看大纲、搜索节点、展开节点、读取有界正文、
  解析 citation；调用者不感知 SQLite 递归查询、FTS 细节、节点切分或引用校验。
- 第一阶段使用 SQLite adjacency rows + recursive CTE 保存树，以 FTS5/BM25 搜索标题、摘要与正文；当前不引入
  向量库或图数据库。
- 当前只有 SQLite 一个实现，不为假想后端预建 adapter seam。先稳定搜索、排序、预算和 citation 行为；出现
  第二个真实实现时再提取 adapter。
- ReAct 可由 LLM 决定“读哪一节”，但代码限制资源范围、最大节点数、返回字符/token、展开深度和不可信内容
  标签。核心考核 workflow 不改为自由 ReAct。
- ingest Reader 以树的自然节点替代任意 token chunk，按确定性覆盖策略处理全部可考节点；面向用户问题的
  Agentic Search 才按 query 选择子树。延迟生成部分 KnowledgeItem 不在本 ADR 第一阶段承诺内。

### 7. 生命周期、事件和迁移保持可恢复、可评测

- 候选 revision、树、KnowledgeItem 与 evidence 在审批前不替换当前快照；审批后通过既有 transaction seam
  原子提交并切换 current revision。
- 解析、搜索、节点选择、展开、引用校验、关系抽取与拒绝原因都发领域事件到同一 `AgentEvent` 脊柱；trace
  至少能还原 query、候选节点、选中节点、预算、revision 和最终 citations。
- 迁移必须保留现有 `resource_id`、`item_id`、Learning Memory 和当前获批正文。现有资源从
  `resource_id + content_hash` 确定性建立初始 revision；结构回填不调用 LLM。
- 旧 evidence quote 若能在原文中唯一定位，则确定性回填 node 与 offset；重复或未命中的引用标为未解析并进入
  审计，不能猜测。未完成结构回填不得让既有 KnowledgeItem 从考核候选池消失。
- 确定性 parser、两种 store adapter parity、原子切换、locator 校验和 FTS 排序走 TDD；LLM 节点摘要、Reader
  结构化输出与语义边走 Record/Replay + eval。

## 备选方案

### 继续保留 raw_content + 临时 token 分块

改动最少，也已解决 Provider 单请求超限，但不能提供持久大纲、精确 citation、渐进式检索或未来图关系的
provenance；Reader 与其他调用方会各自重复发明切分和定位。拒绝。

### 只把整棵树作为 JSON 存在 resources 上

写入简单，但局部搜索、递归导航、外键校验、版本比较、FTS 与 evidence 反查都会把 JSON 路径知识泄漏到多个
调用方，模块缺少 locality。拒绝，树用普通 SQLite 行持久化；必要时可另外导出 JSON 投影。

### DocumentNode 同时充当 KnowledgeItem

能减少实体数量，但章节/段落是作者组织单位，概念是学习与考核单位，两者并非一一对应。一个概念可能跨多个
节点，一个节点也可能包含多个概念；合并会把结构边误当语义边并破坏 ADR-0002。拒绝。

### 直接引入向量库、图数据库或 Knowhere 运行栈

这些系统可以承载更大规模和多模态检索，但当前个人 KB 的层级树、FTS 和小规模关系查询可由 SQLite 完成。
额外服务会增加运维、Replay 和故障面，却没有当前 eval 证明的收益。拒绝。

### 立刻建立全局 CanonicalConcept 并把 Learning Memory 上移

能快速形成跨资源知识图，但同义归并错误会把学习状态静默串账，且 reconciliation 尚无确定性规则，重现
ADR-0002 已排除的风险。推迟，先保留 source-grounded item 和可撤销候选信号。

### 只保留当前 revision，重 ingest 时删除旧原文和树

活动 KB 更简单，但历史 trace 与已展示 citation 会失去可解析来源，削弱可观测和 replay 叙事。拒绝；旧获批
revision 默认保留，未来以显式 GC 管理。

## 后果

### 好处

- Reader 的预算单位从任意 token 片段升级为可复用的自然结构节点，长文处理与搜索共享同一地基。
- 出题、判卷、总结和对话可以给用户展示可返回原文的学习路径，而不仅是一段孤立 quote。
- FTS5 提供当前足够的稀疏检索，同时保留以后增加 reranker、embedding 或其他 adapter 的演进空间。
- 文档结构、grounding、语义关系和跨资源归并各自有明确可信度与生命周期，不会互相污染。
- revision、node selection、citation 与 relation provenance 进入 trace，可建立预算、召回、grounding 和图收益 eval。

### 代价与风险

- schema、迁移、Store interface、Reader prompt/cassette、ingest 原子提交和 ReAct 工具都会变化，是一组需要按
  竖切推进的基础设施工作。
- 历史 revision 会增加存储占用；当前接受该成本，以换取引用与 trace 可解析性。
- Markdown 标题质量不一；synthetic node 能保证预算与覆盖，但不能凭空恢复作者未提供的语义层级。
- 精确 locator 会暴露现有 Reader quote 改写、跨片重复和不在原文中的“证据”，短期可能增加结构化重试和
  真实 cassette 重录，这是正确的 fail-closed 信号。
- 语义边是 LLM 推断，不得与确定性结构边享有同等信任；若 eval 无收益，关系实验应被删除而不是因已建 schema
  强行保留。

### 重新审视信号

- SQLite FTS/recursive CTE 在真实 KB 规模下出现可测的延迟、召回或并发瓶颈。
- PDF、Office、图片或表格成为主要材料来源，需要把 page/bbox 与 parser adapter 提升为正式 seam。
- 历史 revision 的存储增长显著，需要引用感知 GC 或外部 blob 存储。
- 跨资源重复 KnowledgeItem 已明显损害选题和 Learning Memory，且出现可靠、可解释、可 replay 的归并数据与
  reconciliation 规则。
- 语义关系 eval 能稳定提升前置知识感知选题或多跳问答，才把实验能力升为产品默认路径。

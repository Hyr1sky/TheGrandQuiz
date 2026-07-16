# ADR-0007: 资源按稳定 locator 定位，KnowledgeItem 按概念指纹标识，重 ingest 原子替换快照

- 状态：已接受
- 日期：2026-07-16

## 背景

全局 KB 重构用 `resource_id = derive_id(url)`、`item_id = f"{resource_id}#{index:03d}"` 实现了同 URL
去重与资源内唯一（ADR-0005）。稳定性审计发现这套规则只保证“字符串和序号可复算”，没有保证“同一个
ID 明天仍指向同一个领域概念”：

- 本地 CLI 把所有文件写成 `file://local/<文件名>`，不同目录的同名文件产生同一个 `resource_id`。
- URL 是资源 locator，不是内容；现有文档把 `derive_id(url)` 称作“内容寻址”不准确。真实内容另有
  `content_hash`，同 URL 内容可以变化。
- Reader 输出顺序变化时，`#000` 可以从概念 A 变成概念 B；Learning Memory、AskedQuestions 与
  Difficulty 仍锚定 `#000`，于是 A 的状态静默转移给 B。
- 重 ingest 只逐项 `INSERT OR REPLACE`，新快照数量减少时旧尾项不会删除。
- ingest 一开始就用 pending resource 覆盖已读资源；重抓或 Reader 失败会把之前可用的资源标成 failed，
  但旧 KnowledgeItem 仍留在全局选题池，资源状态和知识快照互相矛盾。
- SQLite schema 没有资源 / KnowledgeItem 外键。ADR-0002 曾写明 Learning Memory 应使用代理主键、
  `item_id` 作可空 FK 以支持未来重指，实际 `learning_memory.item_id` 仍是无 FK 的主键。
- SQLite `INSERT OR REPLACE` 的语义是删除冲突行再插入；若后续加 `ON DELETE CASCADE` 仍继续使用它，
  更新同一 KnowledgeItem 会先删除旧行并误清其全部学习状态。

这些问题会直接破坏“薄弱概念锚定具体 KnowledgeItem”的领域不变量。相比偶尔丢失状态，把状态错绑到
另一个概念更危险，因为系统会确信一份错误记忆是真的。

## 决策

### 1. Resource 是 locator-addressed，内容版本由 content_hash 表达

- `resource_id` 继续从稳定的资源 locator 确定性派生，但术语改为 **locator-addressed**，不再称
  `derive_id(url)` 为内容寻址。
- 远程 locator 做保守规范化：scheme / host 小写、去默认端口、去 fragment；不擅自删除或重排 query，
  因为 query 可能改变真实内容。
- 本地文件 locator 使用解析符号链接后的绝对路径生成**不泄露路径的 opaque token**，并保留文件名供
  人识别：同一路径重 ingest 仍是同一资源，不同目录同名文件必为不同资源，移动文件视为新资源。
- `content_hash` 表达该 locator 当前获批快照所基于的内容版本；它不参与 `resource_id`，因此同 URL / 路径
  的更新是同一资源的新 revision。

### 2. KnowledgeItem 用资源内概念指纹标识，不用 Reader 序号标识

- 每个候选计算一个资源内 `item_fingerprint`：规范化概念名 + 规范化、稳定排序的 evidence 引文集合。
- `item_id = derive_id(resource_id, item_fingerprint)`；Reader 重排不会改变 ID。
- summary、confidence 与 evidence locator 可更新而不改变 ID；概念名或证据实质变化产生新 ID。
- 同一 Reader 输出中出现重复 fingerprint 视为结构化输出错误，触发既有 ModelRetry，不用序号偷偷消歧。
- `concept_key` 仍只服务未来跨资源归并，不能拿它替代本次资源内身份。
- 精确指纹不匹配时宁可把它当新 KnowledgeItem、清理旧锚点状态，也不做 LLM 语义猜测式 reconciliation。

### 3. 重 ingest 是审批后的原子快照替换

- fetch、Reader 与审批全部在旧获批快照之外暂存；它们失败或被中断时，已有可用快照保持不变。
- 首次 ingest 失败可以保存 failed resource；已有 read resource 的一次刷新失败只发失败事件，不把已获批
  快照覆盖成 failed。
- 审批完成后，在一个 SQLite 事务中：upsert resource revision、upsert 本次获批 KnowledgeItem、删除该
  资源本次快照中已不存在的旧 item。
- 用户明确审批为空列表代表接受一个空知识快照：旧 item 全部删除；“尚未审批”不得与“审批为空”混同。
- 只有事务提交成功后才发 `RESOURCE_APPROVED` / `ITEM_CREATED` 和成功结束事件；失败回滚后发失败事件。

### 4. 被移除 KnowledgeItem 的关联账确定性清理

- `knowledge_items.resource_id` 外键指向 resources；Learning Memory、AskedQuestions 与 Difficulty 的
  `item_id` 外键指向 knowledge_items，删除 KnowledgeItem 时级联删除对应状态。
- Learning Memory 按 ADR-0002 的既定后果补上代理主键；`item_id` 为唯一、可重指的 FK 属性。
- 所有连接显式开启 SQLite foreign keys。
- 更新已有 resource / item 使用 `INSERT ... ON CONFLICT DO UPDATE`，禁止 `INSERT OR REPLACE` 触发
  delete-then-insert 级联。
- Dict 与 SQLite adapter 对同一快照替换序列保持结果 parity；内存实现也必须删除不在新快照中的旧 item。

### 5. 一次性迁移采用备份后清库重建

- 用户已确认早期 dogfood 数据可以清库重建。执行迁移前复制 learning DB 为带日期的备份，并验证备份
  可打开、原始文件大小非零。
- 新 migration 直接落新 schema 并清空现有知识与关联账，不尝试把序号 item_id 猜测映射到概念指纹。
- Preference Memory 不锚定 KnowledgeItem，继续保留；是否保留语言偏好由迁移验收明确记录。
- 真实材料重新 ingest 后，旧备份保留到全量 dogfood 验收完成。

## 备选方案

### 保留 index item_id，只在重 ingest 时清空该资源全部关联账

可以阻止状态错绑，改动也较小，但即使 Reader 只调整顺序、概念和证据完全没变，全部学习状态仍会丢失；
序号仍不是领域身份。拒绝。

### item_id 包含 content_hash + Reader prompt/model version + index

能保证任何输入或执行环境变化都产生新 ID，不会错绑；代价是网页一个标点变化或 prompt 小改都会清空全部
学习历史。它更像 revision row，不是 KnowledgeItem identity。拒绝。

### 用 LLM 对旧、新候选做语义匹配并迁移状态

可能保留更多状态，但归并错误会静默污染 Learning Memory，且结果难以确定性 replay，重现 ADR-0002
排除跨资源 LLM 判同的风险。MVP 不采用。

### 只加数据库外键，不改 item_id

外键能清理被删除 item，却无法识别 `#000` 已从概念 A 变成 B；同 ID upsert 后关联账仍会错绑。拒绝。

### 以内容 hash 作为 resource_id

同 URL 每次内容更新都会变成新资源，目录中积累多个近重复 revision，用户也无法表达“刷新这篇文章”。
内容 hash 应是 revision 证据，不是资源 locator。拒绝。

## 后果

### 好处

- Reader 重排不再改变 KnowledgeItem 身份；学习状态不会静默转移给别的概念。
- 重 ingest 得到一个真实快照，不残留旧尾项，失败刷新不破坏已有可用知识。
- 资源、知识和关联账的删除语义由数据库约束兜底，脏 orphan row 在 schema 层被阻止。
- “资源 locator”与“内容 revision”术语分开，后续 Web Search / Fetch / MCP adapter 可共享同一模型。
- Dict / SQLite adapter 的测试面从零散 CRUD 深化为快照替换行为。

### 代价

- item_id 全线变化，现有 cassette、eval fixture 和真实 learning DB 需要清理或重录。
- 精确概念指纹对 LLM 改写敏感；概念名或证据实质变化会丢失该 item 的旧状态。这是“宁可丢、不能串”
  的明确取舍。
- ingestion 的 store interface 需要从多次 `add_*` 调用收敛为快照提交语义；S1 与后续学习状态事务 S5
  必须共享同一 transaction seam，不能各自发明事务管理。
- 本地路径移动会生成新 resource_id；若未来需要跨路径识别同一文件，再单独引入用户可见 alias，不能用
  内容 hash 自动合并。

### 重新审视信号

- 真实 dogfood 显示概念名 / evidence 的轻微改写频繁导致有价值状态丢失。
- 出现可靠、可解释且可 replay 的资源内 concept alias 数据，需要在精确指纹之上增加显式 reconciliation。
- 多用户或远程同步要求资源 locator 跨机器稳定，本地 opaque path token 不再足够。

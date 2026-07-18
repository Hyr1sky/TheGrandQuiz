# DS-S2 — 精确 Evidence 与可解析 citation

Status: done（真实 Reader cassette 已重录；精确 locator 回放通过）
Type: AFK

## Parent

[PRD：修订化文档树、精确溯源与渐进式 Agentic Search](../PRD.md)

## What to build

把 KnowledgeItem 的 evidence 从“quote + 可空 locator”深化为可验证的 revision/node/source-span 引用。Reader
在已知 DocumentNode 上返回 node-local evidence，代码转换并校验全局位置；获批 citation 能稳定解析为材料、
内容版本、section_path、位置、quote 和有界上下文。

同时为现有生产知识做确定性回填：quote 在当前 revision 原文中唯一出现时定位到所属节点；重复或未命中时
保持现有 item 可用并生成 unresolved 审计，不用模糊匹配或 LLM 猜测。新快照中的 unresolved/伪造 locator 则
fail closed，不能获批写入。

覆盖 PRD User Stories：1、2、10、16、17、20、25。

## Acceptance criteria

- [x] Evidence 持久模型保存 revision_id、node_id、section_path、global source span、可选 page/block、quote 与 quote hash
- [x] evidence 关联使用普通 SQLite 行与外键，KnowledgeItem 对外模型仍可稳定返回有序 evidence 列表
- [x] 提交校验证明 node 属于 revision、span 位于 node/raw content、quote 规范化后与原文一致；失败阻止整个新快照
- [x] Reader 只返回本批已提供的 node key 与 node-local offsets，代码负责解析数据库身份和 global offsets
- [x] 一个 KnowledgeItem 可引用多个节点，一个节点可支持多个 item；evidence 顺序与 item fingerprint 输入确定性稳定
- [x] locator、section_path 或 summary 单独变化不改变 item_id；概念名/quote 实质变化仍遵守 ADR-0007 指纹规则
- [x] citation renderer 输出可读资源标签、明确 revision、section_path、位置、quote 和有界上下文
- [x] 解析历史 citation 时读取其声明 revision，不静默切换到 current revision；旧 revision 不存在时返回结构化错误
- [x] 当前旧 quote 唯一匹配时确定性回填 node/span；多处匹配或未命中时标为 unresolved 并进入审计报告
- [x] 旧 unresolved evidence 不让现有 KnowledgeItem 从考核池消失；新 Reader 候选 locator 未解析则不可提交
- [x] backfill 可重入，重复运行不改变已解析 locator、item_id、学习状态或审计计数
- [x] property/generative tests 覆盖 Unicode、规范化空白、重复 quote、跨节点 quote、边界 offset 和篡改 quote hash
- [x] citation 校验/拒绝事件进入 trace，包含 revision/node、失败分类和可公开 fingerprint，不泄漏本地绝对路径
- [x] fake provider 测试覆盖有效、多 evidence、未知 node、越界 span、改写 quote 的结构化重试/失败路径
- [x] 五门全绿；需要变化的 Reader cassette 明确列入 DS-S3 真录清单，不手工重写 cassette

## Completion evidence（2026-07-17）

- `0010_exact_evidence.sql` 增加带外键的有序 evidence 行；v9 打开时唯一 quote 确定性定位，重复/缺失进入
  unresolved 审计，重开结果不变。
- `citations.py` 统一执行 grounding、篡改检测、历史 revision 解析与稳定 renderer；新 snapshot 任一证据失败
  即整体回滚。
- 生产库回填 135 条 evidence：83 resolved / 52 unresolved；88 个 item 全部保留，学习状态无差异。
- 完成性审计补充确定性生成用例：Unicode/组合字符、自然节点首尾边界、空白规范化后保存精确原文、重叠
  quote 歧义、跨节点与 hash 篡改。
- `.env` 真实 Reader cassette 已按 DocumentNode 契约重录；12 个 item 的 locator 经纯回放逐字校验，静态四门
  与全量 pytest `768 passed`。

## Blocked by

- [DS-S1](01-revisioned-document-tree.md)

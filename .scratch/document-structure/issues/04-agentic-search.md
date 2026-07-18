# DS-S4 — FTS5 + 渐进式 Agentic Search

Status: done（2026-07-18；真实 ReAct replay + 生产 selected search/read/node-citation dogfood 通过）
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
- [x] 至少一次对生产 current revision 的开放搜索 dogfood 完成渐进读取并返回可解析 citation，真实 trace 证明未倾倒全文

## Completion evidence（2026-07-17）

- `0011_document_node_fts.sql` 与 Store 将 current revision 的索引切换纳入同一事务；v10 打开时可确定性重建，
  FTS 写失败会回滚 revision/tree。中文用 unicode61 + 确定性一/二元投影，不增加外部 tokenizer。
- Document Structure 深模块与六个 ReAct 工具提供 outline/search/expand/read/item citation/node citation；selected
  scope 严格解析，跨工具按 trace 累计读取预算，node citation 强制 read-before-cite。
- capstone 在长文中读取少于 10% 正文找到精确 quote；生产 FTS 1551 rows，全部指向 current revision。
- 完成性审计补充同名章节稳定 tie-break，并让 outline/search/expand 的标题、路径、excerpt 显式携带 untrusted
  标记；成功/拒绝读取事件记录累计预算，node/item citation 拒绝记录结构化分类与安全 fingerprint。
- case14 已用真实模型重录；模型只调用一次 `start_quiz`，参数为 all scope、3 道选择题。目标回放、全部 Tier-1
  eval、HTML report、静态四门与全量 pytest `768 passed`。

## First production attempt（2026-07-18）

- ReAct trace `f0eb5eb637244375b9fb44cb68544d02` 记录了用户真实问答：2 次 `start_quiz`，共 5 道题，题目、判卷、
  concept state 和 follow-up 事件完整持久化。
- 该会话没有 `learning.document_nodes_searched`、成功 `learning.document_node_read` 或
  `learning.citation_resolved(source=node_read)`；`audit-doc` 的 search leg 因 search/read/node-citation 均为 0 而失败。
- 这证明普通 KB 考核路径正常，但不能替代本 issue 的开放 Agentic Search 验收。下一次应明确要求“只查指定材料，
  搜索某个原文问题，读取相关节点并给出可回溯引用”，再以新 trace id 联合审计。

## Production completion evidence（2026-07-18）

- 最终 trace `46b91c61c1c24ebabc94be97db31bb16` 正常产出用户回答，并由 `audit-doc` 与生产
  `learning.db` 联合核验为 `passed=true`：1 次 selected search、3 次成功 bounded read、2 条
  `learning.citation_resolved(source=node_read)`，顺序满足 search → covering read → citation。
- selected scope 恰好为 `6128cc2fa1b9e850`；citation 指向 current revision `a37bdcb799210246` 和 node
  `8848ac957b736804`，span/quote 可逐字解析。累计读取 2762/20721 字符（13.33%），最高预算使用
  2762/12000，没有倾倒全文或扩大 scope。
- 真机失败 trace 先后暴露并锁定四个边界：node citation 参数错误误判 FATAL、read 返回 global 而 resolver 只认
  local、模型不能可靠计算 Markdown/Unicode span、8 次模型迭代后缺 finalization 机会。对应修复仅将本轮
  `NodeCitationValidationError` 标为 DEGRADED；历史 citation 损坏仍 FATAL；resolver 同时接受可唯一验证的
  local/global span，并只在已读窗口内唯一逐字 quote 时派生位置；CLI 上限 12，read/context 硬预算不变。
- 回归覆盖错误回灌后自愈、global span、唯一 quote 自动定位、重复 quote fail closed 与 8-tool + final 深链；
  静态四门、全部 eval 与全量 pytest `775 passed`。

## Dogfood evidence protocol

在 DS-S3 dogfood 已提交的 current revision 上启动真实 `grandquiz react`，让 Agent 回答一个必须查材料原文的问题，
并明确要求给出可回溯 citation。完成后从 `trace.db` 核对并把结果写回本 issue：

- trace 至少包含 `learning.document_outline_viewed` 或 `learning.document_nodes_searched`，随后包含
  `learning.document_node_read` 和 `learning.citation_resolved`；citation 事件必须是 `source=node_read`，且此前已有
  覆盖该 span 的成功 read，不能用既有 KnowledgeItem citation 代替 Agentic Search 证据。
- 本次验收明确指定材料，搜索必须使用只含该 resource id 的 selected scope；不能先 all-scope 倾倒候选，也不能在
  unresolved scope 后退回全库。
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

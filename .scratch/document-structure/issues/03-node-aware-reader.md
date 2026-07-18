# DS-S3 — Reader 节点化覆盖型深读

Status: ready-for-human（生产 ingest/人工筛选已通过审计；最终 citation 与 DS-S4 联合验收）
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
- [x] fake provider 全覆盖；使用 `.env` 真实模型重录受影响 Reader cassette，不能手工伪造请求 key 或输出
- [ ] 至少一份真实长文完成 ingest → 筛选 → current revision/tree/item/evidence 写入，并从 citation 返回原文
- [x] 五门与全部 eval 全绿；真实 trace 记录 token/成本和节点批次，不以提高预算掩盖超限

## Completion evidence（2026-07-17）

- Reader production path 接收 `DocumentSnapshot`，确定性枚举自然正文节点并按完整请求预算组批；旧任意 token
  chunker 已删除，超大正文只由 parser synthetic nodes 处理。
- 模型只看批内稳定 node key 和 node-local offset；代码转换为全局 locator，并对 unknown node、越界、改写
  quote 做有界重试后 fail closed。`reader_batch` span 包住嵌套 model spans。
- fake provider 与原子 ingest 测试已覆盖；旧 Reader cassette 因契约正确变更而 ReplayMiss，必须真实重录。
- 完成性审计新增一个 KnowledgeItem 跨两个自然节点的多 evidence 公共路径测试，证明 evidence 顺序与两个
  revision-global locator 都由 Reader node-local 输出确定性解析。
- fake provider 已证明正文覆盖与预算不变量；“真实候选重复率不增加”必须和旧真录基线在同一长文上比较，
  不再用确定性去重代码间接代替模型层证据，故对应验收项恢复为未完成。
- 旧真录的非敏感指标已冻结在 `../evals/reader-node-baseline.json`（材料 SHA256、调用/候选/重复/token）；
  新真录为 105/105 个可考节点 exactly-once 覆盖、12 个候选、0 重复、8715 prompt tokens，低于 32k 门。
- 真录发现模型会精确选择 node/start/quote、却普遍算错右边界；回归测试先复现后，Reader 改为仅在 quote 从声明
  start 逐字匹配时确定性计算 end。错误 node/start/quote 仍重试并 fail closed，不引入模糊定位。
- 真实长文 ingest → HITL 筛选 → citation dogfood 尚未执行，故 issue 保持 `ready-for-human` 而非 done。
- 静态四门、全部 eval 与全量 pytest `768 passed`；持久化真实 trace 仍由上述 dogfood 验收补齐。
- `approval.decided.decision_source` 明确区分 `human_cli` 与 `scripted`；只读 auditor 会拒绝测试/录制脚本审批。

## Production ingest evidence（2026-07-18）

- 用户对授权材料执行真实 `grandquiz ingest` 并在 CLI 完成人工审批；实际 trace 位于默认
  `~/.grandquiz/trace.db`，不是最后修改于 2026-07-15 的 `localtemp/trace.db`。
- ingest trace `2515ec1af79a4a0a9860993b4a35beb9`、resource `6128cc2fa1b9e850`、current revision
  `a37bdcb799210246` 经 `grandquiz audit-doc` 的 ingest leg 全部通过。
- revision 含 172 个结构节点，其中 141 个可考正文节点 exactly-once 分入 2 个闭合批次（96 + 45）；估算分别为
  13920 / 6736 tokens，均低于 16000 Reader 批次门。真实 prompt usage 分别为 11154 / 5776，低于 32k Provider 门。
- Reader 产出并经 `human_cli` 审批保留 34 个 item；`learning.citation_validated` 先于审批与 revision commit，DB
  current snapshot 的 34 条 evidence 均能逐字解析回声明 node/span。
- 真实模型曾因唯一逐字 quote 的 Unicode 左边界误报触发 `quote_mismatch`。修复仅在声明节点内唯一逐字出现时
  由代码规范化 start/end；零匹配或多匹配仍 fail closed。新增唯一/重复 quote 回归，五门与 `770 passed` 全绿。
- 最终用户可见 node citation 尚未在开放搜索中触发；它与 DS-S4 的 selected search → bounded read → node citation
  使用同一次后续 dogfood 联合验收，因此本 issue 仍不标 done。

## Dogfood evidence protocol

在独立终端对一份已授权长文执行真实 `grandquiz ingest`，由用户在审批界面实际保留/剔除候选；禁止用
`ScriptedApprovalGate(keep-all)` 代替。验收只记录材料 SHA256、trace id 与聚合指标，不额外复制原文。

完成后从 `learning.db` / `trace.db` 核对并把结果写回本 issue：

- trace 同时含 `learning.document_parsed`、配对的 `learning.reader_batch.started/ended`、
  `learning.citation_validated`、`approval.requested/decided` 和 `learning.revision_committed`；成功提交事件不得早于审批。
- 所有 batch 的 `node_ids` 按顺序合并后，恰好等于 committed revision 的全部非 document/section 正文节点，且无重复。
- 每批 `estimated_tokens <= token_budget`；每次 Reader `model.ended` 都有真实 usage，完整请求未超过 32k Provider 门。
- `approval.decided.decision_source=human_cli`，其保留/剔除数量与 committed KnowledgeItem 数一致；DB 的 current
  revision、tree、items、evidence 可见，任一获批 evidence 都能逐字解析回声明 node/span。
- 至少从一个获批 item 的 citation 返回 revision、section_path、精确位置、quote 与有界原文上下文。

终端入口：

```bash
.venv/bin/dotenv run -- .venv/bin/grandquiz ingest \
  --task "文档结构 dogfood" --db ~/.grandquiz/learning.db /path/to/authorized-long-document.md
```

记下终端打印的 ingest trace id；完成 DS-S4 的搜索后用同一生产库运行联合只读验收：

```bash
.venv/bin/grandquiz audit-doc \
  --db ~/.grandquiz/learning.db \
  --ingest-trace <ingest-trace-id> \
  --search-trace <search-trace-id>
```

命令输出逐项 JSON checks；任一证据缺失或矛盾时退出码非零，不写 learning/trace DB。

## Blocked by

- [DS-S2](02-exact-evidence-citations.md)

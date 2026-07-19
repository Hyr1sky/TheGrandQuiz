# 自然材料问答与 Agentic Search 成本收口开发记录

> 记录日期：2026-07-19
> 范围：`.scratch/grounded-answer-efficiency/` 的 GAS-S1–S4。
> 当前边界：GAS-S1–S4、真实 case14/case15 Replay、生产 current-revision dogfood、联合审计与五门均已完成；
> DS-S5 KnowledgeRelation 继续关闭。

## 1. 问题基线

ADR-0008 / DS-S4 已经提供大纲、FTS5、节点展开、有界读取和精确 citation，但普通自然问题仍完全依赖自由 ReAct
逐步规划。两个真实 trace 说明产品化没有收口：

| 基线 | 模型调用 | 工具调用 | 累计 tokens | 精确 node citation |
| --- | ---: | ---: | ---: | ---: |
| 普通自然材料问题首轮 | 8 | 10 | 82,581 | 0 |
| 显式要求 grounded search | 11 | 11 | 132,403 | 2 |

普通问题可能只展示 node id 后直接结束；显式问题虽能成功，但逐轮回灌不断增长的工具 JSON，使单次 prompt 从约
4k 增长到 15k tokens。根因不是 FTS、DocumentNode 或 citation resolver 缺失，而是一个可稳定评测的 grounding
子路径仍被拆成六个细粒度工具交给外层模型反复规划。

## 2. GroundedDocumentAnswer 深模块

新增 learning domain/application workflow，以一个公共入口完成：

```text
query + exact resource ids + budgets
  → selected scope validation
  → current-revision sparse search
  → deterministic leaf candidate projection
  → bounded node reads
  → one structured answer/evidence model slot
  → code-only quote/span validation
  → revision/node/section_path exact citations
```

调用者只接触 `GroundedAnswerRequest` 与 `GroundedAnswerResult`。结果包含稳定状态、answer、verified citations、
searched/read node ids 和 usage/read metrics。`invalid_scope`、`no_evidence`、`budget_exhausted`、重复/改写 quote
与结构化输出耗尽都 fail closed；模型不能生成数据库身份或在未读正文中找 quote。

模型只看到 query、exact scope 标签和本次已读窗口，每个窗口使用临时 `n0` 等 key。它返回答案与逐字 quote；
代码在对应窗口要求 quote 唯一出现，再复用既有 `DocumentSearch.cite_node` 强制 read-before-cite、current revision
与精确 source span。材料标题和正文继续标记 untrusted，system prompt 明确禁止执行其中指令。

没有新增 SQLite migration、索引、向量库、reranker 或 kernel 回调。搜索、读取、模型、citation 和 workflow
started/ended 仍上同一条 `AgentEvent` 脊柱，workflow span 挂在外层 TOOL_CALL 下。

## 3. 双入口与自然路由

同一模块提供两种消费方式：

1. CLI/API/未来独立 ask 可以直接调用 application service，成功路径只发生一次模型调用。
2. ReAct tool registry 注册 `answer_from_documents`，外层模型只负责一次路由与最终转述；既有六个原子文档工具
   继续保留给复杂探索和诊断。

ReAct prompt 明确普通“根据材料回答/解释/总结并给出处”优先使用高层工具，query 只提取 1–3 个高信息量词或
短语，resource ids 必须来自库存清单。工具失败状态必须如实转述，外层不能用模型常识补写。

确定性 ReAct 集成测试的完整路径是三次模型调用：外层路由 → 内层 grounded answer → 外层转述；工具 history
只包含一个结构化结果，不再随内部搜索步骤线性增长。

## 4. 真录暴露的查询放宽边界

case15 第一次真实录制时，外层正确调用了高层工具，但传入 query `事件总线 信封`。底层稀疏搜索要求两个短语
同时命中，而合成材料只逐字包含“信封”，因此高层工具返回 `no_evidence`；外层随后退回 outline/expand/read/cite
原子路径，总计 6 次模型调用、5 次工具调用，未通过成本门。

修复没有扩大 resource scope 或改 citation 门。组合 workflow 先尝试完整 query；零命中时，仅在同一 exact selected
scope 内依次搜索调用者已经给出的 1–3 个短语，按短语和搜索结果的稳定顺序去重合并候选。SQLite 回归锁定
“一个短语无命中、另一个短语命中”的路径；第二次真实录制只调用一次高层工具并直接收敛。

## 5. 真实 Replay 与成本结果

受 tool schema 与 ReAct prompt 影响的两份 cassette 都由 `.env` 配置的真实模型重录，没有手工改 request key、
tool fingerprint 或模型输出：

- case14 仍只调用一次 `start_quiz(scope=all, count=3, question_type=选择题, focus=mixed)`，三道题全部进入核心
  assessment workflow。
- 新 case15 的用户消息只说“根据库存里的 Agent Runtime 材料……请给出原文出处”，不包含任何工具名；模型只
  调用一次 `answer_from_documents(query=事件总线 信封, exact resource id)`。

case15 最终真实指标：

| 指标 | 门限 | 结果 |
| --- | ---: | ---: |
| 模型调用 | ≤ 4 | 3 |
| 外层工具调用 | 1 个高层工具 | 1 |
| 累计 tokens | ≤ 45,000 | 10,282 |
| 最大 prompt | 记录并保持低于 Provider 门 | 4,962 |
| 正文读取 | ≤ 25% | 1 个相关 leaf，规则 scorer 通过 |
| exact citation | ≥ 1 | 1，current revision + 逐字 span |

相对普通自然问题 82,581 tokens 基线下降约 87.5%，模型调用从 8 降到 3；同时把 0 citation 修复为 1 条精确
citation。因此没有实现通用工具历史压缩器，避免在组合 workflow 已解决根因后继续改 kernel 上下文机制。

## 6. 测试与风险审查

公共接口与集成测试覆盖：

- SQLite selected scope、FTS 候选、leaf 投影、bounded read、一次模型调用和 exact citation；
- unresolved resource 在搜索前拒绝，零读取、零模型调用；
- 多短语完整查询零命中后在同一 exact scope 确定性放宽；
- 模型明确无足够证据时一次调用后返回 `no_evidence`，不重试、不发伪 citation；
- prompt 估算超预算时在模型调用前停止；
- quote 在已读窗口重复时 `citation_rejected`，只留错误 fingerprint；
- ReAct 自然问题只调用一次高层工具，总模型调用为 3；
- case15 scorer 反证零工具直答、scope 扩大、无 read/citation、事件乱序、读取超 25%、调用/token 超门和缺
  usage 均不能通过。

最终工程门：

```text
ruff check .                pass
ruff format --check .       pass（155 files）
pyright                     pass（0 errors）
lint-imports                pass（kernel layering kept）
pytest                      784 passed
```

变更风险审查未发现 high-risk 架构偏离。审查中补回了两个中风险契约：模型返回空 citations 必须成为
`no_evidence` 而非重试耗尽；workflow ended 事件必须保留 started 的外层 TOOL_CALL parent。DS-S5 继续关闭，
KnowledgeItem/Learning Memory/quiz workflow 均未改变。

## 7. 生产 dogfood 与联合审计

生产复验使用已 ingest 的 Agent Memory current revision，启动一个无历史上下文的新 ReAct 会话：

```bash
uv run grandquiz react "Grounded answer production verification" \
  --db ~/.grandquiz/learning.db \
  --materials-dir /Users/hyriskyhe/Documents/TheGrandQuiz/agent-memory.md
```

用户只用自然语言询问：“根据库存里的 Agent Memory 材料，Mem0 的记忆处理流程是什么？请给出原文出处。”，没有
点名 `answer_from_documents`、search、read 或 citation。生产 trace 为
`01c64e58ba9949368a06a4693bc5ec26`，结果为：

| 指标 | 生产结果 |
| --- | ---: |
| 外层高层工具 | `answer_from_documents` × 1 |
| 模型调用 / 累计 tokens | 3 / 12,125 |
| selected search / bounded reads | 1 / 3 |
| 阅读字符 / revision 全文 | 2,044 / 20,721（9.86%） |
| current exact node citation | 1 |

`audit-doc` 将该 trace 与 ingest trace `2515ec1af79a4a0a9860993b4a35beb9`、生产 learning DB 联合核验，
`passed=true`。审计确认 exact selected scope 仅包含 resource `6128cc2fa1b9e850`，citation 属于 current revision
`a37bdcb799210246`，且 span 被更早的 node read 完整覆盖；读取比例低于 25% 门限。

生产探索同时留下一个明确限制：对材料中并不存在的“事件信封 / Agent Event”提问时，高层 workflow 正确返回
`no_evidence`，但外层自由 ReAct 仍可能继续调用原子 outline/search，增加失败路径成本。该行为没有扩大高层工具的
exact scope、没有生成伪 citation，也没有用模型常识补写答案；本轮不以扩大阅读或放宽 citation 修复它。若后续要
优化无证据体验，应单独以失败路径 cost gate 立项，而不是改变本 PRD 已验证的 grounding 基座。

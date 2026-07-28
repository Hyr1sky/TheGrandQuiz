# 开发日志 · 阶段三：稳定性加固与生产数据重建收口

> 记录区间：稳定性加固 SH-S1–S11 → 真实 cassette 重录 → 生产 DB 迁移 / 重建 → 真实考核闭环。
> 截至本记录：五门全绿、`721 passed`；生产 `learning.db` 为 schema v8，含 3 个真实资源与
> 88 个 KnowledgeItem；一轮真实考核已完成出题、作答、判决和持久记账。

---

## 0. 这一轮解决了什么

这一轮没有扩张产品功能，而是把已有 Agent Runtime 的稳定性基线做实：资源和概念身份不能漂移，
考核范围不能静默放大，外部 I/O 与 Provider 请求必须受真实预算约束，Replay 必须识别执行契约，
学习状态与 trace 不能“部分成功”，真实 CLI 也必须经过用户审批后才能写入知识库。

最终结果不是只有测试绿，而是完成了以下真实闭环：

```text
迁移前 DB 备份
  → schema v4 升到 v8
  → 三份真实长文 Reader 深读
  → keep / reject / cancel 审批
  → 3 resources / 88 items 入库
  → 从生产库真实出题
  → 用户作答
  → 判决与 Difficulty / AskedQuestions 原子持久化
```

本记录保留稳定性加固的范围、完成证据与收口结论；相关不可逆决策见
[ADR-0007：稳定资源修订与 item 身份](../adr/0007-stable-resource-revision-and-item-identity.md)。

## 1. SH-S1–S9：九个稳定性竖切

| Slice | 完成内容 |
| --- | --- |
| S1 稳定身份 / 原子快照 | 本地 locator 防同名碰撞；item ID 改为概念证据指纹；重 ingest 成功后一次替换，失败保留旧快照；关联账走外键清理。 |
| S2 scope fail-closed | 用 `all / selected / unresolved` 判别联合区分用户意图；点名解析失败时拒绝出题，不再退回全库。 |
| S3 流式 Web Fetch | 按解压后的实际字节逐块限流，超限立即停止，同时保留 SSRF、重定向、超时和内容类型守卫。 |
| S4 Replay 指纹 | 工具名称、说明与 JSON Schema 进入执行指纹；契约变化触发 `ReplayMiss`，纯文本路径保持兼容。 |
| S5 原子学习状态 | Memory、Difficulty、AskedQuestions 共用事务；失败整体回滚，提交后才发状态事件。 |
| S6 Durable trace | durable processor 写失败使 turn 失败；best-effort 展示 observer 继续隔离。 |
| S7 Provider 预算 | 32k 硬门覆盖 messages、tool specs、工具结果和循环历史，每次出站前重新检查。 |
| S8 直接答对难度 | 从未答错的概念也累计连对并升档；真实回放证明 tier 3→4 后高档提示生效。 |
| S9 真实审批门 | CLI 逐项展示并支持 keep / reject / cancel；取消不留半状态，审批事件继续走事件脊柱。 |

suspend / resume 仍诚实保留为后续 skeleton，没有把阻塞式 CLI 伪装成持久挂起能力。

## 2. 真实 cassette 与测试基线

本轮重录并验证了三条关键真实路径：

1. `assess.cassette.json`：真实出题与判卷，证据逐字锚定。
2. `eval_case14_bulk_quiz.cassette.json`：ReAct 只调用一次受控 `start_quiz`，三题均进入确定性 workflow。
3. `difficulty_activation.cassette.json`：连续三次真实判“对”，唯一触发 tier 3→4，并以高档提示继续出题。

最终工程门：

```text
ruff check                  pass
ruff format --check         pass（137 files）
pyright                     pass（0 errors）
import-linter               pass（71 files / 255 dependencies）
pytest                      721 passed
```

`kernel ↛ domain/interfaces/evals` 合同继续保持，稳定性修复没有破坏“领域无关 Runtime”的依赖方向。

## 3. 真机暴露的 S11：Reader 长文预算缺口

生产数据重建时，Agentic-RL 文档第一次真实调用在审批前被正确拦截：

```text
Provider 请求 47,556 tokens 超过硬上限 32,000
```

这说明 S7 的硬门工作正常，但也暴露 Reader 虽被定义为“隔离大上下文”的唯一 subagent，实际上仍把整篇
材料塞进一次请求。修复没有提高 32k 上限，而是在 Reader 内实现确定性 map/reduce：

- 用可注入的确定性 token estimator 按 16k 单片预算切分。
- 优先在段落边界断开，保证正文不丢失、不重叠。
- 每个片段继续使用原有 pydantic 输出契约、有界 ModelRetry 和 `MODEL_STARTED/ENDED` span。
- 聚合由代码完成：多数片段主题作为资源主题，相同稳定 item ID 保留首次出现者。
- 短材料 messages 逐字不变，既有 Reader cassette 无需无意义重录。
- 32k Provider 硬门保持原值，继续作为最终 fail-closed 防线。

三份真实材料最终分别使用 3 / 2 / 4 个 Reader model span 完成深读。

## 4. 生产 DB 迁移、审批与重建

### 备份与迁移

- 迁移前备份：`~/.grandquiz/learning.db.backup-20260717-130422-pre-migration`
- 备份 schema v4、`quick_check=ok`，含 4 resources / 31 items / 1 memory。
- 备份 SHA256：`6596cd1d74c6957758f7710a686c75ca478490158299645597f182a4aa8637ee`
- 三份可恢复原文按旧 `content_hash` 提取；无 raw content 的失败资源没有伪造重建。
- 用户批准迁移后执行 0005–0008；ADR-0007 明确允许清理身份不稳定的早期 dogfood 数据。

### 审批结果

| 材料 | Reader span | 审批 | 真实 token | trace |
| --- | ---: | ---: | ---: | --- |
| Agentic-RL | 3 | 20 / 27 | 40,546 | `1a93870dfed045089ab74988841c5393` |
| Agent Communication Protocols | 2 | 21 / 21 | 32,875 | `0d1cc92618d8490d808aa17f146681ef` |
| Hook As Reference | 4 | 47 / 49 | 63,000 | `6e6a91e9342a4086a2df1686be9c3824` |

Agentic-RL 剔除了文末思考题误抽出的方案型候选；Hook 剔除了跨片段重复出现的 `ConfigChange hook` 和
`Async hooks`。取消路径也单独真机验收：首项输入 `q` 后 `ingest.ended(ok=false)` 正常闭合，数据库保持不变。

最终生产库：

```text
schema v8
quick_check = ok
foreign_key_check = empty
resources = 3
knowledge_items = 88
empty evidence = 0
orphan foreign keys = 0
same-resource duplicate concepts = 0
```

## 5. 真实考核闭环

从重建后的生产库运行一轮真实选择题考核：

- 被考 item：`SubagentStart and SubagentStop`（`30a4a4aca68c0a23`）
- 用户选择：“SubagentStart 在子代理生成时运行，SubagentStop 在子代理完成响应时运行”
- 判决：对
- trace：`6c61b5074c174fb7a81b9c801ab8ed4b`
- 出题 model usage：835 tokens

事件顺序为：

```text
assessment.started
→ model.started / model.ended
→ learning.question_asked
→ learning.answer_judged
→ learning.concept_state_changed
→ assessment.ended(ok=true)
```

生产库新增 1 条 AskedQuestions 和 1 条 Difficulty（tier 3 / correct streak 1）。首次答对不制造虚假 weak
memory，所以 Learning Memory 保持 0；这与“LLM 判卷，代码记账”的状态机契约一致。

## 6. Git 提交链

```text
dbc0a10 feat(stability): implement S1-S9 hardening
ab3fbd2 docs: reconcile completed PRD statuses
a11f57b test(stability): refresh real replay baselines
ef50def fix(ingest): chunk long reader inputs
35cb4e8 docs: record production rebuild evidence
2d55d66 docs: close stability hardening audit
```

分支：`codex/stability-hardening-closeout`。本阶段完成时工作树干净，未自行 push。

## 7. 保留的后续范围

- 审批与作答的持久 suspend / resume。
- Reader 对跨片段同义概念的语义合并，以及练习题章节的抽取质量策略。
- Web Acquisition：正文抽取、`web_search`、浏览器 fallback、MCP adapter。
- 跨资源 `concept_key` 归并、向量检索、复习排期等产品扩展。

这些项目均未混入本轮稳定性修复，也不影响当前稳定性基线已完成。

## 8. 后续 dogfood / 真实测试协作约定

后续需要真人 dogfood 时，默认采用以下方式：

1. 用户在独立终端直接运行 `grandquiz ingest / quiz / react`，自行完成审批和作答。
2. CLI 已打印 trace ID，并将完整事件写入 `~/.grandquiz/trace.db`。
3. Codex 不再默认通过对话转发终端题目或代按交互选项；需要分析时直接读取 `trace.db`，核对事件顺序、
   span、token、错误和持久状态。
4. 只有 trace 缺失、需要额外外部数据授权，或测试结果存在真实产品判断分歧时，再请求 HITL。

这能让 dogfood 更接近真实使用，也避免把对话本身变成 CLI 交互的中间层。

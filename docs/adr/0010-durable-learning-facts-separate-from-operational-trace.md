# ADR-0010：长期学习事实与完整运行 Trace 分开持久化

- Status: Accepted
- Date: 2026-07-30

## Context

当前所有 `AgentEvent` 都可由 `TraceStore` 持久化到独立 `trace.db`。它适合调试、可观测性、Replay 和
Eval，但会包含用户消息、模型/工具调用、token 与错误等敏感运行细节。Learning Model v2 又需要从考核
事件重建 AssessmentAttempt、纠正记录、诊断、误区和 LearnerProjection。

若长期学习事实只存在于 `trace.db`，清理运行 Trace 会同时破坏学习历史；若在考核 workflow 中再直接写
一张 AssessmentAttempt 领域表，则形成事件与表的双写入口，发生失败时难以解释哪一边才是真相。

## Decision

一条 `AgentEvent` 脊柱保留两个职责不同的持久消费者：

1. `TraceStore → trace.db`：保存完整运行审计，可按独立策略清理。
2. `LearningFactJournal → learning.db`：只保存白名单化、版本化、可长期保留的学习事实。

LearningFactJournal 只接收重建学习模型必要的 committed facts，例如：

- question / answer 的必要学习字段；
- initial / final verdict、VerdictCorrection 和状态 reconciliation；
- Evidence 是否在答题前揭示；
- AnswerDiagnosis、DemandValidation 及人工审核；
- classification、applicability、TagAssignment 与 supersession。

它不保存 system prompt、完整工具 payload、模型思维过程、普通 Chat、token 明细或无关运行错误。

AssessmentAttempt 继续是从 LearningFactJournal 重建的物化读模型，不成为第二个写入口。Journal 的写入
必须与 Learning Memory、DifficultyLedger、AskedQuestionsLedger 的领域提交共享 transaction/outbox
边界；只在 assessment committed 后可见，并以稳定 event identity 幂等消费。半途失败的 assessment 可以
保留在 trace，但不能进入长期学习事实。

清除 `trace.db` 不影响学习历史；清除 LearningFactJournal 必须是单独、明确且可审计的用户操作。

## Agent review exports

允许从 SQLite 生成可丢弃的本地审查包：

```text
manifest.json
learning-facts.jsonl
summary.md
```

- JSONL 按 source cursor 稳定排序，每行一个带 schema/taxonomy/projection version 的结构化事实；
- Markdown 只生成面向人的汇总，并引用稳定 ID，不复制成新的规范；
- manifest 记录导出范围、source cursor、redaction profile 与内容 hash；
- 导出默认留在本机 `~/.grandquiz/exports/`，不是备份或导入真相；
- system prompt、密钥、完整工具 payload 和非必要正文不得进入导出；
- 只有用户显式批准、完成脱敏的最小样本才能进入 `src/grandquiz/evals/fixtures/`。

## Consequences

- `learning.db` 增加版本化 journal/outbox migration 与白名单 payload schema。
- `trace.db` 与学习历史拥有独立的数据保留和清除语义。
- AssessmentAttempt、LearnerProjection 和 Agent 审查导出可以在不读取完整 Trace 的情况下重建。
- 同一事实会有完整 Trace 投影和精简 Journal 投影，但领域事件只发一次，业务状态转移逻辑不复制。
- 实现前必须用故障注入证明 ledger commit、outbox 与 journal projection 不产生半状态。

## Rejected alternatives

- **只依赖 trace.db**：数据保留与敏感运行审计耦合，无法安全清理。
- **直接把 AssessmentAttempt 当写侧表**：形成第二个判卷事实入口。
- **把 Markdown / JSONL 当运行时存储**：缺少事务、迁移、约束和可靠并发语义。
- **把完整 Trace 复制进 learning.db**：扩大敏感数据面，违背白名单最小化原则。

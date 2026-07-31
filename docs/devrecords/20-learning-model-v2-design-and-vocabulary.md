# Learning Model v2：数据契约与受控词表设计记录

Date: 2026-07-30

Status: design accepted; implementation not started

## 为什么做这一轮

现有项目已经能入库、对话、考核、记薄弱点和调难度，但数据主要服务“把当前 workflow 跑对”。要继续做
统计、错因、能力回归、用户纠正与数据飞轮，必须先回答：

- 一次考核事实如何长期保存并重建？
- 分类、标签和材料体裁分别属于谁？
- 模型建议在什么条件下才有资格影响产品？
- 完整运行 Trace 与长期学习历史是否应该同生共死？

本轮只设计契约与文档职责，没有创建 migration、API、UI 或生产 prompt。

## 最终结构

```text
材料原文 → ResourceRevision → DocumentNode → Evidence → KnowledgeItem
                                                 ├→ KnowledgeClassification
                                                 ├→ ApplicabilityAssertion
                                                 └→ TagAssignment

Assessment workflow → AgentEvent
                    ├→ TraceStore → trace.db（完整运行审计）
                    └→ LearningFactJournal → learning.db（白名单长期事实）
                                                └→ AssessmentAttempt
                                                      ├→ VerdictCorrection
                                                      ├→ AnswerDiagnosis
                                                      │    └→ Misconception
                                                      ├→ DemandValidation
                                                      └→ LearnerProjection
```

Learning Memory、DifficultyLedger 与 AskedQuestionsLedger 继续是确定性 operational state，不被
LearnerProjection、tag、diagnosis 或连续分数替代。

## Grill 后确定的关键决策

1. KnowledgeOrientation 是持久、多值、可审核分类；kind 只提供默认建议。
2. KnowledgeKind 增加 method。PageIndex 是 method；其原理是 mechanism；接入步骤是 procedure。
3. 一个 KnowledgeItem 暂时只有一个 primary kind；综合题在出题层串联多个 item。
4. managed tag 只经 TagAssignment 关联，不嵌入 KnowledgeClassification。
5. SourceGenre 属于 ResourceRevision，局部 code/table 属于 DocumentNode.node_type。
6. Applicability 是带 Evidence 的独立 item-level assertion；缺失表示 unspecified。
7. ReviewStatus 与 LifecycleStatus 分离；纠正追加 revision + supersedes_id。
8. AssessmentAttempt 是事件派生的物化读模型，不是第二个判卷写入口。
9. 申诉追加 VerdictCorrection，并按 final verdict 序列重算 Memory 与 Difficulty。
10. 单次 diagnosis 只形成 MisconceptionCandidate；持久误区需要用户确认或严格的纠正后复发证据。
11. CognitiveDemand 分为 intended 与 validated；独立、mask intended 的 DemandValidator 必须先校准。
12. 长期学习事实进入 learning.db 的 LearningFactJournal，完整 Trace 可以独立清理。

## 审查中额外纠正的字段混淆

- 题面 `QuestionFormat` 与追问 `QuestionStrategy` 分开；
- `InputModality`（text / voice）与 `AnswerFormat`（choice / natural_language / code）分开；
- 确定性选择题判卷也记录规则版本，不用 null 表示“没有 grader”；
- Attempt 只引用 active diagnosis / validation ID，不嵌入可修订副本；
- Learning Memory 的 None 映射为 not_in_memory，不冒充“从未考过”或“已经掌握”。

## 受控词表

词表分三层：

1. 封闭维度：影响契约和行为，必须修改 schema + Eval 才能新增；
2. managed terms：领域和技术词，稳定 key、alias、审核和废弃；
3. open candidates：模型/用户提出，但审核前不驱动行为。

机器 seed 位于 `docs/vocabulary.v1.yaml`。当前包含 9 个封闭维度与 8 个 proposed managed terms；所有
seed 保持 proposed，等待真实 KnowledgeItem Replay 和人工去重。

## Agent 审查归档

未来可以从 SQLite 生成本地审查包：

```text
manifest.json
learning-facts.jsonl
summary.md
```

JSONL 面向 Agent/Eval，Markdown 面向人；它们都可删除重建，不是事实源或备份。默认留在
`~/.grandquiz/exports/`，只有显式授权且脱敏的最小片段才进入 Eval fixtures。

## 建议实现顺序

1. 为现有 assessment trace 建离线 projector，验证 Attempt 字段能否全部从当前事件恢复。
2. 用真实 KnowledgeItem 建 classification Replay fixture，校准 kind/orientation 与碎片化。
3. 实现 LearningFactJournal migration + transactional outbox + 故障注入测试。
4. 把 approved classification / TagAssignment 接进入库审批。
5. 建只读 AssessmentAttempt / LearnerProjection API 与报告。
6. 建人工标注 Demand calibration set；通过门后才启用 DemandValidator。
7. 最后再让 approved diagnosis/demand 信号参与策略，并用 Eval 证明收益。

## 验证

- YAML 可解析；
- closed dimension key、managed term key 与 alias 无冲突；
- Markdown 本地链接检查通过；
- `git diff --check` 通过；
- 本轮未修改生产代码、数据库 schema 或 API。

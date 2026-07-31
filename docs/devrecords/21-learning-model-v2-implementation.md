# Learning Model v2：把“这次答题”变成可长期复盘的学习事实

日期：2026-07-30

这一轮没有给产品再加一个看起来聪明的分数，而是先把最重要的事实保存正确：问了什么、用户答了什么、
如何判卷、是否提前看过材料、后来有没有申诉，以及这些事实怎样重新算出当前薄弱状态。

## 为什么不能只靠 Trace

`trace.db` 像飞机黑匣子：它记录 prompt、工具调用、错误和 token，适合排障，但内容较敏感，也应该允许
用户单独清理。学习历史则像成绩档案，清掉运行日志后仍应存在。因此同一条事件脊柱现在有两个消费者：

```text
AgentEvent
├─ TraceStore → trace.db              # 完整运行审计，可独立清理
└─ LearningFactJournal → learning.db  # 白名单长期学习事实
```

Journal 不复制完整 Trace，只保存重建学习模型所需的最小字段。测试真实完成一次 Web 考核，关闭应用、
删除 `trace.db`、重新启动，仍能逐字读回同一个 `AssessmentAttempt`。

## 为什么要把四本账一起提交

一次判卷会同时影响 Memory、Difficulty、AskedQuestions 和 Journal。如果前三本成功、Journal 失败，用户
会看到状态改变却找不到原因；反过来也一样。现在四者都经过
`LearningStateWriter.commit_judgement()` 的同一个 SQLite transaction：

```python
with learning_db.transaction():
    asked_questions.record_asked(...)
    memory.record_verdict(...)
    difficulty.set_progress(...)
    learning_facts.append(committed_fact)
```

故障注入测试证明 Journal 写失败时其余账本全部回滚。事务提交后，outbox 再把白名单事实投回事件脊柱；
若进程恰好在两步之间退出，重启会续投，并按稳定 `event_id` 去重。

## Attempt 是投影，不是第五本账

`AssessmentAttemptV1` 由 Journal 纯函数重建。它同时保留：

- 初始判决与最终判决；
- 自适应题型与用户实际覆盖后的题型；
- text/voice 输入和 choice/natural-language/code 回答格式；
- 是否答题前揭示 Evidence 和耗时；
- 出题器、模型判卷或确定性 grader 的版本。

这样“答对一次”不再是一个孤立数字。例如提前看过 Evidence 的答对不会计入闭卷覆盖，确定性选择题会
明确记录 `multiple-choice-exact.v1`，不会伪装成某次模型判断。

## 申诉不改历史，而是重放历史

判错后不直接覆盖旧 verdict。系统追加 `VerdictCorrection`，保留 from/final verdict、revision、
`supersedes_id` 和 reconciliation 结果，再按该知识点全部 final verdict 从空状态重放：

```text
原判：错 → 纠正：对
              ↓
从空 Memory / 默认 Difficulty 重放 final verdict 序列
              ↓
得到唯一可解释的当前状态
```

相同 `request_id` 重试返回同一事实；若同一个 ID 携带不同内容则返回冲突，不会悄悄覆盖。

## 分类为什么分三层

1. `KnowledgeKind`、orientation、source genre 是封闭枚举，模型不能创造新值。
2. domain/technology 等 managed term 来自版本化受控词表，alias 指向同一个 term。
3. 未知自由词先进入 `TagCandidate`，审核前不驱动生产行为。

`method` 与 `mechanism`、`procedure` 已通过固定 Replay 样本建立确定性基线；PageIndex 归 method。入库
审批会在同一事务中提交知识快照与规则 classification proposal，但 proposal 不再冒充人工 approved。
分类、资源体裁和 TagAssignment 都带 revision/supersession，并以白名单事实进入 Journal 和审查导出。

## LearnerProjection 能回答什么

它从 Attempt 与获批 DemandValidation 重建每个知识点的：

- 考核次数和 final verdict 分布；
- 闭卷考核次数；
- 当前 Memory 状态与 Difficulty 档位；
- 已人工确认的 cognitive demand 证据。

`not_in_memory` 只表示当前 Memory 没有记录，不会冒充“从未考过”或“已经掌握”。投影可以删除重建，
也不会反向修改选题或判卷。

## 仍然刻意没有自动化的部分

公共 API 目前只接受用户本人进行 DemandValidation；客户端不能自报“校准 Judge”。自动 Judge 必须先有
人工标签、mask intended demand 的独立评测和受信校准版本。AnswerDiagnosis、持久 Misconception 以及
“用新指标改变下一题”也继续留在 Eval gate 之后。先保证事实可靠，再让系统变聪明。

## 收口后的 Module seam

领域审查后按真实依赖拆成四个职责明确的 Module：

```text
learning_facts.py        # LearningFactEnvelope + Journal Interface + SQLite Adapter
assessment_history.py    # assessment fact builders + Attempt/Demand/Learner projections
classification.py        # 纯分类契约 + 确定性 proposal 规则
classification_store.py  # SQLite revision/review + 合并 seed/本地词表
```

这不是新增四层抽象：调用方直接依赖所需的 Interface，没有兼容转发层。尚无消费者的 confidence、hint、
intended demand、diagnosis 与 span 引用退回 future-only 文档，不进入当前 OpenAPI。

本轮本地审查包可由 `grandquiz export-learning --db ... --out ...` 生成
`manifest.json + learning-facts.jsonl + summary.md`。它是可删除的审查视图，不是第三个数据库，也不会
被 runtime 自动读回。

## 本轮验收

- Python：`936 passed`；
- Learning Model v2 聚焦回归：`18 passed`；
- Web unit：`42 passed`；
- Playwright：桌面/移动端 `14 passed`；
- Ruff check/format、Pyright strict、import-linter、Web lint/typecheck 与生产构建全绿；
- OpenAPI 由当前后端重新生成，future-only 字段不再出现在浏览器契约中。

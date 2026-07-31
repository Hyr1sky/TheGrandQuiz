# 多题计划与可解释判卷收口

日期：2026-07-31

这轮修复来自两条真实反馈：

1. 用户说“两道选择、一道简答”，Web 实际出了三道选择题；
2. HTTP/1.0 简答已经答到主要现象，却被直接判错，参考答案也没有清楚解释本题到底缺什么。

它们表面上分别是题型和判卷问题，根上却相同：同一件事存在多份解释。

## 为什么 Web 会丢掉题型顺序

CLI 较早支持了 `segments`，会把分段展开成逐题序列；后来增加的 Web/FastAPI 仍只认识
`rounds + question_type`。Chat tool 收到混合题型后没有地方保存顺序，最终只剩“共三题”。

现在先生成唯一的 `AssessmentPlan`：

```python
plan = AssessmentPlan.create(
    rounds=3,
    question_type=None,
    segments=[
        QuestionTypeSegment(count=2, question_type="选择题"),
        QuestionTypeSegment(count=1, question_type="简答题"),
    ],
)

assert plan.question_type_intents == ("选择题", "选择题", "简答题")
```

CLI、Web Chat 与 FastAPI 都只消费这个序列。旧 HTTP 字段暂时可用，但 manager 会立刻把它们转成
`AssessmentPlan`，后面的 workflow 不再同时维护两种批次表示。

## 为什么“看起来答对”仍可能被判错

旧链路里有三份标准：

```text
出题：KnowledgeItem.summary + Evidence
判卷：题干 + KnowledgeItem.evidence
参考答案：整个 KnowledgeItem.summary + 全部 Evidence
```

出题模型可以根据摘要暗含一个要点，却只引用另一段 Evidence；判卷模型随后要重新猜“这题具体考什么”。
这使“漏了一个点”与“完全答错”很难稳定区分。

现在开放题必须产出一个 `QuestionSpec`：

```json
{
  "question": "为什么短连接会增加开销？",
  "expected_points": [
    {
      "point_id": "reconnect",
      "description": "指出每次请求都要重新建立连接",
      "cited_evidence": "..."
    },
    {
      "point_id": "handshake",
      "description": "说明重复握手带来额外成本",
      "cited_evidence": "..."
    }
  ],
  "reference_answer": "只回答这道题的参考作答",
  "cited_evidence": ["..."]
}
```

Grader 只能对这份评分点表逐项返回 `matched_points` 与 `missing_points`。代码再检查：

- 两组 point ID 不能重复或重叠；
- 合起来必须覆盖全部评分点；
- “对”必须全部命中；
- “勉强”必须既有命中也有缺失；
- “错”必须有缺失，且不能使用 `complete` 诊断。

LLM 仍负责语义判断，代码负责契约与记账。因此没有把开放问答硬改成关键词匹配，也没有让模型直接改
Learning Memory。

## 用户现在能看到什么

同一个 `ANSWER_JUDGED` 事件同时供 CLI 与 Web 投影：

```text
判断：勉强
答到了：指出短连接会重复建立
还缺：说明握手会产生额外成本
问题：方向正确，但遗漏了连接建立成本
```

参考答案来自本题 `QuestionSpec`；选择题则来自本题答案键。它不再泛化拼接整个 KnowledgeItem。
运行时诊断目前只进入 Trace 和题后反馈，不会提前升级成长期 `AnswerDiagnosisV1`，避免又造一个没有
完整消费者的数据模型。

## 如何防止再次漂移

- 共享领域接口：`AssessmentPlan` 与 `QuestionSpec`；
- adapter conformance tests：Chat 导航序列、FastAPI 逐题消费、Web 请求体、CLI 分段调度；
- 结构化校验门：评分点必须有 Evidence，判卷必须完整覆盖评分点；
- Web 的 `diagnosis` 只投影七个稳定类别，未知内部值降级为无诊断，不扩散成浏览器契约；
- OpenAPI 继续检查 HTTP 形状，但不再被误当作跨 interface 语义测试；
- prompt 变化后重新用真实 provider 录制 assessment 与 difficulty activation cassette。

这轮没有增加额外 LLM 调用：开放题仍是一次出题、一次判卷。新增成本只来自较小的结构化字段；后续
用真实样本跟踪 prompt token 增幅、重试率、人工纠正率与“勉强/错”分界质量。

## 收口验证

- Python：`940 passed`；
- Web unit：`44 passed`；
- Playwright：桌面/移动端 `14 passed`；
- ruff、format、pyright、import-linter、ESLint、TypeScript、生产构建与 Sites worker 全绿；
- OpenAPI 连续生成两次哈希一致，生成结果可重复。

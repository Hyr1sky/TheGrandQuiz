# v0.3 证据闭环：让分类、判卷与纠正真正产生用途

日期：2026-07-31
状态：代码 RC 完成；真实人工盲标校准门尚待样本，不能据此启用自动学习策略。

## 为什么做这一轮

Learning Model v2 已经能够保存分类、考核事实和判决纠正，但“保存了字段”不等于产品已经从中获益。
v0.3 刻意不继续扩展数据模型，而是为三份已有数据各接一个最小、可验证的消费者：

```text
人工批准的分类 ──→ 考核前筛选知识范围
人工盲标样本   ──→ 校准生产判卷器
用户纠正判决   ──→ 本地 Eval 候选
```

这三条路径共同回答一个问题：我们积累的数据能否帮助下一次考核更准确，而不是只让数据库更复杂。

## 1. 分类第一次驱动产品行为

新增的 `knowledge_facets.py` 是唯一的分类消费边界。它只接受同时满足
`review_status=approved` 和 `lifecycle_status=active` 的分类；模型自动生成的 proposal 不会静默影响出题。

筛选规则保持简单且显式：v0.3 的 Web 一次只选择一个 primary kind，并冻结为 exact 知识点 ID。
`KnowledgeOrientation` 继续作为可审核的领域分类保存，但在没有独立产品消费者前不进入考核请求契约。

Web 会先展示当前材料中已审核的类型及数量。如果没有匹配项，
系统在调用模型之前失败，不会偷偷回退到整篇材料。这让“用户指定了什么”与“模型实际看到了什么”一致。

## 2. 内部筛选不能污染模型契约

第一次实现把筛选后的 `item_ids` 放进了公开 `QuizScope`。类型上看很自然，却让 `start_quiz` 工具的 JSON
schema 发生变化，真实录制的 case14、case15、case17 Replay 随即失效。

问题本质是把两类职责混在了一起：

```text
公开 QuizScope        = 用户与模型共同理解的稳定意图
candidate_item_ids    = workflow 启动时冻结的内部执行集合
```

最终实现把 `candidate_item_ids` 作为 `AssessmentSession` 的内部参数传递，公开工具 schema 保持逐字节稳定。
这个调整不仅修复了测试，也把边界说清楚：模型描述“考哪份材料”，代码决定“这次允许抽到哪些已审核条目”。

## 3. 用人工盲标校准生产判卷器

`grading_calibration.py` 不复制一份 Eval 专用 prompt，而是直接调用生产 `grade_answer`。这样测到的是用户真正
使用的判卷链路，包括结构化输出校验、重试和 Token 消耗。

默认 gate 为：

- 至少 10 条符合资格的人工盲标样本；
- 三值 verdict 一致率至少 85%；
- 逐评分点判断准确率至少 90%；
- “人判对、模型判错”和“人判错、模型判对”的严重误判都必须为 0。

只有标注者在未看到模型输出时完成的样本才有资格打开 gate。用户看过模型判决后提交的纠正很有价值，
但它可能受到模型结论锚定，因此不能冒充盲标金标准。样本不足时报告明确返回
`insufficient_evidence`，测试数据也不能把它伪装成通过。报告分别统计 eligible blind 与 exploratory
样本的数量和 Token，eligible 平均成本不会和纠正样本混算。

运行入口：

```bash
.venv/bin/grandquiz calibrate-grading \
  --samples /path/to/human-blind-labels.yaml \
  --out /path/to/grading-calibration-report.json
```

## 4. 用户纠正进入 Eval 候选，而不是自动训练

Assessment API 现在把持久化的 `attempt_id` 返回给 Web。用户可在判卷卡片中选择新的三值结论、填写原因，
然后调用已有的 append-only correction 命令。

每次导出会从最新纠正确定性投影 `eval-candidates.jsonl`。候选保留题目、答案、模型初判、人类终判、原因和
版本信息，同时固定标记：

```json
{
  "blind_to_model_output": false,
  "release_gate_eligible": false,
  "privacy_review_required": true
}
```

因此反馈链是“纠正 → 本地候选 → 脱敏/审核 → 人工选择 Eval”，而不是“纠正 → 自动进入发布测试或训练集”。
多次纠正仍完整保留在 Journal 中；候选读模型只展示每个 attempt 的最新版本。

## 对架构的影响

- 没有新增数据库迁移；三个能力都是已有事实之上的读模型或内部执行约束。
- 分类 proposal 仍是建议，只有人工批准结果可以驱动产品行为。
- Eval candidate 可删除重建，不成为第二份事实源。
- 自动 Demand Judge、Diagnosis、Misconception、能力蓝图和自适应选题仍被质量 gate 阻挡。
- FastAPI 与 Web 通过同一个领域筛选函数保持口径一致；CLI 本轮只保持 Eval candidate 导出口径。

## 验收结果

- Python：`961 passed`
- Web unit：`47 passed`
- Playwright：桌面/移动端 `14 passed`
- Ruff lint / format、Pyright、Web lint / typecheck、生产构建全部通过
- OpenAPI 重新生成前后 hash 一致，不存在生成器漂移

下一步不是增加更多字段，而是收集 10–30 条真实人工盲标样本运行校准，查看一致率、严重误判、重试和
Token 成本。只有证据通过后，才讨论让自动判决影响更主动的学习策略。

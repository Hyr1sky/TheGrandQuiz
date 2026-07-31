# v0.2 功能 RC 收口

日期：2026-07-31

状态：功能代码与文档收口完成；包版本已进入 `0.2.0` 发布准备态，尚未创建 tag 或 GitHub Release。

## 这一版解决了什么

v0.2 的重点不是继续堆页面，而是让学习数据、材料证据和考核判决变成可解释、可纠正、可重建的契约。

### 1. 长期学习事实与运行 Trace 分开

- `LearningFactJournal + transactional outbox` 保存允许长期存在的学习事实；
- `AssessmentAttemptV1` 可由事件重建，不依赖完整 Trace 永久保留；
- 判决纠正采用 append-only 事实与确定性 reconciliation，不修改历史；
- `LearnerProjectionV1` 和稳定导出提供审查入口。

### 2. 分类先进入审核态，不提前驱动产品

- v1 受控词表、分类 proposal、TagCandidate/TagAssignment 与人工审核已经落地；
- 默认分类结果是 proposed，不直接改变检索、筛选或选题；
- 自动 Demand Judge、AnswerDiagnosis/Misconception 和知识关系继续受 Eval/消费者 gate 限制。

这遵守“先证明消费者再增加结构”：字段可以为未来保留在文档中，但没有产品收益证据时不升级成生产事实。

### 3. 材料与故障都能被精确定位

- 非代码 Markdown 的可见 Evidence 唯一映射回 raw source；
- Acquisition 失败统一为安全 `code / stage / reason` 信封；
- 失败进入事件脊柱和 Trace error 统计，并在 CLI/Web 管理态使用同一语义展示。

### 4. 考核意图与评分标准只有一份

`AssessmentPlan` 保存每一轮的题型意图：

```python
AssessmentPlan.create(
    segments=[
        QuestionTypeSegment(question_type="选择题", count=2),
        QuestionTypeSegment(question_type="简答题", count=1),
    ]
)
```

CLI、Web Chat、FastAPI 和 React 只传递、消费这个有序计划，因此“两道选择题加一道简答题”不会再在
adapter 间退化成三道默认选择题。

开放题使用 `QuestionSpec` 把题目、评分点、Evidence 和题目级参考答案绑定在一起。Grader 逐项返回
`matched_points` 与 `missing_points`，代码检查它们完整覆盖评分点并与“对 / 勉强 / 错”一致。长期学习
记账仍只消费最终 verdict；运行时 diagnosis 只服务 Trace 和题后反馈。

## 防止再次漂移

- 领域契约：`AssessmentPlan`、`QuestionSpec`、长期事实 schema 和受控词表；
- adapter conformance tests：CLI、Chat navigation、FastAPI 与 React 请求体；
- 稳定公开投影：Acquisition error 和 Assessment diagnosis 使用有限类型，未知内部值安全降级；
- CLI、ReAct 与 Web Acquisition 共用“快照 + proposed 分类”的原子提交端口；
- 相同 classification `request_id` 携带不同内容时明确返回冲突；
- Acquisition run 终态与 failure stage 在同一事务中落库，避免半状态；
- 学习事实时间来自注入 Clock，不再用 revision 伪装时间；
- OpenAPI 只负责 HTTP 形状，跨 interface 语义由行为测试负责；
- Prompt 变化必须重录真实 provider cassette，并保持默认 Eval 完全离线。

## 收口验证

- Python：`947 passed`；
- Web unit：`45 passed`；
- Playwright：桌面/移动端 `14 passed`；
- ruff、format、pyright、import-linter、ESLint、TypeScript、生产构建和 Sites worker 全绿；
- Eval：`17/17`，默认只使用 Replay；
- OpenAPI 连续生成哈希一致。

本地 Playwright 过去会在浏览器 revision 变化或缓存清空后重新下载约 170 MiB Chromium。现在本地默认
复用已安装的稳定版 Chrome，CI 仍显式安装固定 Chromium；需要验证固定 Chromium 时可设置
`GRANDQUIZ_SYSTEM_CHROME=0`。

## 明确没有进入 v0.2 的内容

- 完整资源、revision 和知识点管理；
- 批量入库与 Reader batch 并发；
- 自动 Demand Judge 和长期 AnswerDiagnosis/Misconception；
- 主动复习排期、知识图谱、ASR 数字人、多用户和公网托管。

下一阶段应先做真实样本校准和消费者实验，再决定 v0.3 的功能范围。功能 RC 完成不等于正式发布：
版本号、安装包、tag、Release Notes 和 GitHub Release 仍需要独立发布动作。

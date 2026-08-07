# 判卷 Evidence 契约可靠性补丁

日期：2026-08-03
阶段：v3.2 工程收口与真实开发回归完成

## 一句话蓝图

模型继续负责“这个评分点在语义上有没有答到”，代码继续负责“它给出的证据是否真在学习者原文里”。
本补丁不放松证据门，而是消除会诱导模型伪造引文的 Prompt 冲突，并把结构可用性从准确率中拆出来。

```text
QuestionSpec + learner answer
             │
             ▼
        production Grader
             │
     ┌───────┴────────┐
     │                │
连续原文校验通过   契约失败 / Provider 失败
     │                │
三值代码聚合       不产生合法判决
     │                │
语义质量指标       合法输出率 / failure_kind
     └───────┬────────┘
             ▼
 GradingCalibrationReport v4
```

## 故障是什么

真实 holdout 的 `GQ3-H10` 连续三次返回完整 JSON，但 `mechanical_guard` 的
`answer_evidence` 都把答案里的前后句改写或用 `...` 拼接。它们不是学习者答案中的连续精确子串，
生产校验门因此拒绝三次输出，最终没有合法判决。

这不是 API 的输出 Token 截断：三次响应都有闭合 JSON 和后续字段。Pydantic 也允许最多 200 字，
实际引文没有撞到这个上限。真正的冲突在 Prompt：一边要求“逐字原文”，一边又建议“最短、80 字以内”。
当模型想同时引用相隔较远的机制和 CI 后果时，它把 80 字理解成压缩目标，主动加入省略号。

H10 其实有合法短证据，例如：

```text
每次 PR 触发检查，违反则阻断合并
```

所以问题不是 rubric 无法举证，而是输出指令和失败反馈不够清楚。

## 补丁做了什么

### 1. Prompt 取消错误优化目标

`answer_evidence` 不再给出 80 字建议，改成四条同一优先级的规则：

- 必须逐字取自学习者答案；
- 必须是一段连续原文；
- 优先选择能独立支持评分点的最短片段；
- 不得改写、使用省略号或拼接不相邻片段。

“短”现在只是连续精确之后的次级偏好，不再能覆盖真实性。
当前模板内容版本为 `answer_grade@9baac9e9`；模板变化会自然使旧质量 cassette 失效。

### 2. 重试反馈从泛化提醒改为可操作纠错

旧反馈只说某个 point 的 Evidence 非法。新反馈会带回 `point_id` 和 JSON 转义后的非法值，明确要求
重新选择一段连续原文，并再次禁止省略号、改写与拼接。确定性测试模拟模型第一次返回
`"闭包能...外层变量"`，第二次读取反馈后改为 `"闭包能捕获外层变量"`，两次调用即可恢复。

代码仍不会用模糊匹配替模型改证据。否则一个语义上不支持评分点的近似句也可能被“修”成 matched，
审计链会失真。

### 3. Report v4 拆开可用性与质量

每条结果新增：

- `output_valid`：是否拿到通过生产契约的完整判决；
- `failure_kind`：`grading_contract` 或 `provider_or_runtime`。

报告新增 eligible 口径的合法输出数、非法输出数和合法输出率。`point_accuracy` 与
`verdict_agreement` 只在合法输出上计算；gate 另外要求 eligible 非法输出数为 0。

例如一条判得全对、另一条结构失败时，报告应呈现：语义准确率 100%、合法输出率 50%、整体 gate failed。
它不会再含糊地写成“准确率 50%”，也不会因为只统计合法输出而错误开门。

v2/v3 历史报告仍可读取；新增字段对旧报告为未知值，不伪造历史测量。

## 验证结果

- 新增 Prompt 冲突与省略号重试回归测试；
- 新增合法/非法混合 cohort 的 Report v4 口径测试；
- Experiment comparison 同步展示合法输出率；
- 两份行为 Replay fixture 在确认原响应仍满足更严格契约后迁移请求指纹，全程零网络；
- Python：`1008 passed`；
- Ruff lint、Ruff format check、Pyright、import-linter 与 `git diff --check` 全绿。

行为 fixture 只证明代码路径可离线回放。其历史 Token usage 不用于新 Prompt 成本比较，也不能替代真实
Provider 复测。

## 真实模型复测

经 owner 明确授权，使用同一已揭盲 Snapshot、`deepseek-v4-pro / Thinking Off` 和
`answer_grade@9baac9e9` 先运行 H10 单题探针，再运行完整 12 条开发回归。H10 第一次仍使用 `...`
拼接不相邻原文，说明 80 字建议只是诱因，不是唯一原因；新的具体 retry 在第二次使 Evidence 合法，
并把语义误判的 `actionable_remediation` 修正为 missing，最终 4/4 point 与人工一致。

| 指标 | v3.1 | v3.2 |
| --- | ---: | ---: |
| 最终合法输出率 | 91.67% | **100%** |
| 首轮合法输出率 | 91.67% | 91.67% |
| 仅合法输出逐点准确率 | **75.00%** | **75.00%** |
| 报告三值一致率 | 58.33% | 58.33% |
| Serious FN / FP | 3 / 0 | **2 / 0** |
| Retry | 2 | **1** |
| Total Token | 22,037 | **20,990** |

v3.2 报告显示 75%，而 v3.1 历史报告是 68.75%，但这不是语义提升：旧 v3 把 H10 无效输出的四点
全部计错；统一成“只看合法输出”后，旧版也是 33/44 = 75%，新版为 36/48 = 75%。H05 有一个漏判
恢复，H02 同时新增两个过度推断，语义漂移互相抵消。单次运行还包含 Provider 非确定性，不能把这些
变化都归因于 Evidence Prompt。

完整 cassette 含 13 个响应，零网络 replay 删除 `latency_ms` 后与 live report 逐字段一致：

- Live Report：`511b677859dd1d7a3975d7ecd80567b30467f3a18dc67f445f9c3df79e56fdef`；
- Replay Report：`1a1672af377b6bb39e640de6d50aa58fb5773ca2dc5b0d8d3b33d6aee46a598b`；
- Cassette：`8afe50a575d25a591b6e7b5d5274b6f40425f9f1cd7a5f25051d7878d1754aaa`。

## 还不能宣称什么

现在可以宣称“最终结构可靠性恢复、重试成本下降、指标口径不再混淆”，但不能宣称首轮 Evidence
复制已经稳定，也不能宣称语义质量提升。这个 Snapshot 已揭盲，v3.2 的完整状态仍是 `failed`。

下一步顺序是：

1. 把自由复制 Evidence 改为代码生成候选 span、模型只选择 ID 的方案先做小型 prototype；
2. 继续收窄 acceptance semantics 与示例边界，重点防止 H02 式脑补；
3. 不再在本 cohort 上刷 Prompt 分数；
4. 冻结另一批从未见过的真实 blind holdout，作为唯一 release gate。

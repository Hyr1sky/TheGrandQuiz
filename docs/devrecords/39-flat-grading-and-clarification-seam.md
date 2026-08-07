# 39. flat 判卷基座回撤与一次性澄清 seam

日期：2026-08-06

## 一句话结果

Required Claims 已退出新题与默认判卷路径，flat atomic ExpectedPoint + AnswerEvidenceUnit ID 恢复为基座；
一次性用户澄清建立了纯领域 planner/state machine，但现有 30 条生产报告中 `uncertain=0`，所以没有接入
AssessmentSession、CLI/Web 或 Learning Memory。

## 回撤了什么

- `question_generate` / `question_probe` 不再要求或示例 `required_claims`；
- 新题解析不再要求 claims；显式或历史 Provider 响应若携带该字段仍原样读取；
- 新题因此稳定选择 flat `answer_grade`。

没有回撤的可靠性成果：critical points、AnswerEvidenceUnit ID、精确答案原文解析、代码三值聚合、
Calibration Report、Dataset provenance、cassette 与 replay。显式载入的历史 claim-aware QuestionSpec 仍可
读取和回放，避免破坏开发证据。

## 澄清 seam

`plan_clarification(question, verdict)` 是纯领域 Interface：只有 `diagnosis=uncertain`，且把某个 missing
point 假设为 matched 会改变代码三值时，才返回一个稳定 `ClarificationRequest`。critical missing 优先，
同档按 QuestionSpec 顺序。

不可变 `ClarificationFlow` 只允许：

```text
awaiting_clarification
  → ready_to_regrade
  → resolved | needs_review
```

它只接受一次补充，保存原答与补答，生成版本稳定的合并重判输入；第二次补充、重复 finish 和空白补答均
被拒绝。它不依赖 Provider/Store/Memory/FastAPI，未来若 gate 通过，由 AssessmentSession 在记账前调用。

## 为什么没有继续接生产

对 Holdout 03 已揭盲 30 条真实生产报告做零网络审计：

| diagnosis | 数量 |
| --- | ---: |
| complete | 13 |
| missing_key_point | 15 |
| off_topic | 2 |
| uncertain | 0 |

五个三值分歧也全部被模型确定地报成 missing/off-topic。当前 planner 因而触发 0/30；若按 reason、Gold
disagreement 或字符串启发式强行触发，就等于在生产代码里偷看开发集并重新制造隐藏 Judge。

## 下一步 gate

先在 Development Gold 上单独验证一种版本化“不确定性识别”输出，报告 coverage、clarification rate、
resolution rate、needs-review rate、serious error 和 Token。未取得新的真实调用授权前不运行 Provider；
候选未在开发集胜出前不收集新 holdout，也不实现 API/UI/记账延迟。

后续真实结果见 [判卷澄清二分类原型](40-clarification-signal-prototype.md)：原型未过语义门，并证明
grading Gold 不能替代独立 Interaction Gold。

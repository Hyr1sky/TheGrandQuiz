# 42. 用户主动补充与判卷申诉竖切

日期：2026-08-07

## 一句话结果

自动澄清实验没有过门，因此产品改走更保守的人工入口：开放题判卷后，学习者可主动提交一次补充说明；
系统保留原答，用同一 `QuestionSpec` 和同一 Grader 对“原答 + 补充”重判，再通过既有追加式
`VerdictCorrection` 重放 Learning Memory 与 Difficulty。

## 为什么不是再加一个 Judge

前两轮原型说明模型不擅长稳定地区分 `ambiguous_support`。本竖切不猜“用户是不是表达含糊”，也不自动
追问。触发信号就是用户点击按钮：模型误解答案时，用户最清楚自己是否需要解释。

```text
初次作答（不可变）
        │
        ▼
初次判卷 ── 用户无异议 ──► 下一题
        │
        └─ 用户主动补充（最多一次）
                    │
                    ▼
          原答 + 补充的稳定文本
                    │
                    ▼
             同一 Grader 重判
                    │
                    ▼
       VerdictCorrection 追加事实
                    │
                    ▼
       代码重放 Memory / Difficulty
```

## 职责如何深化

- `assessment.appeal.AppealSubmission` 只维护不可变原答、一次补充和唯一重判文本，不判断歧义。
- `VerdictCorrectionService` 从 FastAPI 路由中接管幂等检查、revision、事实追加和跨账本事务；人工直接纠错
  与补充重判现在共用它。
- `AssessmentManager` 冻结本轮 `QuestionSpec`、原答和语言，异步运行重判；独立 appeal 状态不会篡改
  已经闭合的考核终态。
- FastAPI 只接受 command、映射 404/409；Web 只展示 `available / grading / resolved / failed` 有限状态。

## 关键不变量

1. `AssessmentAttemptV1.answer_text` 始终是第一次回答，补充另存为 `supplemental_answer`。
2. 相同 request id 与相同补充可重试；不同的第二次补充返回 409。
3. 选择题没有补充入口；开放题才保留可重判的 `QuestionSpec`。
4. 重判不直接改 Memory；只追加 correction，再由确定性代码从全部 final verdict 重建状态。
5. 自动 ambiguity classifier 和 direct-support abstention 继续关闭。

## 验收证据

- 新领域测试覆盖原答不可变、稳定合并文本、空补充拒绝和单次限制。
- FastAPI 集成测试覆盖 `错 → 对`、幂等重试、第二次冲突、追加事实与状态 reconciliation。
- 当前主界面的 `AssessmentPanel` 测试覆盖入口、一次提交、重判结果与入口关闭。
- 定向 Python 回归：53 passed；发布收口后的全量 Python：1034 passed；Web unit：49 passed；
  Playwright：20 passed（新增桌面/移动端真实申诉链）；
  Ruff、Pyright、import-linter、Web typecheck/lint/build 全绿。

这是一条产品交互闭环，不代表自动澄清模型已经通过质量门。

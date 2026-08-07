# 37. Required Claims 真实开发集原型

日期：2026-08-06

## 一句话结果

受限 Required Claims 把输出结构做可靠了，却没有把判卷语义做可靠：12/12 输出合法，但三值只从 7/12
提升到 8/12，逐点仍为 37/48，并新增六个逐点分歧；29,400 Token 比 19,512 baseline 高 50.68%。
预注册实验失败，因此暂停生成下一批未见人类 holdout。

## 测了什么

在已经揭盲的 12 条 Holdout 03 Development Gold 上，保持原问题、答案、ExpectedPoint、Evidence、人工
point 标签和 critical points 不变，只为 48 个 point 增加 93 条人工冻结的 `required_claims`。生产
Grader 自动走 `answer_grade_claims`：模型逐 claim 选择 AnswerEvidenceUnit ID，代码固定 all-of 推导 point。

Provider 固定为 DeepSeek V4 Pro / Thinking Off。成功条件在调用前冻结：至少修复五个目标 verdict 中的
四个、零新增逐点错误、12/12 合法、无 serious error，并且 Token 增幅不超过 15%。

## 得到了什么

| 项目 | baseline | required claims |
| --- | ---: | ---: |
| verdict agreement | 7/12 | 8/12 |
| point accuracy | 37/48 | 37/48 |
| valid output | 12/12 | 12/12 |
| serious FN / FP | 0 / 0 | 0 / 0 |
| retry | 0 | 1 |
| total tokens | 19,512 | 29,400 |

H01 与 H08 被修复，H06/H18/H20 仍失败。模型即使面对更短的 claim，仍漏掉“状态快照 + 新会话”等价于
checkpoint recovery，以及“路径越具体优先级越高”等价于 nearest-rule precedence。与此同时，固定 all-of
把 H06/H14 等旧 Gold 原本整体接受的回答判得更严。

## 学到的架构知识

Required Claim 不是 few-shot，但它也不是确定性真值。代码只能保证：claim 不重不漏、Evidence ID 有效、
all-of 计算正确；它不能保证人写的 claim 没有比原 point 更严格，也不能保证模型正确识别同义蕴含。

因此这条 seam 的真实价值目前是“让 rubric 歧义和模型决定可观察”，不是“自动提高准确率”。结构可审计性
和语义质量必须分别设门，不能因为 Pydantic 与 replay 全绿就进入新 holdout。

Token 放大的主要来源也不是输入里的 93 句话本身，而是输出要求每条 claim 都携带 label、Evidence ID、
reason，同时 point 再重复 label/reason。若继续实验，应优先删除可由 Evidence 解释的逐 claim 自由文本理由，
而不是压缩 Evidence 或放宽结构校验。

## 下一步边界

当前不运行新 release holdout，也不启用自动判卷策略。一个可检验的后续候选是“紧凑 claim 输出 + 只对
会改变三值的 missing claim 做聚焦复核”：第一阶段只返回 claim label/Evidence ID，代码找出影响最终三值的
missing claim；第二阶段只看该 claim 和完整答案 Evidence，复核同义、组合和等价机制。它是否值得做仍需
新的 Development Gold preregistration，并同时约束新增 false positive、调用次数和 Token。

本地 primary evidence 位于 gitignored 的
`localtemp/calibration/grading-required-claims-prototype-01/`；其中含冻结 claims、preregistration、真实
cassette、live/replay report、summary 与详细错误解释。

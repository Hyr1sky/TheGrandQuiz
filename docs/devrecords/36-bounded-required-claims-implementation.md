# 36. 受限 Required Claims：从 Prompt 猜测变成可校验数据

日期：2026-08-04

## 一句话结果

新 QuestionSpec 的每个 ExpectedPoint 现在带 1–3 条原子 `required_claims`。模型逐 claim 绑定学习者
答案 Evidence，代码按固定 all-of 推导 point，再沿用 critical point 规则聚合“对 / 勉强 / 错”。旧题不
补字段、不改消息，继续走旧判卷路径。

## 它不是 few-shot

Few-shot 是把若干示例写进 Prompt，让模型模仿风格；模型仍可自行理解边界。Required Claim 是题目数据：

```json
{
  "point_id": "explicit_exception_fallback",
  "description": "只在明确异常条件下切换 OCR",
  "required_claims": [
    "先检测普通文本提取是否失败并确认扫描件",
    "只有确认扫描件后才触发 OCR"
  ]
}
```

Grader 必须分别返回 `point.claim_1` 和 `point.claim_2` 的 matched/missing 与答案 Evidence ID。代码检查
两个 ID 都出现、引用真实答案单元，并且只有两条都 matched 才把 point 判为 matched。因此它是软语义判断
外面的硬结构契约。

## 兼容路径

```text
历史 QuestionSpec（无 claims）
  └─ answer_grade → point Evidence → 原逻辑

新 QuestionSpec（全部 point 有 claims）
  └─ answer_grade_claims → claim Evidence → code all-of → point → critical 聚合
```

同一道题不能一部分 point 用新契约、一部分用旧契约。旧对象读取时 `grading_claims` 会回退到
`description`，但序列化会省略空字段，因此既有 Snapshot 内容哈希不会被偷偷改写。

## TDD 与回归

本轮从公开 `QuestionSpec → grade_answer` seam 写 RED：字段原本会被 Pydantic 静默忽略；新生成题原本
不会拒绝缺失 claims；判卷原本不会保存 claim Evidence，也不会做 all-of。实现后又锁住：claims 去重、
新旧模式不可混用、claim 输出按题目顺序规范化、claim-aware prompt/version 进入 Calibration Report。

所有确定性测试 Provider 已升级为新题契约。两份真实来源 Replay fixture 没有调用模型重录，而是机械保留
原题、判决与 usage，只增加单 claim 和对应 Evidence 映射，再按新消息重新计算 replay key；因此它们仍只
证明行为可回放，不声称新 Prompt 的真实质量或成本。

## 仍未证明的部分

Holdout 03 的失败结论不变。这批数据已经揭盲，也没有 required claims，不能事后补字段再宣称通过。
下一步要用新契约生成新题、人工审题并收集新的独立人类答卷。只有新的 unseen release holdout 同时通过
三值一致率、逐点准确率、合法输出率、严重错误和 Token 门，才能决定保留本设计。

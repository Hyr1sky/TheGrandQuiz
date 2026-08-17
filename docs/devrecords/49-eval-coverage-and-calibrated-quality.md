# 49. Eval E5–E6：覆盖身份与多维质量校准

## 为什么做这一轮

v0.5.0 已有 17 个 Harness cases、Tier-1 deterministic graders、case15 Tier-2 Judge、
Grading Benchmark、Trace 与 Replay，但维护者仍难以从一个可信入口回答三个问题：覆盖了哪些产品能力、
报告评测的是哪套完整系统组合、语义 Judge 是否先复现了人类边界。

本轮先完成 Eval-guided Evolution 的 E5–E6。它不自动修改 prompt，不把已见样本重新包装成 Holdout，
也不让一个通用总分替代确定性规则门。

## E5：Coverage 与 Subject Identity

- 用闭集 `EvalSurface` 描述 acquisition、reader/grounding、grounded answer、question generation、
  answer grading 与 learning-state transition；新增未分类 case 会在执行前失败。
- Harness cases 与 Grading Benchmark 在同一 coverage report 中可见，但保留各自执行类型和指标语义。
- `EvalSubjectSnapshot` 冻结 prompt、Provider/model/thinking、工具 schema、预算、重试和策略版本，使用确定性
  canonical hash；凭证、完整 prompt、结果指标和 cassette completion 不进入 subject identity。
- Replay cassette identity 与 subject identity 关联但不混同：Replay 证明可复现，不证明新模型泛化。

## E6：从候选门到独立质量套件

生产 Grader 的 Holdout 03 已揭盲，只能作为 Development Gold。对比 gate 要求同 Dataset Snapshot、
同 Provider 和同指标，并同时检查已知修复、protected negatives、结构合法率与 token 增长。已录制的候选
没有跨过预注册门，因此被诚实拒绝，生产 prompt 保持不变。

语义质量没有合并成一个万能 Judge，而是按消费者拆为三套版本化 rubric：

| Suite | Development Gold 边界 | Rubric | 真实 calibration |
|---|---|---|---|
| Question Quality | good / partial / leaked / unsupported / misleading | `question_quality@v1` | 1.000 / 1.000，6,573 tokens |
| Reader Fidelity | supported / missing / duplicate / pseudo-item / cross-node | `reader_fidelity@v3` | 1.000 / 1.000，6,775 tokens |
| Grounded Answer | multi-material / refusal / conflict / bilingual / incomplete | `grounded_answer@v2` | 1.000 / 1.000，4,869 tokens |

表中的两个数依次是 agreement 与 exact agreement。每套 pack 都有唯一 registry 条目、rubric/version、
冻结内容哈希、独立录制脚本、cassette 与 token 成本；新增或孤立 YAML 不会自动获得执行资格。

## Rubric 校准中学到的东西

真实 Judge 首轮暴露的主要问题不是 JSON schema，而是维度串扰：遗漏会连带降低 source fidelity，重复会被
忽略在 learning usefulness 之外，练习要求与事实陈述的模态差异也可能被混淆。修复没有移动 owner 标签，
而是明确每个 rubric 的职责：

- Reader 的 support、coverage、separation、locality 和 usefulness 分别扣分；“请设计 X”不能支持
  “系统已经使用 X”。
- Grounded Answer 的 support 只评已经陈述的主张，coverage 单独评是否答全；材料沉默时有依据地拒答是
  完整 grounded 行为。
- Judge 仍必须返回结构化结果，并逐字引用 candidate/reference；任何 pack 未经 owner adjudication、
  rubric 不匹配、边界不完整或 ReplayMiss 都 fail closed。

`grounded_answer@v2` 改变了请求指纹，因此既有四样本 calibration 与 case15 Tier-2 cassette 也经单独
授权真实重录：四样本 calibration 为 1.000 / 1.000、3,706 tokens，case15 规则门与质量门均通过，
Judge 使用 1,071 tokens。

## 数据边界与验证

Question、Reader、Grounded Answer 三组人工标签均为 **Development Gold only**。它们可以校准 Judge、
回归已知边界和比较开发候选，但不能充当新的 Release Holdout、授权生产晋升或触发自动自进化。

最终门禁：

- Python：1,111 passed；
- Ruff lint / format、Pyright strict、import-linter：全绿；
- 三套真实 calibration 与 case15 普通回归均由 packaged cassette 离线 Replay；
- cassette 敏感字段扫描未发现 API key、Authorization、base URL 或外部 URL。

下一步进入 E7：在同一 immutable snapshot 和 suite policy 上构造 baseline/candidate 配对实验，分别报告
validity、quality、cost、latency、retry 与 stability，并用 worse / mixed / better 候选证明系统能拒绝、
保持 undecided 和识别可进入新 Holdout 的候选。

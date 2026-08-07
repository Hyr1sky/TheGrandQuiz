# ADR-0011: 用受限 Required Claims 固化评分点接受条件

- 状态：实验 seam 保留，默认策略已否决并完成代码回撤
- 日期：2026-08-04

## 背景

Holdout 03 的 30 条人类答卷达到 90.83% 逐点准确率，但三值一致率 83.33%，低于 85% 发布门。五个
改变三值的错误都是单点假阴性：组合表达、等价机制、状态交接或不同组件名已经满足语义，Grader 却把
参考实现措辞当成了隐性标准。

已揭盲 Development Gold 上的四种 Prompt/输入原型没有一个同时满足召回、精确率和 Token 预算。
之前的任意 nested `all_of/any_of` 原型又产生结构重试和显著 Token 放大。单段
`ExpectedPoint.description` 因而仍然过载：既给人解释评分点，又让模型猜里面有哪些不可省略的条件。

## 决策

ExpectedPoint 继续保持扁平，但新题必须为每个 point 提供 1–3 条 `required_claims`。这些 claims 只允许
固定 all-of：模型逐 claim 判断并选择 AnswerEvidenceUnit ID，代码校验覆盖和 Evidence 后推导 point
label，再按 critical points 推导最终三值。

`description` 保留为人类可读概括；可分别计分或承担不同题意的条件继续拆成不同 ExpectedPoint，不得
藏进 claims。claim ID 由 point ID 和稳定顺序确定，模型不自行命名。claim-aware Grader 不接收完整
参考答案，避免示例实现成为隐性 rubric。

历史 QuestionSpec 没有 claims 时继续走原 `answer_grade` 和 point-level Evidence 契约，序列化不补空
字段；新题走独立、版本化的 `answer_grade_claims`。同一道 QuestionSpec 不允许混用两种模式。

## 备选方案

- **继续调单次 Prompt**：开发集能提升召回，但都会新增逐点错误，已被原型否证。
- **保持纯 flat point，不加字段**：结构最轻，但无法把人类说明与机器可核验的必要条件分开。
- **任意 Boolean rubric tree**：表达能力更强，但会自然长出 any-of、threshold 和 exception；既有原型
  已出现结构失败、重试和 Token 放大，不适合个人本地产品当前规模。
- **每题再调用第二个 Judge**：增加成本和延迟，且只是让第二个模型重新猜同一份含混 rubric。

## 后果

好处是同义/组合表达与“缺一个必要条件仍不能命中”进入同一份数据契约，报告可审计到 claim Evidence，
旧 Snapshot 和 cassette 仍有明确兼容路径。代价是新题与新判卷输出更长，QuestionSpec schema、Prompt、
Replay fixture 和 OpenAPI 都要同步；出题质量也必须避免把示例误写成 claim。

这只是生产候选，不代表质量门已通过。Holdout 03 已降级为 Development Gold；必须用新契约生成全新题目、
收集独立人类答案并冻结新 holdout，才能决定是否保留该设计。若新 holdout 没有稳定提升，或平均判卷 Token
显著超预算，应撤回 claims，而不是扩大 Boolean 语言。

## 2026-08-06 验证补记

在消耗新 holdout 前，先用 12 条已揭盲 Development Gold 做了预注册真实原型。93 条 claims 的结构输出
12/12 合法且可完整 replay，但 verdict 仅从 7/12 到 8/12、point 仍为 37/48，新增六个逐点分歧；Token 从
19,512 增至 29,400（+50.68%）。因此本 ADR 的 schema seam 只能视为**可审计实验能力**，尚未成为通过
质量门的默认判卷策略；暂停创建新的 release holdout。

失败同时否证了“只要把 description 拆细，同一个模型就会可靠识别同义蕴含”的假设。后续若保留 seam，
必须先在 Development Gold 证明更紧凑的输出或聚焦 missing-claim 复核能同时改善召回、控制 false positive
和 Token；否则按本 ADR 原定退出条件撤回，而不是继续增加 claim 数量或 Boolean 表达力。证据见
[Required Claims 真实开发集原型](../devrecords/37-required-claims-development-gold-prototype.md)。

## 2026-08-06 退出决定

按上一节提出的最后一个有界候选，又预注册并真实运行了“紧凑 claim 输出 + 只对会改变三值的 missing
point 做聚焦复核”。紧凑首阶段在 owner rubric audit 后的 43 个 aligned points 上得到 37/43、9/12，
并解决 4/4 个高影响目标；5 次聚焦复核却没有修复任何错误，反而新增一个 point false positive，使最终
结果降到 36/43、8/12。17 次调用共 18,561 Token、零重试，结构和预算通过，语义单调性失败。

因此执行本 ADR 原定退出条件：Required Claims 只保留为兼容、审计和后续独立研究 seam，不作为默认
生产判卷策略；不继续叠加第三层 Judge、Boolean rubric 或阈值例外。下一次代码收口必须把新题生成与
默认判卷路径恢复为 flat atomic ExpectedPoint + 代码校验的 AnswerEvidenceUnit ID；在完成该回撤前，
现有 claim-aware 分支不得视为已通过的默认能力。证据见
[紧凑 Claims 与聚焦复核真实原型](../devrecords/38-compact-claim-focused-review-prototype.md)。

## 2026-08-06 代码收口

新题生成 Prompt 与解析门已恢复为 flat atomic ExpectedPoint，不再要求 Provider 产生
`required_claims`。字段、显式 QuestionSpec 载入、历史 Provider 响应与 `answer_grade_claims` 仍按原样读取，
保证旧 cassette/实验兼容。回撤不删除 critical points、AnswerEvidenceUnit ID、
三值代码聚合和 calibration/replay 基建。

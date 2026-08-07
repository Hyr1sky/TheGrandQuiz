# 判卷契约收窄与 DeepSeek 2×2 Pilot

日期：2026-08-03
状态：契约、审计、方向性实验与首轮确认 holdout 已完成；质量门失败，后续语义判卷收口见
[Grader 语义匹配收口](29-grading-semantic-matcher-closeout.md)。

## 一句话结论

这一轮没有用“换更大的模型”掩盖评分口径问题，而是先把职责拆开：**LLM 只判断每个评分点是否命中，
代码根据预注册核心点聚合“对 / 勉强 / 错”，报告同时保存原始判断与最终判断。**随后用同一批 10 条开发
样本比较 Flash/Pro × thinking 开/关。`deepseek-v4-pro + thinking off` 暂时最均衡，但样本已经用于开发，
不能再充当发布盲测集。

## 全局蓝图

```text
密封 QuestionSpec
  ├─ ExpectedPoint[]：模型逐项判断
  └─ critical_point_ids：出题时预注册，模型不可改
                ↓
production grade_answer
                ↓
模型输出：matched / missing / diagnosis / raw verdict
                ↓
代码校验完整分区并确定性聚合
  全命中 → 对
  零命中或缺任一核心点 → 错
  其余 → 勉强
                ↓
Verdict
  ├─ model_verdict：审计
  └─ verdict：产品唯一消费的 derived verdict
                ↓
Calibration Report v2
  ├─ 数据、Prompt、Provider、模型、thinking 身份
  ├─ 人工/模型逐点标签与诊断
  └─ 质量、严重误判、重试、Token、延迟
                ↓
固定 cohort 比较器（身份不一致即拒绝）
```

这进一步落实了“LLM 判断，代码记账”：模型仍处理难以硬编码的语义匹配，但不能用一个容易漂移的自报
verdict 直接改变 Learning Memory。

## 为什么原契约会漂移

第一次 19 条真实校准里，`GQ-A03` 与 `GQ-X03` 出现同一种现象：模型与人工对每个评分点的 matched/missing
划分完全相同，最终三值却不同。人工把“核心方向错误”判为错，而 Prompt 倾向把“命中一部分”判为勉强。
这不是简单的模型能力问题，而是“哪些点具有一票否决权”没有进入机器可见的题目规格。

新契约把核心性放入 `QuestionSpec.critical_point_ids`，而不放在 Grader 输出里：

```python
if not missing:
    derived = "对"
elif not matched or critical_points.intersection(missing):
    derived = "错"
else:
    derived = "勉强"
```

核心点必须在看见学习者答案前密封。旧 Snapshot 没有预注册核心点，本轮绝不根据已经看到的输出倒推补标；
因此这次 pilot 的主指标是逐点准确率，三值一致率只用于观察默认聚合的行为。

## Report v2 补了什么

每一题现在保存：

- 人工 verdict、matched/missing points；
- 模型自报 `model_verdict` 与代码 `derived_verdict`；
- 模型 matched/missing points、diagnosis、reason、cited Evidence；
- attempts/retries、prompt/completion/total tokens、latency、safe error；
- run manifest：endpoint host、model、thinking mode、reasoning effort、snapshot ID/hash、sample IDs、Prompt 版本。

报告刻意不保存 API key 和完整 Prompt。Provider 的 replay identity 也包含 model/provider/thinking/effort，避免
不同实验条件误用同一条 cassette；真实请求每成功一次立即 checkpoint，断线续跑时先本地命中再决定是否
调用 Provider。

## Provider 方言为什么要显式区分

旧配置把 `DISABLE_THINKING=true` 统一翻译为 Qwen 风格 `enable_thinking=false`。这对 DashScope 合理，但
DeepSeek V4 官方契约使用 `thinking: {type: enabled|disabled}`；第一次真实运行因此只能标为
`thinking_control_unknown`，不能科学地声称已经关闭思考。

现在 `api_dialect` 在 Provider 边界内处理：

- DeepSeek：`thinking.type`，可选 `reasoning_effort=high|max`；
- DashScope：`enable_thinking: boolean`；
- CLI 实验覆盖只改变非秘密模型参数，不修改 `.env` 或密钥。

## 固定 10 条开发样本

从 19 条已见样本中选出 7 条第一次 verdict 分歧样本与 3 条稳定锚点，固定为：

`GQ-X02, GQ-X04, GQ-X03, GQ-H04, GQ-A03, GQ-A04, GQ-C03, GQ-C01, GQ-C02, GQ-M02`

四个条件使用同一 Snapshot、同一内容哈希、同一 sample IDs 和 `answer_grade@111976c1`。比较器会拒绝任何
cohort 或 Prompt 漂移。所有真实报告与 cassette 位于 gitignored
`localtemp/calibration/v030-javaguide-agent/pilot/`。

## 2×2 真实结果

| 条件 | 逐点准确率 | 整题分区全对 | derived 三值一致 | 严重 FN/FP | Token | 平均延迟 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Flash / thinking off | 71.79% | 30% | 50% | 0 / 0 | 14,764 | 1.75 s |
| Flash / thinking high | **82.05%** | **40%** | 50% | 0 / 0 | 30,611 | 17.35 s |
| Pro / thinking off | 79.49% | **40%** | **60%** | 0 / 0 | 14,828 | 2.81 s |
| Pro / thinking high | 76.92% | 30% | 40% | 0 / 0 | 35,030 | 34.91 s |

四组都是 0 schema error、0 retry。报告 SHA-256：

- Flash/off：`b72c6f7c40b351eca4962f91a81236298084cdeb0fd212d2f4ac36d8cdfd32d2`
- Flash/on：`77efe807655c30dfaa9ddba9204c2557eb325c601a321aca2b5eedad58fa2249`
- Pro/off：`b0cb7574ade78fc6d7799265838a7990d225d8db38e94c9753c9c8cc337515de`
- Pro/on：`edfbfd3e3ae84847cee0f44bc1ce137a4ce1a63fc9ae92805b5c84604b1e347b`
- Comparison：`929ce9dfaf8489f36bae35a3fd1d3c7f654d0e116f2bc9cfb84ff6a49c9531ca`

随后在断网假设下用四份 cassette 各重放一次；除必然重新测量的本地 `latency_ms` 外，四份 Report v2 都与
真实运行逐字段相同，证明模型输出、usage、raw/derived verdict 与审计字段可以离线复现。

## 如何解读，而不是怎样“挑冠军”

1. Thinking 不是单调增益：它帮助 Flash，却让 Pro 在这 10 条上退化，并产生巨大的 completion token 和延迟。
2. Pro/off 相对 Flash/off 增加约 1.5 个百分点 Token，却提升 7.7 个百分点逐点准确率和 10 个百分点整题
   全对率；它是当前 Pareto 意义上最均衡的候选。
3. Flash/on 获得最高逐点准确率，但 Token 约为 Pro/off 的 2.06 倍，延迟约 6.18 倍，不适合作为同步判卷
   的默认值；可保留为争议样本离线复核候选。
4. Pro/on 同时更慢、更贵、质量更低，被其他条件支配，不继续投入。
5. 10 条样本很小，而且来自已分析过的开发集；这些数字只能选择下一轮候选，不能证明泛化质量或打开 gate。

## 下一轮确认性验证

1. 先密封新题目，QuestionSpec 同时预注册 ExpectedPoint 与 critical points；
2. 题目冻结后再收集独立答案，答题者仍看不到 rubric；
3. 人工终审逐点标签和最终三值，检查二者是否服从同一聚合规则；
4. 隐私审核并形成新的不可变 Snapshot；
5. 先跑 Pro/off；只有边界题需要时，再与 Flash/on 做 paired comparison；
6. 新 holdout 达到既有 gate 后，才讨论改变生产默认和让自动信号驱动学习策略。

模型间“一致率”不是“准确率”：没有人工标签时，多模型共识最多用于发现争议；有冻结人工标签时，主指标
仍是逐点 accuracy、严重错误和 paired sample 差异。Judger 也不能看到 `intended_demand` 或自动改写金标准。

## 验收边界

- 单测覆盖 Report v2、固定 cohort fail-closed、关键点聚合、DeepSeek/DashScope 精确请求体与 cassette 续跑；
- CLI 支持模型/thinking/effort/sample subset/cassette，并提供多报告比较命令；
- 领域术语只在 `CONTEXT.md` 定义，完整字段在 `domain-model.md`，未来顺序在 `roadmap.md`；
- 本轮不更换 `.env` 的生产默认、不修改冻结人工标签、不打开自动学习 gate。

## 2026-08-03：新 holdout 确认结果

随后使用同一 JavaGuide 固定 commit 密封了 12 道全新题，覆盖 Agent runtime、MCP、Context/Memory、
Eval/Observability 四类。owner 与 friend-01 各自闭卷回答 6 题；Codex 先做逐点辅助初筛，owner 在看见
候选 grader 输出前接受全部标签。该流程属于 assisted human gold，足以做方向确认，但不能冒充完全独立的
公开 benchmark。

12 条样本通过 source hash、exact Evidence、response hash、三值聚合和隐私审批后形成不可变 Snapshot：
`9d8780e2c20807e1bb3b84fcf51d7922fa5520381782582058e56a28616dddca`。真实对照仍使用
`answer_grade@111976c1`：

| 条件 | 逐点准确率 | 整题分区全对 | Derived 三值一致 | 严重 FN / FP | Retry | Token | 平均延迟 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Pro / Off | **81.25%** | **50.00%** | **50.00%** | **0 / 0** | **0** | **19,928** | **3.59 s** |
| Pro / High | 68.75% | 33.33% | 41.67% | 2 / 0 | 1 | 43,778 | 42.30 s |

两组都是 12/12 请求成功、0 最终 error；两份 cassette 离线重放时，除本地重新测量的 `latency_ms` 外，
报告逐字段一致。High 相比 Off 不仅总 Token 约 2.20 倍、延迟约 11.78 倍，还新增两个严重 false
negative，因此不再作为候选。

Off 仍未达到 policy 的 85% 三值一致率和 90% 逐点准确率。误差审计显示主要问题不是缺少更长推理，
而是 grader 对同义表达和隐含但可验证的回答召回不足，同时偶尔把未写出的细节判成 matched。当前决定：

1. Pro/Off 只保留为下一轮实现基线，不改变生产默认；
2. 不打开自动判卷驱动 Learning Memory 的 gate；
3. 将本轮 12 条转为开发误差集，先收窄原子评分点和语义匹配 Prompt；
4. 修改后必须再密封一套未见的新 holdout，不能在已经看过的题上宣布过门。

本地敏感报告、cassette 和完整逐题审计仍位于 gitignored
`localtemp/calibration/grading-holdout-01/experiment/`，不进入公开仓库。

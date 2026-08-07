# Grader 语义匹配收口

日期：2026-08-03
状态：实现、真实 cassette、开发误差集复测与合成挑战回归已完成；人工质量门仍未通过。

## 一句话结论

原判卷器要求模型直接报 matched/missing，却没有迫使它指出“学习者到底写了哪句话”。
这会同时放大两种相反错误：同义改写被漏判，只出现了相关词的答案又被模型脑补为命中。本轮
把生产契约收窄为：**LLM 逐点做语义判断，matched 必须绑定答案原文，代码验证引文并聚合三值。**

## 模块蓝图

```text
QuestionSpec
  ├─ ExpectedPoint[] + source cited_evidence
  └─ critical_point_ids
            │
            ▼
answer_grade prompt
  语义命中，不是字面重合
  允许同义表达 / 合理替代方案
  禁止补全未写出的细节
            │
            ▼
PointAssessment[]
  ├─ point_id
  ├─ label: matched | missing
  ├─ answer_evidence: 答案原文片段 | null
  └─ reason
            │
            ▼
grade_answer 代码门
  ├─ point_id 不重不漏
  ├─ matched 引文必须是 learner_answer 子串
  ├─ missing 不得带伪造引文
  └─ critical point 确定性聚合三值
            │
            ▼
Calibration Report v3
  保存逐点标签 + 答案引文 + 理由；v2 历史报告仍可读
```

这里有两种 Evidence，不可混淆：

- `ExpectedPoint.cited_evidence` 证明评分点从材料哪里来；
- `PointAssessment.answer_evidence` 证明模型从学习者答案哪里看到了命中。

代码只能证明后者“确实写过”，无法证明它“语义上就是对的”；这部分仍是模型能力，必须通过人工
holdout 校准。

## 为什么不直接上第二个 Judger

再加一个模型可以增加票数，却不会自动产生真值；两个模型还可能共享同一种 rubric 偏差。当前更需要的是
“每个判断可定位”，因此先增加答案引文契约。未来第二模型只适合作为争议发现或 Rubric Critic，不应绕过
人工 gold 直接改写学习状态。

## 实现前冻结的合成挑战集

为避免写完 Prompt 后再挑容易通过的例子，修改生产实现前已在 gitignored 目录冻结 12 条合成对抗样本：

- 位置：`localtemp/calibration/grading-semantic-challenge-01/`；
- 内容哈希：`545ea903dfbd6afc833cd83496f75f108dedf7d7931d69c188f8b8cf4f8f3791`；
- 预期分布：对 7 / 勉强 1 / 错 4；
- 针对错误：同义改写、合理替代方案、简洁但充分的表达、只命中背景词、缺失关键边界、反向因果等。

它的 `label_provenance=synthetic_oracle`、`release_gate_eligible=false`。它是针对已知缺陷的“单元考题”，
不是真实用户分布；即使 12/12 通过，也不得把质量门标为 passed。

## TDD 与回归证据

1. 先写新结构绿路测试，观察旧 parser 因缺少 matched/missing 列表而失败。
2. 再写“旧 list-only 输出必须拒绝”的契约测试，防止新契约被宽松兼容掏空。
3. 再写同义表达/禁止脑补的 Prompt 行为契约，然后修改模板。
4. Report v3 先红后绿，并新增 v2 缺少逐点证据时仍可读的兼容测试。
5. 新 Prompt 落地后旧真实 cassette 准确地产生两个 `ReplayMiss`；重录后单题考核与难度激活
   capstone 均可离线重放；加上 v3.1 出题契约测试后最终全量为 `1005 passed`，没有其他产品回归。

## 真实 Pro / Thinking Off 开发误差集复测

用新 Prompt `answer_grade@794e5369` 在同一个 12 条 Snapshot 上重跑：

| 指标 | 收口前 | 收口后 | 变化 |
| --- | ---: | ---: | ---: |
| 逐点准确率 | 81.25% | **87.50%** | +6.25pp |
| Derived 三值一致率 | 50.00% | **66.67%** | +16.67pp |
| 严重 FN / FP | 0 / 0 | **0 / 0** | 持平 |
| Retry | 0 | 1 | +1 |
| Prompt Token | 17,649 | 18,782 | +6.42% |
| Completion Token | 2,279 | 6,748 | +196.09% |
| Total Token | 19,928 | 25,530 | +28.11% |

改善来自可定位的逐点修正：`GQ2-H05` 不再脑补未写的 result/error 互斥点；`GQ2-H06` 从
1/4 提升到 3/4；`GQ2-H09` 与 `GQ2-H10` 的聚合三值回到人工结论。剩余误差也更清晰：
`GQ2-H01` 仍未接受 LLM Call 边界与分层观测的隐含表达；`GQ2-H03` 仍在 orchestrator 与 aggregation
gate 上存在分歧；`GQ2-H06` 虽然逐点改善，但缺少预注册核心点仍使代码聚合为“错”；
`GQ2-H08` 反而将已表达的混合边界判 missing，并发生一次结构重试。

本轮质量有明显提升，但仍低于 90% 逐点与 85% 三值 gate。同时详细逐点理由使 completion Token
显著上升；这不能被当作免费的质量改善，后续需在不损害引文可审计性的前提下压缩 reason 与 Prompt。

## 合成挑战结果与资格隔离

冻结的 12 条合成挑战得到 100% 逐点准确率、100% 三值一致率、0 严重 FN/FP、0 retry，共
16,824 Token。这说明本轮专门针对的同义改写、合理替代和“相关词不等于命中”边界已在定向集上收口。

第一次运行曾直接把“实现前未见模型输出”映射为 `blind_to_model_output=true`，这会让生产报告错把合成
oracle 当作人工 gold。已用同一 cassette 零 Token 重放修正：生产 Report 现在是 0 eligible / 12
exploratory / `insufficient_evidence`；独立 `grading-synthetic-challenge-summary.v1` 才保存上述定向指标。
合成集无论表现多好都不会打开发布门。

## 可复现产物

本地敏感答卷、报告与实验 cassette 位于 gitignored
`localtemp/calibration/grading-semantic-matcher-v3/`。关键 SHA-256：

- 开发误差集 Report：`344d59e33eba8cf736ca8ad6607dd9d8c8935bd3a34d48ef2b2e5ce3fe504900`；
- 开发误差集 cassette：`44852218d0d1b21639bc4942e2505fcd9417b151df203bdf5ca4de39538d388b`；
- 合成挑战 exploratory Report：`1a0b3851a65756becc98b842317c3b1b419f45f26806aaf2e25f9b3521947660`；
- 合成挑战 Summary：`6fe04c44b3dcb6da3f5c846e60589a8d8f3efdcc21d8ddcaec08d337ba4c4e63`；
- 合成挑战 cassette：`baf25840376a11405a742a566b9b8f0e626761422ea44bfecb85e68e47faac6a`。

下一步不是继续在这 24 条已见样本上追分，而是先审查剩余分歧究竟属于 Grader 假阴性、rubric
过紧还是人工标签可议，再收一批真实、未见、人工盲标 holdout 作最终 gate。

## 2026-08-03：残余分歧人工裁决

owner 在不改动原 Snapshot 和 annotations 的前提下，完成 H01/H03/H06/H08 四题归因。结论为：

- H01：原人工标签可议，`llm_call_boundary` 修订为 missing，有效三值从“对”改为“勉强”；
  `separate_observability` 维持 matched。该修订只进 append-only overlay，不改写原 gold。
- H03：确认 Grader false positive。结构化方向没有覆盖冻结 rubric 中的 TaskID 与 Dependencies。
- H06：确认 Grader false negative。`max_lines + Server 物理截断` 足以语义支持 hard limit。
- H08：确认 Grader false negative。将两类信息分流到两种存储介质，本身就表达了 hybrid boundary。

修订 overlay 后，已见开发集的公平口径从 87.50% 提升为 **89.58% 逐点准确率**，三值一致率从
66.67% 提升为 **75.00%**，严重 FN/FP 仍为 0/0。它仍是 development-only，不能因人工重新裁决后
接近阈值就充当发布 holdout。本地审计产物：

- overlay SHA-256：`9f8cf78e00c881bc16be63d60176168b136becac5defd6cd0c6714af487a93ce`；
- summary SHA-256：`0ba66b3274bed7ecacc07632bebd720afa18fb85c2fe09dea9a5de5e37656d72`。

## v3.1 成本收窄与真实复测

根据人工裁决，下一版 Prompt 只加入可泛化的判断规则，不写样本 ID 或专用答案：

1. 显式列为“至少/必须”的多个必要条件要全部支持，避免 H03 式过度概括；
2. 必然实现不变量的操作机制可以命中，不强求术语或数值阈值逐字出现，覆盖 H06 类语义；
3. 对不同信息的分流与责任分配可联合表达混合边界，覆盖 H08 类语义；
4. 每个逐点 `reason` 目标不超过 30 个中文字符，总体 reason 不超过 50；不收紧 Pydantic
   读取上限，以保持旧 Report v3 可读。

H01 暴露的 rubric 过载问题进入未来出题规则：一个 ExpectedPoint 只表达一个可独立判断的语义不变量，
定位责任层与枚举扩展职责若都是必答项，应拆成多个评分点。新版本为 `answer_grade@80d6d27c`、
`question_generate@b3267a80`、`question_probe@54af9642`。

经 owner 明确授权，使用同一 12 条开发误差集、同一 `deepseek-v4-pro / Thinking Off` 设置完成真实
复测。v3 与 v3.1 的 48 个逐点决定完全一致；应用冻结的人工裁决 overlay 后，两版同为 89.58% 逐点准确率、
75.00% 三值一致率、0/0 严重 FN/FP。v3.1 没有在已见样本上刷分，也没有引入判断漂移，主要收益是成本：

| 指标 | v3 | v3.1 | 变化 |
| --- | ---: | ---: | ---: |
| Prompt Token | 18,782 | **16,449** | -12.42% |
| Completion Token | 6,748 | **5,147** | -23.73% |
| Total Token | 25,530 | **21,596** | -15.41% |
| Retry | 1 | 1 | 持平 |

同一份冻结合成挑战仍为 100% 逐点 / 100% 三值 / 0/0 严重错误，Token 从 16,824 降至 13,857
（-17.64%）。它仍保持 0 eligible / 12 exploratory / `insufficient_evidence`，不能打开发布门。
两组 cassette 均已做零网络 replay，除 `latency_ms` 外报告逐字段一致；单题考核与难度激活两份发布测试
cassette 也已按新 Prompt 重录并通过离线 replay。

剩余误差簇没有被隐藏：H03 `structured_contract` 仍是假阳性；H06 `hard_limit`、H08
`hybrid_boundary` 仍是假阴性；H01 `separate_observability` 与 H09 `memory_provenance` 仍有逐点分歧。
它们应作为新真实密封 holdout 的观察项，而不是继续针对已见答案堆 Prompt 规则。

v3.1 本地审计产物位于 gitignored `localtemp/calibration/grading-semantic-matcher-v31/`：

- 开发误差集 Report：`aff1b88376219b40d7c0fc2ed47a74f87ff405e70743779976a11989500d3493`；
- 开发误差集 cassette：`ba12ea97df8673b34c3214bcf373a943783101f991e95614587b9503491d6752`；
- 人工裁决口径 Summary：`fe8848826e38117c05f78ec0ebc89fec8b99c93422bdf17716aeca1a7df14294`；
- 合成挑战 exploratory Report：`160efce960319b4d04044caa75d735cd24cb1d1bec88f8758bc7a2819612f06f`；
- 合成挑战 Summary：`106075933ac1a09bf9480f4076697ba79f614c13223357e138ec6738f2fcf56a`；
- 合成挑战 cassette：`429c20349ae6c29e3d922617670cd95228bb058de982e56be0025a0edf4d31fd`。

最终工程门：`1005 passed`；Ruff lint、Ruff format check、Pyright 与 import-linter 全绿。

## 新真实密封 Holdout：release gate 未通过

为防止继续在已见开发集上过拟合，另建 `grading-holdout-02`：12 道新题、48 个原子评分点，题目与
rubric 在答卷前锁定；owner 闭卷作答后再由 Codex 初筛、owner 接受全部人工标签。12 条样本全部通过
隐私审核并冻结为 Snapshot
`6d8c915a22458c7b0d0c12226651084a9ad51a4e5dcbcf04734abaaa6bb21430`，0 excluded，且在生产
Grader 运行前保持 blind。

经明确授权，以 `deepseek-v4-pro / Thinking Off` 和 `answer_grade@80d6d27c` 运行正式 gate：

| 指标 | 结果 | Gate |
| --- | ---: | ---: |
| Eligible samples | 12 | >= 10 |
| Point accuracy | **68.75%** | >= 90% |
| Verdict agreement | **58.33%** | >= 85% |
| Serious FN / FP | **3 / 0** | 0 / 0 |
| Retry | 2 | audit |
| Token | 22,037 | audit |

gate 明确失败。3 个 serious FN 分别是混合预加载/JIT 边界、Top-K/最近轮次等预算裁剪机制、真实进程
计时与 telemetry；模型仍倾向要求评分点说明中的示例或术语逐字出现。另有一题三次把
`answer_evidence` 改写或加入省略号，无法通过 exact-substring 契约，最终没有合法判决。这说明 v3.1 在
已见 cohort 上的 89.58% / 75% 不能外推到新题；成本收窄成立，但泛化质量尚未成立。

同一 cassette 已零网络 replay，删除 `latency_ms` 后报告逐字段一致。人工 gold 不做事后修改，本 Snapshot
自此只作为开发回归集。下一版应优先把评分点的 acceptance semantics 与说明示例分离，并将 Evidence 从
自由复制文本改为代码可验证的 span/offset 选择；修复后必须再用另一批未见真实 holdout 开门。本地哈希：

- Live Report：`3c2fb2cf0615310ccfd293c6286a08ed759bb5e64bd0aa13500c7519e12c48dc`；
- Replay Report：`4fe6c40e2a88c1a679e45b4b36c595937d941b1b6aadcd633523a543cec84664`；
- Cassette：`755831652a083dbc32c5ae2a640ca70003b1e3c9ee1caa083cb1c3fbca0866b4`。

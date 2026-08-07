# 真实判卷校准准备：从人工答卷到不可变 Snapshot

日期：2026-08-03
状态：数据准备、本地授权链与第一次真实生产校准已完成；质量门按设计失败。后续契约收窄与 2×2 pilot
见 [28-grading-contract-and-model-pilot.md](28-grading-contract-and-model-pilot.md)，自动学习策略继续关闭。

## 先看全局蓝图

```text
人工盲答与终审
    ↓
Calibration Compiler
校验文件哈希、题/答/标签覆盖和人工终审状态
    ↓
19 × GradingCalibrationSample + 1 × RubricExclusion
    ↓
Eval Inbox
记录候选版本、隐私审核人与理由
    ↓
Dataset Snapshot
冻结内容 hash 和完整审核 provenance
    ↓
Calibration Runner
调用生产 grade_answer，计算一致率、逐点评判准确率、严重误判、重试和 Token
```

数据准备阶段先停在 Snapshot；用户随后明确授权把 19 条题目、rubric、Evidence 与答卷发送至 DeepSeek
官方 API，第一次真实校准已完成。失败结果原样保留，没有修改人工标签或 rubric 迁就模型。

## 每个模块负责什么

| 模块 | 做什么 | 明确不做什么 |
| --- | --- | --- |
| 本地 Source Pack | 保存密封题目、两位答题者的独立答卷、owner 终审和 SHA-256 | 不充当数据库，不自动调用模型 |
| `evals/grading_dataset.py` | 验证冻结证据并转换为正式样本；保留 rubric 排除记录 | 不改人工标签，不写 Inbox，不判卷 |
| `domain/learning/eval_inbox.py` | 导入候选、保存隐私审核、创建不可变 Snapshot | 不理解 YAML 文件布局，不调用 Grader |
| `evals/grading_calibration.py` | 让 Snapshot 的 eligible blind 样本进入现有生产 `grade_answer` | 不复制 Eval 专用 prompt，不修改数据集 |
| CLI calibration adapter | 装配本地路径、SQLite、Provider 和审计产物 | 不承载领域判断 |

这次“深化”的重点不是增加字段，而是把三条边界接实：人工文件到正式样本、正式样本到授权快照、授权快照到
现有生产判卷器。每一段都可以单独复现和失败，不需要猜上一段发生过什么。

## 数据契约如何逐层收窄

### 1. Calibration Source Pack：人类证据原件

`calibration-manifest.yaml` 记录密封题目、两份答卷和终审标签的 SHA-256。Compiler 会拒绝以下情况：

- 任一冻结文件改了一个字节；
- 答题者看过 rubric、不是闭卷或题目 hash 不一致；
- 题目、答卷、标签没有 exactly-once 对齐；
- `label_status` 仍是 assistant prefill，而不是 `human_adjudicated`；
- 某题的 point labels 没有完整覆盖 ExpectedPoint。

### 2. GradingDatasetCompilation：可信转换结果

Compiler 只输出既有 `GradingCalibrationSample`，没有发明第二份判卷契约：

```python
GradingCalibrationSample(
    sample_id="GQ-A01",
    annotator="owner",
    blind_to_model_output=True,
    question=question_spec,
    learner_answer=response,
    human_verdict="勉强",
    human_matched_points=["control_boundary", "path_certainty"],
    human_missing_points=["operational_reason"],
)
```

`GQ-M03` 没有被删除或强行改标签，而是进入 `RubricExclusionV1`。原因是题面允许多种合理实现，rubric 却把
BM25+dense、reranker 等特定技术路线升成必答项。把它混入 gate 会测到 rubric 缺陷，而不是 Grader 质量。

### 3. Dataset Snapshot：获批且不可变的运行输入

19 条样本导入 Eval Inbox 后，由 owner 完成本地隐私审核。Snapshot 保留每条候选的 payload hash、review
request、reason 和 time；相同内容重复执行得到同一个 snapshot，而历史 snapshot 不会被后来修改。

本次真实结果：

- 人工裁决：20 条；
- eligible blind：19 条；
- rubric exclusion：1 条（`GQ-M03`）；
- exploratory correction：0 条；
- Snapshot：`880c4f104c5b59d8ad6fd2b945378c6d35f5f797394d20090358378d5c41c843`；
- 数据准备阶段 Provider calls / Token：0。

本地原件、SQLite 和编译产物位于 gitignored `localtemp/calibration/v030-javaguide-agent/`，不会进入开源仓库。

## 如何复现

准备数据，不调用模型：

```bash
.venv/bin/grandquiz prepare-grading-calibration \
  --pack localtemp/calibration/v030-javaguide-agent \
  --db localtemp/calibration/v030-javaguide-agent/eval-inbox.db \
  --out localtemp/calibration/v030-javaguide-agent/prepared \
  --reviewer owner \
  --review-reason "已确认不含密钥和非必要个人身份信息"
```

用户明确接受 Provider 成本后，才运行：

```bash
.venv/bin/grandquiz calibrate-grading \
  --snapshot 880c4f104c5b59d8ad6fd2b945378c6d35f5f797394d20090358378d5c41c843 \
  --db localtemp/calibration/v030-javaguide-agent/eval-inbox.db \
  --out localtemp/calibration/v030-javaguide-agent/grading-calibration-report.json
```

## 测试怎样约束这条链

测试只穿透三个公开接口，不锁私有 helper：

1. `compile_grading_dataset`：成功编译、hash 篡改失败、未终审失败、rubric exclusion；
2. `promote_grading_dataset`：导入、批准、重试幂等、只冻结 eligible 样本；
3. `run_snapshot_grading_calibration`：Snapshot 通过同一个生产 Grader calibration runner。

因此以后可以调整 YAML 解析或内部函数，但不能悄悄绕过人工终审、隐私门或 Snapshot。

## 第一次真实校准结果

运行目标为 DeepSeek 官方 API 的 `deepseek-v4-flash`，只使用 production Grader 的 `role=basic`。旧 adapter
发送的是 DashScope 风格 `enable_thinking=false`，而 DeepSeek V4 使用另一份 thinking 契约，因此本次运行的
思考状态必须记为 `thinking_control_unknown`，不能作为开/关对照证据。报告保存于
gitignored `localtemp/calibration/v030-javaguide-agent/grading-calibration-report.json`，SHA-256 为
`b2ac1c0d8b4e3e9bffd44d6f7e78947de304d0a3b1b566e2bafb0434be1d42f0`。

| 指标 | Gate | 实际 | 结果 |
| --- | ---: | ---: | --- |
| eligible samples | ≥ 10 | 19 | 通过 |
| verdict agreement | ≥ 85% | 63.16%（12/19） | 失败 |
| point accuracy | ≥ 90% | 79.17%（57/72） | 失败 |
| serious false negative | 0 | 0 | 通过 |
| serious false positive | 0 | 0 | 通过 |
| schema/provider error | 0 | 0 | 通过 |
| retries | 观察项 | 1 | 记录 |
| total tokens | 观察项 | 66,894 | 记录 |
| average tokens / eligible sample | 观察项 | 3,520.74 | 记录 |

人工 verdict 分布是“对 4 / 勉强 12 / 错 3”，模型是“对 2 / 勉强 15 / 错 2”。7 个 verdict 分歧全部发生
在相邻档，没有“对 ↔ 错”的严重跨档错误；生产 Grader 明显向中间的“勉强”收缩。

更重要的是，`GQ-A03` 与 `GQ-X03` 的模型逐点评判和人工逐点评判完全一致，最终 verdict 却由人工“错”变成
模型“勉强”。这说明至少部分失败不是模型没识别要点，而是聚合契约不完整：人工指南允许“核心方向错误，
即使命中局部点仍判错”，生产 Prompt 则把“至少命中一个且至少缺失一个”强烈引向勉强；`ExpectedPoint`
又没有机器可见的核心性或推翻条件。当前不能声称哪一方天然正确，必须先把同一标准写成可执行契约。

还有一个本轮暴露的审计缺口：`GradingCalibrationResult` 只保存 point correct count，没有保存模型的
`matched_points / missing_points / diagnosis / reason`。因此可以定位哪些题分歧，却无法离线审查模型具体漏判
了哪个评分点。下一次真实调用前应先补齐这些字段，并保持报告不包含 system prompt 或密钥。

### 当时识别出的下一步（现已完成）

1. 扩展 calibration report，持久化安全的模型逐点判定、diagnosis 与简短 reason；
2. 明确“全命中 / 部分命中 / 核心错误”如何确定性聚合为三值，避免人工指南与生产 Prompt 各说一套；
3. 不修改当前冻结人工标签，用同一 Snapshot 做受控复测；
4. 只有复测达到 gate，才讨论让自动信号驱动学习策略。

前三项已在下一轮完成；同一 Snapshot 的复测是方向性开发 pilot，不是新的盲测 release gate。结果、实现
职责和新的 holdout 要求见 [判卷契约收窄与 DeepSeek 2×2 Pilot](28-grading-contract-and-model-pilot.md)。

## DevRecords 应该怎么看

不需要按编号从 01 开始通读。理解这条演进链，按以下顺序即可：

1. [25-v030-evidence-loop.md](25-v030-evidence-loop.md)：为什么需要真实盲标，以及质量门测什么；
2. [26-v040-human-approved-discovery.md](26-v040-human-approved-discovery.md)：为什么候选必须经人工授权再形成快照；
3. 本文：真实数据如何穿过 v0.3 与 v0.4，最终停在 Provider 成本门前。

每篇先看“为什么做”和蓝图，再看模块职责、数据契约、不变量/失败路径，最后看验收数字。代码片段只是帮助
理解契约，不是新的权威定义；领域术语看 `CONTEXT.md`，完整实体看 `docs/domain-model.md`，未来顺序看
`docs/roadmap.md`。

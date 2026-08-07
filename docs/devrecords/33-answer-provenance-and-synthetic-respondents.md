# 答卷来源隔离与 Synthetic Respondents

日期：2026-08-04
阶段：Synthetic Respondents 01 与 Holdout 03 数据建设均已完成；正式 gate 结果见 DevRecord 34

## 为什么不能直接让 DeepSeek 替你完成 Holdout

模型答题很适合快速制造完整、遗漏和边界错误答案，但它测到的是“Grader 能不能处理模型式表达”，不是
真实学习者体验。如果 DeepSeek V4 Pro 同时是答题者和 Judger，还会共享措辞和概念偏好，使指标偏乐观。

因此本轮采用两条互不混算的轨道：

```text
已揭盲 Development Gold 题 ──> DeepSeek 30 条模型答卷 ──> exploratory challenge

未来未见 Holdout 03 题 ─────> owner/朋友独立答卷 ──────> release-gate eligible
```

## 先补的契约漏洞

旧 `GradingCalibrationSample.eligible` 只检查“人工 annotator + 盲于模型输出”。这意味着 DeepSeek 作答、
owner 批改的数据也可能被误算进 release gate。

现在答案来源采用四值契约：

- `unassisted_human`
- `assisted_human`
- `model`
- `synthetic_oracle`

只有第一种可以 eligible。`GradingDatasetCompilation` 会按每个 response source 保留 provenance 和模型身份，
`GradingCalibrationReport` 也直接展示它们；不能再用一个丢失原因的布尔值混过去。旧数据缺省为
`unassisted_human`，保持已有人工包兼容。

## 真实生成

生成模型：DeepSeek V4 Pro / Thinking Off。模型每次只看到题干与画像，不看到评分点、critical point、
参考答案、Evidence、人工判决或生产输出。

30 条组成：12 条精炼正确、12 条部分理解、6 条合理但有细微误区。来源覆盖 Holdout 02 已揭盲的全部
12 道题，因此不污染未来 Holdout 03 的新 QuestionSpec。13 个真实响应共使用 3,954 prompt + 3,711
completion = 7,665 Token。第 4 题首次返回重复画像，被结构门拒绝并有界纠错；这次额外响应也进入 cassette。

本地原始产物位于 `localtemp/calibration/grading-synthetic-respondents-01/`，受 gitignore 保护；公开仓库只
记录方法、契约和汇总，不提交真实实验答卷。

人工 assistant screening 已覆盖全部 30 条：初筛为 `对 6 / 勉强 12 / 错 12`，其中 6 组边界需要 owner
复核。复核的重点不是“模型答得像不像标准答案”，而是题干是否足以支持 critical point、隐式但正确的
行为是否应被接受，以及正向评分点是否遗漏了会产生假阳性的负向约束。原始答卷、逐点初筛与复核队列
均留在 gitignored 的本地实验目录。

Holdout 03 第一批也已从同一固定 source commit 冻结 10 个全新 QuestionSpec，共 40 个原子评分点；所有
Evidence 均通过逐字子串校验。owner 只接触无 rubric 的题面和空白答卷。该批次是 20+ 新题目标的第一半，
尚不能单独作为 release gate。

owner 随后完成了 10 条 `unassisted_human` 闭卷答卷；题库与答卷分别以 SHA-256 锁定，答案冻结后才打开
rubric。Codex 的盲于生产输出初筛为 `对 3 / 勉强 5 / 错 2`，但仍是 non-authoritative。两个会改变三值
结论的 critical 边界是：通用 Trace 是否足以替代关键依据/检查步骤等可审计产物，以及 Evidence 绑定是否
已经等价于“先抽逐字引用再分析”。这两项必须由 owner 终审，生产 Grader 在此之前保持未运行。

owner 最终接受了全部 Codex 初筛：正式人工标签为 `对 3 / 勉强 5 / 错 2`，排除 0 条。题库、答卷、
annotations 与 manifest 均已独立 hash；确定性编译得到 10 条 eligible、0 条 excluded，全部答案来源均为
`unassisted_human`。该批是 Holdout 03 的首批真实 Gold，不等于已经满足 20+ 新题和 24–30 条人类答卷的
预注册规模；生产 Grader 仍未运行。

同日又单独冻结 Batch 02：GQ4-H11–H20 共 10 个新 QuestionSpec / 40 个原子评分点，覆盖任务分解、工具
边界、Skill 验证门、MCP Server 安全、Workflow State/恢复、clean restart、条件分支和分层 Markdown
规则。所有 Evidence 再次通过固定源码逐字校验，与已有 54 道题的最高文本相似度为 0.291；语义审计后
还主动将重复使用“每日 CI Loop”场景的候选题替换为复杂 Skill 条件分支。第二批 rubric 与题面已隔离，
等待 owner 闭卷作答；两批题目合计已达到 20 个新 QuestionSpec。

owner 已完成 Batch 02 的 10 条 `unassisted_human` 闭卷答卷并锁定 hash。Codex 初筛为 `对 5 / 勉强 5 /
错 0`，生产 Grader 仍未运行；H12 的负向工具边界、H14 的参数化 SQL/脱敏与 H16 的异常升级是建议 owner
重点复核的接受边界。owner 随后接受全部初筛，第二批确定性编译得到 10 eligible / 0 excluded。

两批合计为 20 个新 QuestionSpec、80 个原子评分点和 20 条 `unassisted_human` eligible 答卷，人工标签
分布为 `对 8 / 勉强 10 / 错 2`，排除 0 条。题目规模目标已完成；距离 24–30 条人类答卷的正式 gate
规模还差朋友提供的 4–10 条自然答案，不需要再造第三批题。生产 Grader 继续保持未运行。

## 同题多答的 identity seam

准备朋友答题包时发现，旧编译器把 `sample_id` 同时当成“题目 ID”和“答案 ID”，因此只能表达“一题一答”。
这会让 20 道新题无法合法承载第 21–30 份自然答卷。现在 response entry 可额外提供 `question_id`：

```yaml
- sample_id: GQ4-F01-H11   # friend-01 的这份独立答案
  question_id: GQ4-H11     # 它回答的既有 QuestionSpec
  answer: "..."
```

编译器按 `sample_id` 校验答卷与人工标签 exactly-once，按 `question_id` 查找 rubric，并要求每个题目至少有
一份答案。旧包没有 `question_id` 时继续按 `sample_id` 解释，两个已冻结 owner 包的 compiled content hash
保持不变。这样新增朋友答案不会伪装成新题，也不会覆盖 owner 的答案。

两位朋友随后分别独立闭卷完成 5 条答案，均确认未使用搜索、AI 或 rubric。原始 Markdown、逐字转录的
response pack、assistant screening 和 owner 终审标签分别锁定；朋友标签为 `对 9 / 勉强 1 / 错 0`，
39 matched / 1 missing，排除 0 条。扩展 Batch 02 编译为 20 eligible / 0 excluded，同时保留原 Owner-only
manifest 与 content hash，不改写旧审计链。

最终 Holdout 03 为 30 条 `unassisted_human` 答卷、20 个 unique QuestionSpec、120 个 point decisions，
人工标签 `对 17 / 勉强 11 / 错 2`。本地隐私扫描没有发现身份、凭证、私有路径或客户数据；两批 Compilation
被晋升为单一 30 eligible / 0 exploratory Dataset Snapshot：
`71a504b0725e41e9992e217de1daf89429f1b126faaa281c7d8822558d306743`。后续获得明确外发授权并完成正式
运行；三值一致率 83.33% 未过 85% 门槛，完整结果与诊断见
[Holdout 03 正式 Release Gate](34-holdout-03-release-gate.md)。

## 这批数据能做什么

- 提前发现 Grader 的假阳性、假阴性和语义过度推断；
- 比较同一问题在完整、部分、误区表达下的判决边界；
- 验证输出结构、重试和 Record/Replay；
- 人工标注后成为 Development Gold 的 exploratory 分层。

Synthetic Respondents 不能补足人类 Holdout 数量，也不能用于宣称真实学习者准确率。本轮最终通过 owner
20 条与两位朋友各 5 条独立答案达到 30 条；这个来源分层在真实 gate 中得到保留。

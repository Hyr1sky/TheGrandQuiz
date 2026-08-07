# 判卷答案 Evidence 单元生产化

日期：2026-08-04
阶段：Evidence 结构可靠性收口；语义质量 gate 仍关闭

## 一句话蓝图

模型不再“抄一段答案来证明自己判对了”，而是从代码提前编号的答案原文单元里选 ID；代码验证选择并
还原原文。这样保留了 LLM 的语义判断能力，同时把原文真实性变成可以程序性验证的契约。

```text
学习者答案
    │ 代码按句末标点/换行切分一次
    ▼
AnswerEvidenceUnit[]：ID + source offsets + exact text
    │ Prompt 只展示一次，每个单元一个 ID
    ▼
LLM：逐评分点输出 matched/missing + answer_evidence_ids
    │ 代码校验 ID 存在、不重复、属于本次答案
    ▼
PointAssessment：保留 IDs，并解析出兼容展示用 answer_evidence
    │
    ├─ Calibration Report / UI 可读原文
    └─ critical_point_ids 继续由代码聚合三值判决
```

这里的 `AnswerEvidenceUnit` 是一次判卷调用内的临时输入契约。它不是知识库里带 revision/node/source
span 的长期 `Evidence`，也不会新建一张表或成为第二套领域事实。

## 为什么先做实验

v3.2 已证明“取消 80 字目标 + 明确禁止省略号”可以让最终合法输出恢复到 100%，但 H10 第一轮仍然把
不相邻句子用省略号拼在一起。这说明自由复制本身就是不稳定动作：模型既要做语义判断，又要准确执行
字符级复制，两个任务互相干扰。

我们先在已揭盲开发集上做了 throwaway prototype，只回答一个问题：把复制改成选择 ID，结构和成本是否
真的改善？它不是新的 release holdout，因此只用于比较机制，不能用于宣布模型已经达到发布质量。

| 契约 | 首轮合法输出 | 最终逐点准确率 | 三值一致率 | 重试 | Token |
| --- | ---: | ---: | ---: | ---: | ---: |
| 自由复制原文 | 11/12 | 77.08% | 66.67% | 1 | 21,116 |
| 唯一句子单元 ID | **12/12** | 77.08% | 66.67% | **0** | **18,850** |

ID 方案在不改变语义判定的情况下减少了 10.73% Token，并消除了本轮结构重试，所以满足进入生产的门槛。

## 生产契约怎么工作

### 1. 代码先建立唯一单元

`build_answer_evidence_units()` 按中文/英文句末标点和换行切分，去掉单元边界空白，但 `text` 始终是
学习者答案的精确 source slice。单元保持原文顺序、互不重叠，ID 由契约版本和 source offsets 构成：

```python
AnswerEvidenceUnit(
    unit_id="v1e019_074",
    start=19,
    end=74,
    text=answer[19:74],
)
```

分号暂不切分。这是刻意保持窄小、稳定的 v1 契约；若未来需要 clause 级拆分，应升级版本并重新做成本与
质量实验，而不是悄悄改变同一 ID 的含义。

### 2. 模型只选择 ID

Prompt 只展示一次学习者答案单元。matched point 至少选择一个 `answer_evidence_ids`；missing point 必须
返回空列表。模型输出旧的 `answer_evidence` 自由文本会被拒绝，防止新旧契约在生产中悄悄混用。

### 3. 代码解析兼容读字段

Parser 拒绝未知 ID、重复 ID，并把多个选择规范化为 source order。合法 ID 再由代码还原成精确原文，写入
`PointAssessment.answer_evidence`。因此现有报告和 UI 不需要同时迁移，但这个字段已经从“模型自报事实”
变成“代码派生的读模型”。历史 Report v2/v3/v4 仍可读取。

### 4. 三值聚合没有改变

本次只加固 Evidence 结构，不修改 `ExpectedPoint` 的语义，也不修改 `critical_point_ids` 的确定性聚合：
全命中为“对”，零命中或缺任一核心点为“错”，其他为“勉强”。因此实验中的语义指标保持不变是符合预期
的，它证明我们没有借结构补丁偷偷改变评分规则。

## TDD 推进记录

1. 先写稳定切分测试：精确 source slice、顺序稳定、每段非空原文只展示一次。
2. 再写生产 parser 测试：matched 必须选 ID，missing 必须为空，未知/重复 ID fail closed。
3. 写 retry 测试：错误反馈回显非法 ID，并列出当前合法 ID；第二次调用可恢复。
4. 迁移接口、Eval harness 与 scripted provider，让 CLI/Web/FastAPI 继续穿过同一个 `grade_answer()` seam。
5. 机械迁移两份真实 cassette 的请求指纹和 Evidence 字段；保留原始判断、理由与 usage，不把它们冒充
   新 Prompt 的真实成本测量。

这种顺序把风险拆成三层：纯函数切分、生产边界校验、完整 workflow 回放。失败时能准确知道是哪层契约漂移。

## 验证结果

- `tests/test_grading.py`：27 passed；
- `tests/test_grading_calibration_v2.py`：9 passed；
- 接口、Assessment、Eval 与 Replay 影响集：160 passed；
- Python 全量：`1009 passed`；
- 两份真实行为 cassette 零网络 Replay 通过；
- Ruff format/check、Pyright、import-linter 与 `git diff --check` 全绿。

## 这一步没有做什么

- 没有把一个过载 `ExpectedPoint` 自动拆成多个评分点；H02/H10 的 atomic rubric micro 仍只是已揭盲实验。
- 没有改变 acceptance semantics，也没有引入第二个 Judger 自动修正生产判决。
- 没有重跑或打开 release gate；这批样本已经参与开发，不能再次充当未知 holdout。
- 没有持久化 AnswerEvidenceUnit，也没有创建新的 schema、队列或领域服务。

下一步应该先把“一个评分点究竟允许哪些合理表达”收窄为原子、可独立判断的语义契约，再用独立的新
blind holdout 检验语义质量。结构可靠性和语义正确性是两道不同的门，本次只关闭了前者。

后续结果：nested `all_of/any_of` prototype 因结构可靠性、Token 与示例过约束问题被拒绝；生产继续保持
flat ExpectedPoint。详见 [Benchmark 规模与 acceptance semantics 收口](32-grading-benchmark-and-replay-sequences.md)。

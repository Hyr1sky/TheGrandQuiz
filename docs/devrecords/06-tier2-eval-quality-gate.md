# Tier-2 Eval 质量门开发记录

> 记录日期：2026-07-19
> 范围：EJ-S1–S4。
> 当前边界：首版只给 case15 自然材料回答启用 `grounded_answer`；其余 14 条用例继续只运行 Tier-1。Eval 不自动修改 prompt、难度或生产数据，DS-S5 KnowledgeRelation 继续关闭。

## 1. 为什么要增加第二层评测

原有 Eval Harness 已能用确定性规则证明工具顺序、selected scope、状态写入、读取预算和 exact citation 正确，但“引文确实存在”不等于“最终回答的结论被引文充分支持”，规则也无法稳定判断问题覆盖度和学习解释质量。

本轮没有用 LLM judge 替代规则 scorer，而是建立双层门：Tier-1 继续负责可确定验证的行为事实，Tier-2 只负责规则层真实看不到的语义质量。声明了 quality profile 的用例必须两层都通过；未声明 profile 的用例显示 N/A，不产生 judge 调用或成本。

## 2. 校准优先的 QualityJudge

新增 Eval 层 `QualityJudge`，输入只有预注册 rubric id、用户问题、最终候选回答和最小 reference。`grounded_answer` rubric 固定评三个维度：`semantic_support`、`question_coverage` 与 `learning_usefulness`，采用 1–4 档离散分数，代码从固定门限计算 pass/fail，不采信模型自报的整体结论。

模型必须为每个 criterion 恰好返回一次分数、简短理由、candidate 逐字依据和 reference 逐字依据。Pydantic 与确定性代码拒绝缺失、重复、未知 criterion、越界分数、空理由和伪造 evidence；结构错误只允许有限重试，耗尽、Provider 异常或 Replay miss 都 fail closed。

judge 的 workflow started/ended、模型 started/ended、prompt version、usage、失败分类和错误 fingerprint 继续进入 `AgentEvent` 脊柱。评审使用独立 EventEmitter 与 span 树，不污染被测 workflow 的事件序列断言。

## 3. 人工 calibration gate

项目内新增 4 个人工标注的合成样本，覆盖 fully supported、partially supported、unsupported embellishment 与 justified refusal。每个样本给出三维可接受分数区间；只有 12 个阻断性维度全部落入人工区间，judge 才能包装成 `CalibratedQualitySuite` 并参与正式用例。

显式录制的第一次真实尝试在 `unsupported-embellishment` 样本发生分歧，校准门拒绝采用结果且没有写 fixture。脚本随后补齐失败时的逐样本审计输出；第二次真实运行达到 4/4 样本、12/12 维度 within-range agreement，校准消耗 6,240 tokens。这个过程验证了 calibration-first 不是形式检查：模型波动会被挡在质量门之外，不能靠手改输出做绿。

## 4. case15 双层门与离线 Replay

case15 的 YAML 增加可选 quality profile。Solver 捕获最后一个用户可见 assistant 回答，quality adapter 只投影问题、candidate 与测试内置 reference，不发送整条 trace 或内部工具历史。

Tier-1 仍独立检查 exact selected scope、search → read → citation、逐字 source span、读取比例、工具次数和 execution tokens；Tier-2 再判断语义支持、问题覆盖和学习可用性。`CaseReport` 分开保存 `rule_passed`、`quality_passed`、execution tokens、judge tokens、subject/judge prompt versions、两条事件流和两棵 span 树。

真实 `deepseek-v4-flash` 对 case15 的三个维度均给出 4 分，judge 消耗 1,123 tokens；被测 workflow 的既有 execution tokens 仍为 10,282，没有把评测成本冒充产品成本。录制 cassette 共 5 个 key，即 4 次 calibration 加 1 次 case15 judge，总 usage 为 7,363 tokens，不含密钥、生产材料或 `learning.db` 数据。

默认 `run_all()` 和 `grandquiz report` 只加载该 cassette，绝不从 `.env` 隐式创建真实 Provider。缺文件、旧 prompt/rubric 导致 key miss、校准失败或结构化 judge 失败都会只把 quality-enabled case 标成明确质量红灯；其余 14 条 Tier-1 用例不受 judge 基础设施影响。

## 5. HTML 与 CLI 可发现性

Eval 索引页增加 Rule、Quality、execution tokens、judge tokens 与 rubric 列，未启用 Tier-2 的用例明确显示 N/A，并可按全部通过、Rule 失败或 Quality 失败筛选。

case15 详情继续用原有 `render_trace_html` 展示 subject trace，在同一页面追加 rubric、judge prompt version、三维分数、理由和 candidate/reference evidence。judge 事件树另存为 `case15-quality.html`，同样复用 `render_trace_html`，没有复制第二套 trace 渲染器。所有模型与用例动态文本都先 HTML escape，报告保持自包含和零外部资源请求。

`grandquiz report --help` 现在说明默认离线 Replay、默认首页、显式 `--out` 与 macOS `open` 示例；`grandquiz trace --help` 说明 trace id、默认 trace DB、默认输出和打开方式。实际离线运行已成功生成 `/tmp/grandquiz-eval-report/index.html`。

## 6. 测试与工程门

新增契约测试覆盖合法结构化判定、criteria 缺失、伪造 evidence、有限重试耗尽、Provider 异常闭合 span、人工 calibration、一次校准后只评 case15、缺少 suite、空 cassette Replay miss、execution/judge 成本分离、双 Tier HTML 与 CLI help。

最终门禁：

```text
ruff check .                pass
ruff format --check .       pass（161 files）
pyright                     pass（0 errors）
lint-imports                pass（kernel layering kept）
pytest                      802 passed
grandquiz report            pass（15/15，默认离线 Replay）
```

## 7. 仍然保留的边界

这轮完成的是“可相信、可回放、可解释的 LLM grader 闭环”，不是自动自我修改系统。后续 prompt 或策略实验可以读取 Rule/Quality/cost 三组证据并提出候选，但是否采用仍由 HITL 决定。

当前只有自然材料回答 rubric；干扰项 plausibility、开放判卷正确性、语义近重复与 Reader fidelity 可以沿用同一质量结果、校准和报告骨架逐个增加，不应一次把全部 15 条用例都变成高成本 LLM eval。下一条产品开发主线仍可独立推进 Web Acquisition，不需要改变本轮 Eval 边界。

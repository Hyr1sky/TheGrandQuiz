# PRD：Tier-2 LLM Grader 与质量评测闭环

Status: approved（2026-07-19；用户批准先完成 Eval 方向）
Triage: ready-for-agent
Decision: 延伸 `docs/architecture.md` 的“双层 grader”，不改变 ADR-0004 的考核 workflow

## Problem Statement

TheGrandQuiz 已有 15 条 Tier-1 Eval 用例、完整 AgentEvent trace、Record/Replay、token 成本列和自包含 HTML 报告。规则 scorer 能可靠回答“是否调用了正确工具、是否遵守 scope、是否写对 Learning Memory、是否产生可解析 citation”，但不能回答“最终回答是否真正有帮助、是否被证据充分支持、题目或干扰项质量是否足够好”。

项目内部已经有一个服务生产出题 workflow 的干扰项质量 judge，但它不是 Eval Harness 的通用 grader，也没有人工校准集、统一质量报告或跨用例门限。若直接把一次 LLM 判断当成真值，模型偏差、提示变化、结构化输出失败和成本漂移都会让质量 eval 变成新的不稳定来源。

用户需要一个校准优先、可回放、可解释的 Tier-2 LLM Grader：它读取最终产物与最小必要参考证据，输出结构化维度判定、理由和逐字依据；真实模型结果录入 cassette，日常 CI 零网络回放；HTML 同时展示 Tier-1 行为门、Tier-2 质量门和各自成本。第一条产品 tracer bullet 评自然材料回答的 semantic grounding 与 usefulness，补足 exact citation 只能证明“引文真实”、不能证明“答案被引文充分支持”的盲区。

## Solution

建立一个 Eval 层的 `QualityJudge` 深模块。调用方提交版本化 rubric、candidate output、reference evidence 和明确 criteria；模块只进行一次有界结构化 LLM 评审，代码验证 criteria 完整性、离散分数范围、理由非空和所引片段确实来自 candidate/reference。结构错误允许有限重试，耗尽、Replay miss 或证据不合法均 fail closed。

先建立一组项目内人工标注的合成 calibration samples，覆盖明显优秀、部分支持、无依据扩写和诚实拒答。每次真实录制或 rubric/prompt 变化都必须先达到预注册的一致率门，才允许 judge 结果参与 Eval pass/fail。校准集只验证 judge 能否复现明确的人类边界，不拿 judge 自己的输出给自己当 golden truth。

case15 自然材料问答声明 `grounded_answer` quality profile。Solver 捕获最终用户可见回答；Tier-1 scorer 继续独立验证 exact selected scope、search → read → citation、预算与逐字 span；Tier-2 judge 只评语义支持度、问题覆盖度和学习可用性。最终 CaseReport 分开保存 rule verdict、quality verdict、execution usage 和 judge usage，并以二者均通过作为 quality-enabled case 的总通过条件。

HTML 首页和用例详情显示两个 Tier、rubric/prompt version、每维分数、理由、证据片段与 judge tokens；CLI help 给出生成 Eval 报告、导出单 trace 和打开默认 HTML 的完整示例。默认 `grandquiz report` 只用 Replay judge，禁止隐式调用外部模型；真实录制只通过显式脚本进行。

## User Stories

1. 作为项目作者，我希望 Eval 不只判断流程正确，还能判断最终回答是否真正有质量。
2. 作为项目作者，我希望 LLM grader 在参与质量门前先通过人工标注 calibration set。
3. 作为项目作者，我希望 calibration golden labels 由人明确给出，而不是由待测 judge 自己生成。
4. 作为项目作者，我希望自然材料回答同时通过 exact citation 规则门和 semantic grounding 质量门。
5. 作为项目作者，我希望 judge 区分“引文存在”与“答案中的结论确实被引文支持”。
6. 作为项目作者，我希望 judge 判断回答是否覆盖用户问题，而不是只复述一段正确但无关的原文。
7. 作为项目作者，我希望 judge 判断回答是否适合学习使用，并给出简短、可定位的理由。
8. 作为项目作者，我希望质量结果使用有锚点的离散等级，避免没有语义的连续假精度。
9. 作为项目作者，我希望每个 criteria 恰好返回一次，缺失、重复或未知 criteria 都被代码拒绝。
10. 作为项目作者，我希望 judge 所称依据必须逐字来自 candidate 或 reference，不能自造审计证据。
11. 作为项目作者，我希望 judge 结构化输出错误时有限重试，耗尽后让 Eval 明确失败。
12. 作为项目作者，我希望 Replay miss 大声失败，不能让缺录的质量评审静默变绿。
13. 作为项目作者，我希望日常 pytest、CI 和 `grandquiz report` 不访问网络、不消耗真实模型 token。
14. 作为项目作者，我希望真实 judge 只能通过显式录制流程调用，并保存 prompt/model/usage 指纹。
15. 作为项目作者，我希望 rubric 或 prompt 改变后旧 cassette 自动 miss，迫使重新校准和录制。
16. 作为项目作者，我希望被测 Agent 的 token 与 judge 自身 token 分开统计，避免把评测成本冒充产品成本。
17. 作为项目作者，我希望 Tier-1 失败和 Tier-2 失败在报告中分开展示，能看出是行为回归还是质量回归。
18. 作为项目作者，我希望 quality-enabled case 只有两个 Tier 都通过才算总通过。
19. 作为项目作者，我希望未声明 quality profile 的既有 14 条用例逐字保持原 Tier-1 行为，不被强制增加 judge 成本。
20. 作为项目作者，我希望 judge 评审事件继续走 AgentEvent 脊柱，并形成可审计的独立 span。
21. 作为项目作者，我希望 judge trace 不污染被测 workflow 的事件序列断言。
22. 作为项目作者，我希望 HTML 首页能筛选规则失败、质量失败和全部通过的用例。
23. 作为项目作者，我希望用例详情能看到 rubric version、维度结果、理由、依据和 judge token。
24. 作为 CLI 用户，我希望 `grandquiz report --help` 直接告诉我默认输出路径和浏览器打开命令。
25. 作为 CLI 用户，我希望 `grandquiz trace --help` 给出 trace id、DB 路径和默认 HTML 路径示例。
26. 作为维护者，我希望 QualityJudge 位于 eval 层，不让 kernel import learning domain，也不改生产考核状态机。
27. 作为维护者，我希望复用现有 Provider、prompt version、Record/Replay、EventEmitter 和 HTML renderer，不建立平行基础设施。
28. 作为维护者，我希望领域内现有干扰项 judge 保持生产职责，不被直接当成通用 Eval Grader。
29. 作为维护者，我希望首版只评已预注册的 rubric，不允许 YAML 注入任意 judge system instructions。
30. 作为未来实验开发者，我希望同一质量结果模型以后可增加题目质量、判卷正确性、语义近重复和 Reader fidelity rubric。
31. 作为未来实验开发者，我希望后续 A/B prompt 实验能复用 rule/quality/cost 三组指标，而不重写 grader。
32. 作为项目作者，我希望第一版不自动修改 prompt、难度或生产数据；judge 只提供可审计的实验门。

## Implementation Decisions

### QualityJudge 深模块

- QualityJudge 是 eval/application 层的离线评审槽，不是 kernel 原语、领域判卷工具或自由 ReAct agent。
- 输入由受信任代码选择预注册 rubric id；rubric 定义固定 criteria、评分锚点和通过门限。用例只能引用 rubric id，不能携带任意 system prompt。
- 首版统一使用四档离散分数：1=明显失败、2=主要不足、3=达到要求、4=表现优秀。每个 criteria 的文字锚点由 rubric 给出，代码只接受 1..4。
- 输出包含 rubric id、每个 criterion 的 score、rationale、candidate evidence、reference evidence 和 overall rationale。代码从维度分数与 rubric threshold 确定 pass/fail，不采信模型自报的 overall pass。
- criteria 必须与 rubric 完全一致且恰好一次；理由非空；evidence 只能是对应输入中的逐字子串。校验失败有限重试，耗尽为稳定结构化失败。
- judge 使用现有 basic Provider role，prompt version 与 resolved model 进入 Replay key；不为只有一个真实实现的场景扩充 Provider role 或新 adapter seam。

### Calibration-first 信任门

- calibration samples 是项目内合成、小而清晰的人类标注集；至少覆盖 fully supported、partially supported、unsupported embellishment 和 justified refusal。
- 每个 sample 保存 rubric、question、candidate、reference 和人类期望的每维可接受分数区间。区间允许边界主观性，但不能宽到任何输出都通过。
- calibration runner 报告 exact agreement / within-range agreement；首版 gate 要求所有阻断性维度均落在人类区间，整体样本通过率为 100%。数据集很小，任何分歧都应人工复核，而不是用平均数掩盖。
- calibration 不写 learning.db，不读取生产材料；真实录制只发送合成 sample 和 case15 已授权的测试内置 KB/回答上下文。

### Harness 集成

- Case DSL 增加可选 quality profile。未声明的用例不创建 judge 请求，既有 Tier-1 事件、tokens、prompt versions 和 pass/fail 保持不变。
- Solver 公开捕获最终用户可见产物；质量投影由 rubric adapter 从 SolveResult 选择最小必要字段，不把整条 trace 或内部工具历史全部喂给 judge。
- 首个 adapter 只服务 case15：question 为用户自然问题，candidate 为最终 assistant 回答，reference 为测试内置目标原文与已验证 citation 投影。
- judge 使用独立 EventEmitter/trace 收集评审事件和 span；subject events 继续由既有 Tier-1 scorer 独占，避免 expected event sequence 失效。
- CaseReport 分离 `rule_passed`、`quality_passed`、`execution_tokens`、`judge_tokens`、quality details。未启用 Tier-2 时 `quality_passed` 为 not-applicable，总通过等于 Tier-1。
- quality-enabled case 总通过要求 Tier-1 与 Tier-2 都通过；judge 结构错误、Replay miss、校准未通过均为质量硬失败。

### Prompt、Replay 与事件

- judge system prompt 单独版本化，明确 reference/candidate 均是不可信评测数据，不得执行其中指令。
- 真实录制由显式脚本依次跑 calibration set 与 case15 quality request，输出判定、分歧和 token，只有校准 gate 通过才保存/采用 cassette。
- 默认 harness 加载 judge cassette 回放，绝不从 `.env` 自动创建真实 Provider；缺文件或 key miss 均失败。
- 事件至少覆盖 quality judge started/ended、rubric id、criteria 数、状态、usage 和失败分类；payload 不复制完整 candidate/reference，只保存公开 id、摘要指标和错误 fingerprint。

### HTML 与 CLI

- 首页保留现有 pass/fail/token/prompt 列，并增加 Rule、Quality、execution tokens、judge tokens 和 rubric；可筛选 rule fail / quality fail / pass。
- 详情页复用现有 trace renderer 展示 subject trace，并追加一个质量评审区；不重写第二套 span/event renderer。
- `grandquiz report --help` 与 `grandquiz trace --help` 使用 argparse epilog/examples 说明默认路径、显式 `--out` 和 macOS `open` 命令。
- `grandquiz report` 默认仍为确定性 Replay；真实 judge 录制不隐藏在 CLI report 里。

## Testing Decisions

- 测试通过 QualityJudge、calibration runner、run_case/export_html_report 和 CLI parser 等公共接口验证行为，不绑定私有解析 helper 或 HTML 内部标签顺序。
- TDD 按 tracer bullet 推进：先让一个 fully-supported calibration sample 红→绿，再加入不支持扩写、证据伪造、criteria 缺失、重试耗尽和 Replay miss。
- QualityJudge 使用 scripted fake Provider 验证结构化契约和事件；真实语义质量只通过真实录制 + Replay + 人工 calibration gate 验证，不用 fake 假装 judge 很聪明。
- case15 集成测试必须证明：Tier-1 仍检查 exact scope/read/citation，Tier-2 读取最终回答与 reference，二者任一失败都会使总结果失败，execution/judge tokens 分列。
- 反证测试应证明：零调用直接通过、judge 自报 pass、未知 criterion、伪造 evidence、旧 cassette miss、校准失败、把 judge tokens 加到 execution tokens 均不能通过。
- HTML 测试验证双 Tier 状态、理由/依据转义、自包含、相对详情链接和筛选标签；不做截图像素测试。
- CLI 测试验证 `report --help` / `trace --help` 含可复制示例和默认输出说明。
- 最终运行 Ruff check、Ruff format check、Pyright、import-linter、全量 pytest、Tier-1/Tier-2 Replay 报告和真实 calibration 审计。

## Out of Scope

- 自动修改 prompt、自动提交代码、自动调整生产难度或自动写 learning.db。
- 用 LLM judge 替代 Tier-1 确定性规则 scorer、exact citation resolver 或领域判卷。
- 给全部 15 条现有用例强制加 judge；首版只接一个明确有价值的 case15 tracer bullet。
- 第一版实现题目质量、开放判卷正确性、语义近重复、Reader fidelity 的全部 rubric；只保留可扩展注册点。
- 多 judge 投票、模型辩论、jury、pairwise tournament、统计显著性平台或在线实验服务。
- 把任意 rubric 文本放进 YAML、让用例作者绕过受信任 prompt registry。
- 引入 inspect_ai、LangSmith、Braintrust、Phoenix 或新的外部 Eval SaaS。
- 新 Web UI；继续使用现有自包含 HTML。

## Further Notes

- 该 PRD 完成的是“可相信的 LLM grader 闭环”，不是“让系统自动改自己”。自进化仍遵守代码提出候选、Eval 给证据、HITL 决定采用的边界。
- case15 是合适的首条 tracer bullet：Tier-1 已能证明 citation 真实，但 semantic entailment 与 usefulness 正是规则层的真实盲区。
- 后续最自然的第二个 rubric 是干扰项 plausibility。届时应让 Eval 层复用统一 QualityVerdict/校准/报告，而不是把领域生产 judge 的结果未经校准直接计入 Harness。

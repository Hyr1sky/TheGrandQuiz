# PRD: M8 Eval Harness + 它护住的 dogfood 质量修复

Status: done（5 个 issue 全部完成：三项 dogfood 质量修复 + Tier-1 harness + 质量回归 scorer）
Created: 2026-07-06

> 把"可评测的 Agent Runtime"做实：将现有 8 个考核用例形式化为 inspect_ai 式 Task/Solver/Scorer + 报告
> （Tier 1 规则断言层，即 build-order step 8 验收线），并先修掉真机 dogfood 暴露的三个质量问题
> （语言漂移 / 重复出题 / 干扰项太弱）——其中两个的回归恰好是 Tier 1 的新规则 scorer。
> 术语以根目录 [CONTEXT.md](../../CONTEXT.md) 为准；遵循 architecture.md 的两层 eval 分层
> （规则断言=MVP，LLM-judge=二期）、ADR-0004（循环是 workflow / LLM 判卷，代码记账）。
> LLM-judge 质量 eval（Tier 2）不在本 PRD——见 Out of Scope。

## Problem Statement

我（用户 #1，学习者）已经用交互 CLI 真机跑了几轮考核，暴露出三个降低考核价值的问题：

1. **出题语言不稳定**——第二轮出了一道全英文题，第三轮又变成中文，两轮之间没有可控的语言策略，读起来割裂。
2. **重复出题**——连续两轮问的题目内容完全一样（只语言不同），复考薄弱概念时我被同一道题反复问，挖不深。
3. **选择题干扰项太弱**——干扰项一眼能排除，选择题沦为送分，考不出"看过觉得懂 vs 真懂"。

同时，作为 runtime 开发者，我现在**只能靠手动 dogfood 一轮轮碰运气发现这些问题**：它们是不可复现的、发生在 LLM 两个槽（出题 / 判卷）上的质量问题，现有 8 个用例用假 provider 灌 canned JSON，**从不判 LLM 槽的质量**，所以碰到的问题既没被测住、也无法量化"改 prompt 后到底好没好"。我需要把考核用例形式化成一套可回归、带成本与版本归因的 eval harness，让"手动发现"变成"每次提交自动打分、跑在真机录制的 golden cassette 上、零 token"。

## Solution

分两部分，先修 bug、再建 harness：

**先修三个 dogfood 质量问题**（每个一条竖切，各带一个确定性回归）：
- **语言可配置、默认中文**：给 LearningTask 加语言设置（默认中文，按 task 可覆盖），出题 / 判卷的语言指令由代码按该设置注入，enrich 角色改 temperature=0 让生成可复现。
- **无重复出题**：给考核循环加一个代码持有的"已问过"台账（锚定 item），出题时把已问过的题作为"换个角度提问"的约束下传，并在出题结构化门加一道归一化去重校验（重复即 ModelRetry），复考同一薄弱概念时保证每次是不同的题——这正是"LLM 判卷，代码记账"。
- **干扰项加硬**：把选择题 prompt 的"干扰项要合理"从含糊软约束改成硬约束、操作化（每个干扰项须是具体常见误解或证据里的邻近但错概念，选项平行、禁 meta 选项 / 题干回声）。

**再建 M8 Eval Harness（Tier 1 规则断言层）**：把现有 8 个考核用例形式化为一套 `evals/` 包——用例即 Sample、驱动 assess_once/ingest_resource 的测试装配即 Solver、对 AgentEvent 流与 span 树的断言即 Scorer——并新增两个规则 scorer（语言一致性、无重复），跑在真机录制的 golden cassette 上零 token 回放，产出带 pass/fail + token 成本列 + prompt 版本号的报告。只借 inspect_ai 的 Task/Solver/Scorer 形状，不 vendor 框架。

## User Stories

1. 作为学习者，我想让某个 LearningTask 的出题语言可配置、默认中文，以便无论材料是中文还是英文，考核语言都统一、不再跨轮漂移。
2. 作为学习者，我想按 task 覆盖出题语言（如某个英文主题设成英文），以便在需要接触原文术语时用材料语言被考。
3. 作为学习者，我想让复考同一薄弱概念时每轮拿到的是**不同角度**的题，而不是逐字重复的同一道题，以便真正把这个概念挖深、而不是被同一题反复问。
4. 作为学习者，我想让复考仍然锁定薄弱概念（薄弱优先不被破坏），只是换题面，以便"连续答对两次才销账"的判定建立在真被考过多个角度上。
5. 作为学习者，我想让选择题的干扰项是有迷惑性的近似项（针对常见误解），以便选择题能区分"看过觉得懂"和"真懂"，而不是送分。
6. 作为学习者，我想让上述三处改动不改变我已经熟悉的逐题交互体验（出题 → 答 → 判决 → 追问 / 正解 → 下一题），以便修复是无感的质量提升。
7. 作为 runtime 开发者，我想把出题 / 判卷的目标语言当作**数据**从 LearningTask 下传到出题 / 判卷，而不是写死在 prompt 正文里，以便语言决策归代码、LLM 只负责服从（符合"LLM 判卷，代码记账"）。
8. 作为 runtime 开发者，我想让语言指令以占位符形式存在于 prompt 模板、运行期按 task 语言填充，以便 prompt 版本号（内容哈希）跨语言保持稳定，而实际发出的 message（及其 replay_key）按语言区分。
9. 作为 runtime 开发者，我想让 enrich 角色以 temperature=0（或固定 seed）生成，以便出题在录制前就尽量可复现，减少真机漂移。
10. 作为 runtime 开发者，我想让"已问过的题"是一份代码持有、锚定 item 的会话内台账，以便去重逻辑是确定性的、可断言的，而非交给 LLM 自觉。
11. 作为 runtime 开发者，我想把已问过的题作为约束注入出题的 user message，以便引导模型换角度提问。
12. 作为 runtime 开发者，我想在出题结构化输出门（`_parse` / `_parse_mc`）加一道归一化去重校验，重复即触发 ModelRetry，以便复用现有有界重试把重复题挡在到达学习者之前（缝 3）。
13. 作为 runtime 开发者，我想把选择题干扰项的 plausibility 从软约束提升为硬约束并操作化，以便模型默认产出有迷惑性的干扰项而非废项。
14. 作为 runtime 开发者，我想在改动 prompt 后重录受影响的 golden cassette，以便内容哈希 bump 导致的 ReplayMiss 被解决、回放重新对齐。
15. 作为 runtime 开发者，我想补录当前缺失的选择题 / 追问路径 cassette，并去掉 test_assess_replay 里"预置观察中"的兼容 workaround，以便 golden 覆盖不再只有开放题路径、也不再偏离默认的 fresh-memory 流。
16. 作为 runtime 开发者，我想把现有 8 个考核用例形式化为一套 `evals/` 包（cases / graders / harness），以便"可评测的 Agent Runtime"这一卖点有独立、可运行、带报告的载体，而不只是散落在 pytest 里的断言。
17. 作为 runtime 开发者，我想把两个测试文件里重复的装配 / 汇总辅助（`_harness` / `_summ`）抽进一个共享模块，以便 eval harness 与单测复用同一套确定性装配、port 更薄。
18. 作为 runtime 开发者，我想让每个用例是一个 Sample（种子化的 KnowledgeItem 库 + 预置 Learning Memory 状态 + 脚本化作答 + rng 种子 + 期望的事件流 / span 断言），以便用例是声明式、可枚举的。
19. 作为 runtime 开发者，我想让 Solver 用注入的 Replay Provider 驱动既有 assess_once / ingest_resource 跑一遍并把发射的 AgentEvent 流交给 Scorer，以便 eval 完全确定、零 token。
20. 作为 runtime 开发者，我想让 Scorer 是读事件流 / span 树的规则断言（事件类型序列、payload 字段、记忆 / 存储状态、span 结构、provider 调用 / 角色），以便 Tier 1 覆盖确定性骨架、无需 LLM judge。
21. 作为 runtime 开发者，我想新增"语言一致性"规则 scorer（对每个 QUESTION_ASKED 的 question / options 算 CJK 字符比例分桶），断言每题等于 task 语言且全会话同桶，以便语言漂移一旦复发就被 eval 抓住。
22. 作为 runtime 开发者，我想新增"无重复"规则 scorer（对一次会话的 QUESTION_ASKED 做归一化后断言零逐字重复），以便重复出题一旦复发就被抓住，且它同时是去重修复的回归门。
23. 作为 runtime 开发者，我想让 eval 报告带 pass/fail + token 成本列 + prompt 版本号，以便回归可按成本与 prompt 版本归因（成本列正是 `Usage.total_tokens` computed_field 的用途）。
24. 作为 runtime 开发者，我想让 ReplayMiss 在 eval run 里硬失败、绝不静默通过，以便 cassette 与 prompt / 材料漂移时大声报错、提示"需重录"。
25. 作为 runtime 开发者，我想借 inspect_ai 的 Task/Solver/Scorer/log 形状但不 vendor 框架，以便保留手写 runtime、只取分离与报告的词汇（reference-map 已如此界定）。
26. 作为 runtime 开发者，我想让 M8 顺带补上 route_question_type 当前缺失的覆盖（经用例 8 的题型路由断言），以便这处确定性核心不再无测。
27. 作为 runtime 开发者，我想让去重台账的临时进程内 dict 实现打 `SKELETON` 标记并记入走骨架台账，以便"跨会话去重的 SQLite 表"这笔欠账不被遗忘。

## Implementation Decisions

**分层与范围（来自 architecture.md 的两层 eval 分层）**

- **Tier 1 = 规则断言层 = 本 PRD + build-order step 8 验收线**（"8 个考核竖切用例通过"）。
- **Tier 2 = LLM-as-judge 质量层 = 二期**（干扰项 plausibility / 语义重复 / 判卷正确性 / Reader 抽取保真度）——**不在本 PRD**，但本 PRD 重录的 golden cassette 与形式化的 harness 为其铺好底座。
- 三个 dogfood 修复里，语言与重复的回归是**便宜的确定性规则 scorer**，天然落 Tier 1；干扰项的真打分需要 judge，故本 PRD 只做 prompt 加硬 + 可选确定性反-tell 门，其 plausibility 打分显式留给 Tier 2。

**① 语言可配置（默认中文）**

- `LearningTask` 增加语言设置字段，默认中文；沿 selection → question → grading 下传（签名向后兼容，未设时退化为默认中文）。
- 语言指令以**占位符**形式置于出题 / 判卷 prompt 模板，运行期按 task 语言在消息组装处填充：**prompt 内容哈希版本号跨语言稳定**，而发出的 message（及 replay_key）按语言区分 → 不同语言天然是不同 cassette。
- enrich 角色 provider 调用设 **temperature=0**（或固定 seed），作为生成可复现的确定性补强。
- 语言的"设计归属"：MVP 落在 LearningTask（per-task，改动最小）；Preference Memory 的语言偏好（roadmap 已designation）留作后续更大范围的承接，不在本 PRD 强做。

**② 无重复出题（代码记账，不动 selection）**

- **不在 selection 修**（排除刚问的 item 会破坏薄弱优先复考 / ADR-0003）。修在出题侧：
- 加一份**代码持有、锚定 item_id 的会话内"已问过"台账**（MVP：进程内 `dict[item_id -> list[question]]`，在考核循环入口持有；打 `SKELETON`，正式版为与 Learning Memory 并列的 SQLite 表，见走骨架台账新增行）。
- 台账经考核循环入口 → assess_once → 出题函数下传；出题时注入"已问过以下、请换角度"到 user message。
- 出题结构化输出门（`_parse` / `_parse_mc`）加**归一化去重校验**：新题归一化后命中已问过集合即 raise ModelRetry，复用现有有界重试与缝-3 校验门模式。

**③ 干扰项加硬（prompt）**

- 重写选择题 prompt 的干扰项段：plausibility 由软约束升为硬约束并操作化——每个干扰项须是**具体常见误解**或**从 item 的概念 / 摘要 / 证据取的邻近但错**概念；所有选项在长度 / 具体度 / 语法上平行；禁 meta 选项（"以上都对/都不对"）、禁题干回声。
- 可选：出题结构化门加**便宜的确定性反-tell 门**（长度离群 / 题干词汇回声 / meta 选项）作为 ModelRetry 触发——**只挡表面泄漏，不测 plausibility**。是否加由实现者按投入判断，非必须。
- prompt 改动经内容哈希 bump 自动失效旧 cassette。

**cassette 重录**

- ①③ 改动 prompt → replay_key 变 → 重录受影响的 golden cassette；②注入已问过约束同样改 message → 也需重录。
- 补录当前缺失的选择题 / 追问路径 cassette；去掉 test_assess_replay 的"预置观察中"workaround，改为对齐当前 M3.4 路由默认流录制。
- 重录是**人机边界**（真机、需密钥），交回用户执行；agent 侧准备好录制脚本 / 使 ReplayMiss 大声失败并指明需重录哪条。

**④ M8 `evals/` 包（Tier 1）**

- 包形状按 architecture.md 已定：`evals/cases/`（用例，声明式）+ `evals/graders/`（规则 scorer；本 PRD 不含 LLM-judge）+ `evals/harness.py`（runner + 报告）。
- **Solver**：一个通用适配器，从 Sample 元数据重建确定性前置（种子化 KnowledgeItem 库、预置 Learning Memory 状态、脚本化 Responder、rng 种子、ManualClock、注入 Replay Provider），调既有 assess_once / ingest_resource 一次，捕获发射的 AgentEvent 流。
- **Scorer（规则）**：把现有 pytest 断言机械地重表述为读事件流 / span 树的 scorer——事件类型序列、payload 字段、记忆 / 存储状态、span 结构、provider 调用 / 角色五族。8 个用例全落此。
- **新增两个规则 scorer**：语言一致性（QUESTION_ASKED 的 question / options 按 CJK 字符比例分桶，断言==task 语言且全会话同桶）；无重复（QUESTION_ASKED 归一化后零逐字重复）。
- **共享装配**：把当前两个测试文件重复的 `_harness` / `_summ` 提升为共享模块，供 tests 与 evals 复用。
- **报告**：per-case pass/fail + token 成本列（来自 MODEL_ENDED payload 的 Usage.total_tokens）+ prompt 版本号（name@digest）。ReplayMiss 硬失败。
- **不 vendor inspect_ai**：只取 Task/Solver/Scorer/log 的形状与词汇（reference-map 界定）。

## Testing Decisions

好测试只断言外部行为、不耦合实现细节。沿用仓库已确立的三条缝（本 PRD 未新增缝，只在缝 1 上把断言形式化为 eval harness，与开发者在本轮讨论中确认一致）：

- **缝 1 — 事件 / trace 流（主缝，M8 harness 的落点）**：Replay Provider 驱动 assess_once / ingest_resource 跑脚本化输入，断言 AgentEvent 流。M8 把这条缝形式化为 Solver（跑）+ Scorer（断言），并新增语言一致性 / 无重复两个规则 scorer。**被测**：既有 8 个用例 + 两个新 scorer；语言修复的多轮语言一致、去重修复的会话内零重复都在此断言（用脚本化假 provider 即可复现"红"，修完转"绿"，无需 cassette）。
- **缝 2 — 确定性核心单元缝**：纯函数 TDD。**被测**：去重台账的归一化 / 命中判定；语言字段下传的确定性；（若加）反-tell 门的长度离群 / 回声判定。
- **缝 3 — 结构化输出契约缝**：喂畸形 / 重复 / 弱干扰项回放响应，断言 validator 拒绝并触发 ModelRetry。**被测**：出题门新增的归一化去重校验（重复题在到达用户前被挡）；（若加）确定性反-tell 门。
- **golden cassette 回归（缝 1 之上）**：重录后的 assess / ingest cassette 逐字节回放一致；ReplayMiss 大声失败即"需重录"信号。

Prior art：现有 8 个用例（`tests/test_assessment.py` / `test_ingest.py`，各以 `_harness` 装配 + 事件流断言）、`tests/test_assess_replay.py` / `test_ingest_replay.py`（golden cassette 回放）、缝-3 的幽灵引文 / 幽灵题门（出题 / 判卷 validator + ModelRetry）。M8 的规则 scorer 与新去重门是这些既有模式的直接延伸。

**测试哲学不变**：确定性核心（去重台账 / 语言下传 / 状态机 / 选题 / 事件信封）走缝 2 TDD；LLM 两槽的**质量**不 unit-TDD——Tier 1 只断言"可判分"的确定性属性（语言桶、逐字去重），"够不够难 / 判得准不准"的质量打分是 Tier 2（二期）的 LLM-judge，不在本 PRD。

## Out of Scope

- **Tier 2 LLM-judge 质量 eval**：干扰项 plausibility 打分（"不懂概念能否排除"）、语义近重复判定、开放 / 追问路径的判卷正确性、Reader 抽取保真度——全部二期。本 PRD 只做到 prompt 加硬 + 确定性可断言的属性。
- **跨会话去重持久化**：去重台账 MVP 是会话内进程内 dict；跨会话的 SQLite 去重表留后（走骨架台账记欠账）。
- **Preference Memory 语言偏好**：语言 MVP 落 LearningTask；Preference Memory 承接更大范围偏好留后。
- **selection 层的多薄弱项轮转**：本 PRD 的去重修在出题侧；selection 在多个薄弱项间的轮转分散是可选的补充，不在本 PRD。
- **vendor inspect_ai 框架本体**：只借形状。
- **真机录制本身**：重录 golden cassette 需密钥、属人机边界，由用户执行；本 PRD 交付到"使 ReplayMiss 大声失败并备好录制脚本"。
- 其余 kernel 层（M4 HookManager / M5 ContextBuilder / M6 RecoveryPolicy）、审批 / 作答的 suspend-resume 正式化、Reader 抽 `kernel/subagent.py`——各自里程碑，不在本 PRD。

## Further Notes

- **切片顺序（各一个可验收 issue）**：① 语言可配置 → ② 无重复出题 → ③ 干扰项加硬 →（用户重录 cassette）→ ④ M8 evals 包（Tier 1）。①②③每条自带确定性回归（缝 2/3 + 缝 1 多轮断言）；④把 8 用例 + 两新 scorer 形式化。Tier 2 单列、二期。
- **对标（学习 by 模仿，详见 docs/reference-map.md）**：eval→inspect_ai（Task/Solver/Scorer 分离 + eval log/replay view，取形状不 vendor）；结构化输出去重门→pydantic-ai 的 output_type + ModelRetry；语言 pin 是"输出严格"的一部分（呼应现有 coercion 的 Postel 定律思路）。
- **面试叙事点**：语言 bug 有三重叠加原因（无 pin + 每轮不同 item + 真机 temp≈1），prompt pin 治标、temp=0 + 语言即数据才是真·可复现；重复出题的修复体现"复考是设计意图、重问同一题才是 bug"，且刻意不在 selection 修以保薄弱优先——是"LLM 判卷，代码记账"的又一处落地；干扰项 plausibility 是"确定性 harness 结构上看不见、必须靠 judge"的教科书案例，正好界定 Tier 1 / Tier 2 分层。
- **确定性纪律**：所有新逻辑（语言下传 / 去重台账）走注入的时钟 / 种子化 RNG，不引入新的非确定源；cassette key 仍是 sha256(messages)+role+model_id，任何 prompt / 语言变化自动换键、旧 cassette 大声失效。

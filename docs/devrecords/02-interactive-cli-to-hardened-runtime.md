# 开发日志 · 阶段二：从可交互工具到可评测 / 可观测 / 可恢复的加硬 runtime

> 记录区间：M8 Eval Harness → 运行时可见（HTML trace）→ 自评复盘 → 窄口径收口 → M6 RecoveryPolicy → M4 HookManager。
> 截至本记录：**321 测试全绿**、pyright strict 0 error、ruff clean、**import-linter 分层门 KEPT**、eval 10/10 零 token 回放。
> 承接 [01](01-assessment-slice-to-interactive-cli.md)（地基不重复）；本篇记"把三块招牌——可观测 / 可评测 / 可恢复——从宣称做成代码"的过程与判断。

---

## 0. 这一阶段的主线（一句话）

阶段一把考核竖切端到端跑通；阶段二**不加新功能，而是把"可观测 / 可评测 / 可恢复的 Agent Runtime"这句卖点逐块兑现成可验证的代码**——并且中途停下来**对自己做了一次对抗式自评**，据此排出"先诚实收口、再加硬 kernel"的节奏。工程成熟度的体现不在写得多，而在**敢诚实评估 + 按判断力排序**。

---

## 1. 逐块 · 为什么 · 巧思 · 坑

### M8 — Eval Harness（把"可评测"做实）
- **形态**：`evals/` 用 YAML 声明用例（`id/kind/setup/expected_events`）+ Python 规则 grader（有序事件序列 / payload 字段 / 记忆末态 / span 树形状 / provider 角色分槽）+ harness。跑在**同一条事件脊柱**上——eval 用例本身就是一条 trace。
- **巧思 · golden cassette 零 token 回归**：真机对 qwen+deepseek 录一次 cassette，CI 逐字节回放、不触网、不烧钱；prompt 漂移 → `ReplayMiss` 变红 = 重录信号。
- **巧思 · dogfood bug → 回归探针**：真机试用抓到两个 bug（跨轮语言漂移、重复出题），修复后**做成 case9/case10**——删掉语言注入 / 去重门即变红。eval 真在守护行为，不是走过场。
- **坑 · 决策6"大声失败"**：`ReplayMiss` / 任何异常 → `passed=False`，**绝不静默计过**（有专测钉死）。否则 cassette 缺录会被当"通过"，eval 就成了摆设。
- **诚实边界**：只到 **Tier-1 规则断言**；Tier-2 LLM-judge（出题语义质量 / 判卷正确性）尚未建——文档明标 scoped-out，不假装双 Tier。

### 运行时可见（把"可观测"从宣称变成可点开的 artifact）
- **EventSink → processor 管线**：`register(processor)` 形式化订阅者（对标 openai-agents `TracingProcessor`，不 vendor），**逐订阅者异常隔离**（只吞 `Exception`、`BaseException` 照传）——闭掉了阶段一那个"Rich markup 崩炸整轮"的坑。为 Tier C 的 OTLP 导出留口。
- **自包含 HTML 查看器**（`kernel/report.py` 纯函数）：把事件流 + span 森林 + token/latency 渲成**零 CDN / 零 JS / 可离线打开**的单文件；被 eval 报告与真机 trace 视图**共用一个渲染器**。
- **真机落 trace**：`grandquiz quiz/ingest` 经 `register(TraceStore)` 落**独立 trace.db**（与 learning.db 分开、各自 `user_version`）；`assess_once` 签名一行不改——可观测是脊柱投影、非业务耦合。`grandquiz report / trace <id>` 一条命令产出可点开的 artifact。

### 自评复盘（这一阶段的方法论亮点）
- **做法**：起一个 workflow——5 路读者分层核实**磁盘上的真实状态** vs 架构搭建顺序 / ADR，再过**两道独立对抗评审**（工程严谨度 + 简历差异化）。两评审各自独立收敛到 **B+ / U 形**同一结论。
- **诚实诊断**：搭建顺序两头实（trace/replay/eval/SQLite）、腰部空（step 4/5/6）；三招牌里当时只"可观测"真兑现，"可恢复"几乎为零，"可评测"半边。**自评抓到一个被假绿测试掩盖的真 bug**（见下）。
- **排序判断**：先窄口径诚实收口 → 再加硬 kernel（M6→M4→M5）→ 才谈 ReAct。理由：现在扩 ReAct = 从零建第二套工具系统，"一条 runtime 托两种编排"的故事会崩成"两个 domain 各自烘焙"。

### 窄口径收口（让所有"声称 done"为真）
- **坑 · SQLite 静默丢 `language`（真 bug）**：`SqliteLearningStore` 只存 task_id/title/domain，`LearningTask.language` 跨往返被退回默认中文——**反讽地抵消了刚做的语言修复**；且 `test_matches_dict_store` 未覆盖该字段故**假绿**。修：migration 0002 补列 + 往返 + 补覆盖该字段的断言。**教训：'两实现等价'的不变量，测试没逐字段覆盖就是假的。**
- **Preference Memory**（补 ADR-0003 的 M7 缺口，此前零代码）：镜像 `LearningMemory` 建协议 + dict + SQLite（parity 逐字段含 confidence）+ migration 0003；第一个偏好 = `question_language` 显式设置，出题按 **偏好 > task 默认 > 中文** 覆盖。**判断**：难度偏好 / 偏好推断器延后到 ReAct——当前是确定性选择题、无自由答题的行为信号，谈不上推断。
- **import-linter 进 CI**：把"kernel 领域无关"从 grep 约定升为**自动门**（`kernel ↛ domain/interfaces/evals`）。这条门恰好在下一步 M6/M4 的设计里**当场起了约束作用**（见下）。

### M6 — RecoveryPolicy + ErrorClass（"可恢复"有了真代码）
- **背景**：错误处理散落——CLI `run_quiz` 硬编码 `except (QuestionError, GradingError)` 跳过本轮（SKELETON #7），domain 多处"优雅降级属 M6"原样冒泡。
- **巧思 · 异常自带分类，绕开分层门**：`kernel/recovery.py` **不能 `isinstance` 领域异常**（import-linter 门会红）。解法——异常**自带 `error_class` 标**：domain/providers 各自 import kernel 的 `ErrorClass` 给自身打标（domain→kernel 合法方向），kernel 只读标分类，**未带标 → 默认 FATAL（fail loud）**。刚立的分层门直接塑造了设计，正是加硬层该有的样子。
- **不可破不变量**：`ReplayMiss` → FATAL → 必冒泡、**绝不 SKIP**（决策6），mutation 钉死。`assess_once` 一行不改（eval 里 ReplayMiss 照样硬失败）。recovery 决策发 `RECOVERY_DECIDED` 上脊柱——错误进 trace，现在决策也进。SKELETON #7 销账。

### M4 — HookManager（Hook 体系 interceptor 半边）
- observer 半边阶段一已在 EventSink；M4 补 **interceptor（`before_*` 可改参 / 可阻断）**。同构 M6 的分层解法：`kernel/hooks.py` 零 import domain，domain 侧注册 hook，kernel 泛型 `run_before(point, value)` 按注册序折叠 interceptor。
- **巧思 · veto + fail-closed**：interceptor 抛 `HookVeto` = 阻断；抛**非-veto 异常** = 隔离（不炸 turn）+ 记录 + 发事件，但**转成 veto 冒泡、绝不静默放行**（沿用 M6"宁挡勿放"——注入中和器出 bug 宁可挡住也不喂未中和内容给 LLM）。
- **真客户**：reader 的注入中和 `neutralize_fence` 从内联直调改成注册式 `before_untrusted_read` interceptor——证明"改参"落在真实安全边界，也给未来 ReAct 的 `before_tool` 立好挂点。

---

## 2. 一个过程坑（诚实记录，讲"对抗验证的卫生"）
M6 的 4 路对抗验证一度报出一个 **pytest 间歇性红的"blocking"**——但那是**验证脚手架的伪报**：我让 4 路 verify **共用同一个 worktree**，其中 mutation 路在 `sed`/`git checkout` 翻转再撤销代码时，与 correctness 路的**全量 pytest 并发**撞车，pytest 读到瞬时被改坏的文件 → 间歇失败（correctness 还误诊成"枚举身份不确定"）。**隔离后连跑 45/45 全绿**。修法：mutation 型 lens 各自独立 worktree，或**单个串行验证员**（mutation 做完再跑全量门、绝不并发）——M4 换成后者，一次过、无伪报。**教训：对抗验证本身也要保证隔离，否则会自造假信号、浪费定位时间（疑罪从有是对的，但要能快速证伪）。**

---

## 3. 巧思清单（阶段二新增，接 01 的表）
| 巧思 | 一句话 | 在哪 |
|---|---|---|
| golden cassette 零 token eval | 真机录一次、CI 逐字节回放、prompt 漂移即红 | evals + providers/replay |
| dogfood bug → 回归探针 | 真机抓的 bug 修完做成 case9/10，删门即红 | evals/cases |
| 自包含 HTML 查看器 | 零 CDN / 零 JS / 可离线，一个渲染器两处共用 | kernel/report |
| processor 管线 + 异常隔离 | 富订阅者 + 逐订阅者隔离，为 OTLP 留口 | kernel/events |
| 异常自带 error_class 标 | kernel 只读标分类，绕开 kernel↛domain 门 | kernel/recovery + domain 异常 |
| ReplayMiss 恒 FATAL | recovery 绝不 SKIP，保 eval/replay 契约 | kernel/recovery |
| hook fail-closed | 坏 interceptor 隔离但转 veto 冒泡、不静默放行 | kernel/hooks |
| import-linter 自动门 | "领域无关"从约定升为 CI 门，反塑 M6/M4 设计 | pyproject + CI |
| 消费者驱动、拒投机基建 | 无消费者的 kernel 层不预建（M5 缓办，见下） | 方法论 |

---

## 4. 一个判断：M5 ContextBuilder 缓办到 ReAct
建完 M4 后重估：ContextBuilder（分区拼装 + token 预算 + 工具结果截断）**当前无消费者**——`assess_once` 每题重建 fresh messages 不累积、`run_turn` 裁剪平凡且无真调用方；其载荷价值只在**多步循环 = ReAct**。故不在此刻建投机基建，**把 M5 折进 ReAct 阶段与真消费者共建**——契合 CLAUDE.md 自己的"不在竖切前打磨 kernel / YAGNI"纪律。"当前版块"（扩 ReAct 前收口 kernel）实质已达成：错误裁决 / hook 机制 / 分层自动门 / Preference 记忆到位，ReAct 前置跑道已通。

---

## 5. 下一步：ReAct 阶段（把专用考官收进通用编排 + 让 eval 成为自进化 gate）
- **形态**：考官（ingest + assess）降为 domain **工具 / 子代理**，收进一个**最小核心 ReAct 主体**（runner 升 tool 循环 + tool 注册表 + M5 ContextBuilder 共建 + 可选 kernel/subagent.py 提取）——兑现"一个领域无关 runtime 托两种编排：确定性 workflow + 自由 ReAct"。
- **eval 扩到整条 trajectory**：从"单槽 replay"升到"多步轨迹评测"（选对工具没 / 恢复对没 / 是否越权）——形成整体闭环。
- **迭代 gate → 自进化**：现在缺一个迭代 gate；有 trace（观测）+ grader（评分）+ 确定性 replay（可复现）之后，可建"eval 作为 CI 回归门 → 指标驱动的 prompt/policy 优化 → 只接受可测改进的 gate"——把项目做成一个**有边界、可度量、被门守住**的自改进系统（对标 DSPy 的指标编译 / inspect_ai 的轨迹评测）。这正是本项目"可观测·可评测·LLM 判卷代码记账"资产的最高杠杆兑现。

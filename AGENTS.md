# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目是什么

**考核驱动的个人学习工具**，工程内核是一个可观测、可恢复、可评测的 Agent Runtime（Python 3.12+）；
作者本人是用户 #1，同时作为 AI/Agent 工程师方向的简历项目。核心循环是"考核"：学完材料 → 被拷问
→ 暴露薄弱概念 → 记入记忆 → 下次优先考薄弱点。

**当前状态（2026-08-04）**：可观测/可恢复/可评测的 Agent Runtime 已落地——`kernel/`（events/runner/tools/
hooks/context/clock/recovery/trace/db）+ `providers/`（OpenAI 兼容 + Record/Replay）+ `domain/learning/`
（考核竖切 ingest→深读→出题→判卷→薄弱记账）+ `interfaces/cli/`（ingest/quiz/react/report/trace 子命令）
+ `evals/`（17 条 Tier-1 规则用例 + case15 校准优先 Tier-2 质量门）。**最小 ReAct 对话核（R1）与全局 KB 重构均已落地**（`grandquiz react`
可真机跑：自然语言选材料 + 定题型的持久全局知识库考核）；上下文压缩、真实网络抓取、跨会话去重与
自适应难度第一阶段也已完成。[稳定性加固](docs/devrecords/03-stability-hardening-closeout.md) 已收口；其上的
[修订化文档结构](docs/devrecords/04-revisioned-document-search-foundation.md) DS-S1–S4 代码也已落地：不可变 revision/tree、精确
Evidence、自然节点 Reader、FTS5 与有界 Agentic Search。生产 DB 已备份并迁移到 schema v11；新增真实材料后现为
4 resources / 122 items / 4 revisions / 1723 nodes / 1723 FTS rows / 169 evidence（117 resolved / 52 unresolved）。
Reader、ReAct case14/case15/case17 与 Tier-2 judge 已用真实模型录制；Web Acquisition WA-S1–S5 已落地
（Trafilatura、质量门、可选 Tavily / SearXNG、Search/Fetch Replay、case16/case17），免信用卡 Key、
loopback-only 单容器、两种 provider 真实连通与 search → 用户选择 → ingest ReAct dogfood 均已验收。
Local Web 的 LW-S1–S5 与 Web Runtime WR-O1–O4 也已落地：FastAPI 资源/大纲/有界节点、
GroundedDocumentAnswer run、稳定 SSE、取消、精确 citation 与 loopback 启动，“墨迹星图”亮/暗 React
Article/Assessment Workspace，复用 `AssessmentSession` 的逐题考核、可审计 Evidence reveal、幂等提交/下一题，
以及 exact 当前材料、跨轮 Chat cursor、安全实时 `TraceObservatory`，并提供 Markdown/Text 上传、公开 URL
导入、持久状态与跨重启候选审批。v0.1.0 功能 RC 已完成安全 Markdown、Assessment trace 终态、Chat 并发拒绝、
Provider 原生 delta 流式 Chat、turn-scoped 真取消、版本化首次引导、稳定观测投影与确定性 Web Scenario Bot
收口；当前收口为 LW-S7 发布门，LW-S6 完整资源/知识点管理进入 v0.1.0 后 backlog。Learning Model v2
基础闭环也已落地：长期白名单 Journal/outbox、可重建 Attempt、判决纠正/reconciliation、受控词表与分类审核、
LearnerProjection 和稳定审查导出；自动 Demand Judge、Diagnosis/Misconception 仍受 Eval gate 限制。
v0.2 RC 又补齐非代码 Markdown 节点中 CommonMark 可见 Evidence 到 raw source 的唯一映射，以及 Acquisition
`code / stage / reason` 安全错误信封；领域失败会进入 Trace error 统计，并在 CLI/Web 管理态可见。多题考核由
`AssessmentPlan` 统一 CLI/Web/FastAPI 有序题型意图，开放题由 `QuestionSpec` 统一评分点、Evidence、参考答案
和逐点评判；v0.2 功能 RC 已关闭。发布回归又修复了考核导航覆盖 Chat 回复，并为输入框增加空白态 `↑` 恢复
上一问题；默认 Eval/HTML 只做离线 Replay，Rule/Quality 与 execution/judge 成本分列；v0.3 代码 RC 已接通 Web
approved-only 分类筛选、生产判卷人工盲标 calibration gate 与判决纠正到本地 Eval 候选；v0.4 进一步补齐
人工授权的材料发现 inbox、Acquisition 桥接、Eval 隐私审核与内容哈希不可变快照。首批 19 条盲标的生产
校准与固定 10 条开发样本的 Flash/Pro × thinking 2×2 pilot 已完成；Report v2、预注册核心评分点和代码三值
聚合已落地；后续语义判卷收口将已见开发集原始口径提升至 87.50% 逐点准确率 / 66.67% 三值一致率，
人工残差裁决 overlay 后为 89.58% / 75.00%；合成挑战 12/12 通过但强制为 exploratory。v3.1 使用同一
Pro/Thinking Off cohort 真实复测后保持全部逐点决定不变，总 Token 降低 15.41%；真实 cassette 已重录并
通过离线 replay。随后 12 条全新真实 blind holdout 的正式 gate 仅为 68.75% 逐点准确率 / 58.33% 三值
一致率，并有 3 个 serious FN 与 1 个结构化判卷失败，因此自动策略继续关闭；该 Snapshot 已揭盲，只能
转为开发回归集。针对结构失败的 v3.2 补丁已移除 Evidence 的 80 字诱导，明确连续原文且禁止省略号/
拼接，重试会回显非法片段并要求改选；Calibration Report v4 将合法输出率与合法输出上的语义质量分列，
gate 仍要求 100% 合法输出。真实开发回归中最终合法输出率从 91.67% 升至 100%、重试从 2 降至 1，
但首轮 H10 仍使用省略号；统一合法输出口径后新旧逐点准确率均为 75%，不能改变 holdout 失败结论。
随后将自由复制答案 Evidence 收窄为代码生成的唯一句子单元 ID：真实开发实验首轮合法输出 12/12、
重试归零、Token 下降 10.73%，语义指标不变；生产 Grader 已只接受 ID 并由代码解析精确原文。
进一步的 nested rubric prototype 虽修复 H02/H10 并表达 H07 的 OR 边界，却仅 3/4 合法、4 次重试且
Token 比 flat baseline 高 282.77%，因此拒绝引入 Boolean rubric schema，继续使用 flat atomic
ExpectedPoint。该实验还推动 Cassette 对同 replay key 保存有序响应序列，旧单条 fixture 保持可读。
随后补齐答卷 provenance 契约：只有 `unassisted_human` 可进入 release gate，模型/辅助/合成答案在
Compilation 与 Report 中均强制为 exploratory。已在 12 道揭盲 Development Gold 题上用
DeepSeek V4 Pro / Thinking Off 生成 30 条模型答卷（12 完整 / 12 部分 / 6 合理误区），仅作为新的
Synthetic Challenge；13 个录制响应合计 7,665 Token，不污染 Holdout 03 新题源。30 条 assistant
screening 已完成（对 6 / 勉强 12 / 错 12），等待 owner 复核 6 组真正的 rubric 边界；Holdout 03 首批
10 个新 QuestionSpec / 40 个原子评分点已冻结并通过固定源码 Evidence 逐字校验，owner 的 10 条独立
闭卷答卷也已锁定；owner 接受 Codex 的对 3 / 勉强 5 / 错 2 初筛，确定性编译得到 10 条 eligible /
0 excluded，全部为 `unassisted_human`。生产 Grader 尚未运行，且该批仍不足以单独构成 release gate，
第二批 GQ4-H11–H20 的 10 个新 QuestionSpec / 40 个原子评分点也已独立冻结并通过 Evidence、排重和
防泄漏校验；owner 的第二批 10 条闭卷答卷已锁定并接受 Codex 的对 5 / 勉强 5 / 错 0 初筛，第二批
编译同样为 10 eligible / 0 excluded；两批合计为 20 个新
QuestionSpec、80 个评分点、20 条 eligible（对 8 / 勉强 10 / 错 2），排除 0 条。Compilation 已拆开
`question_id` 与独立答卷 `sample_id`；两位朋友又独立完成 10 条自然答卷，owner 终审后正式 cohort 达到
30 条人类答卷 / 20 个 unique QuestionSpec / 120 个逐点评判（对 17 / 勉强 11 / 错 2）。本地隐私审核
冻结的 30 eligible / 0 exploratory Dataset Snapshot
`71a504b0725e41e9992e217de1daf89429f1b126faaa281c7d8822558d306743` 已用 DeepSeek V4 Pro / Thinking Off
运行正式 gate：合法输出 30/30、逐点准确率 90.83%、严重 FN/FP 0/0 均通过，但三值一致率 25/30 =
83.33% 低于 85%，因此 gate 失败。31 个真实响应已录制并通过 30/30 离线语义 replay；该 cohort 已揭盲，
只能作为 Development Gold。随后受限 Required Claims seam 已实现并在 12 条已揭盲 Development Gold 上
真实验证：输出 12/12 合法，但三值仅 8/12、逐点 37/48，新增六个逐点分歧且 Token 比 flat baseline
增加 50.68%，预注册实验失败；因此暂停新 holdout，seam 只作为可审计实验能力，不得描述成已通过的
默认判卷策略。owner rubric audit 后的紧凑 claim 真实实验虽以 18,561 Token 在首阶段解决 4/4 个高影响
目标，但 5 次聚焦复核没有修复错误且新增一个 false positive，使 aligned point 从 37/43 降为 36/43、
三值从 9/12 降为 8/12；按预注册退出条件，Required Claims 默认路线已否决，不再叠加 Judge 或消耗新
holdout。代码已把新题生成与默认判卷恢复为 flat atomic ExpectedPoint + AnswerEvidenceUnit ID；
claim-aware 分支仅保留兼容与实验入口。一次性判卷澄清的纯领域 planner/state machine 已实现，但
Holdout 03 的 30 条报告中 `uncertain=0`，因此尚未接入 AssessmentSession、CLI/Web 或记账。随后 12 个
决定性 missing point 的真实二分类信号原型结构 12/12 合法、9,587 Token，但只找回 2/5、误追问 1/7、
precision 66.67%，预注册失败；误差证明下一轮必须用独立三态 Interaction Gold 区分无支持、真实歧义与
初判冲突，不能从 grading matched/missing 标签直接派生追问。owner 已接受 12 条三态标签（6 / 2 / 4），
第二轮 Support Relationship 真实原型得到合法 11/12、exact 9/12、no support 5/6、ambiguity 0/2、
direct support 4/4、3 次重试、12,342 Token；预注册失败且未接生产。其旁路的用户主动申诉竖切已落地：
开放题允许一次补充，原答不可变，同一 Grader 重判后经追加式 Verdict Correction 重放学习状态；这不代表
自动 ambiguity classifier 已获准。当前 pytest 为
`1036 passed`，
Ruff、Pyright 与 import-linter 全绿。Web unit 为
`50 passed`，Playwright 桌面/移动端为 `20 passed`。Reader 真实基线为
105/105 个可考节点 exactly-once 覆盖、12 个候选、0 重复、单次请求 8715 prompt tokens。DS-S3 的生产 ingest/
人工筛选已由 trace `2515ec1af79a4a0a9860993b4a35beb9` 通过只读审计（141 个可考节点、2 批、34 条 exact
evidence）。DS-S4 生产 trace `46b91c61c1c24ebabc94be97db31bb16` 也已通过 selected search → 3 次 bounded
read → 2 条 node citation 联合审计，只读 2762/20721 字符（13.33%）；文档结构 PRD 的 DS-S1–S4 现已 done。
DS-S5 KnowledgeRelation 因没有关系增益证据而按 eval gate 关闭，本轮不建 schema/抽边/消费路径。
设计权威仍在 `docs/` 与 `CONTEXT.md`。
**动手写代码前按序读**：

- [CONTEXT.md](CONTEXT.md) — 领域语言权威表（先读这个统一术语）
- [docs/architecture.md](docs/architecture.md) — 目标架构、两条核心设计判断、搭建顺序
- [docs/roadmap.md](docs/roadmap.md) — MVP 考核竖切、领域模型、eval 用例
- [docs/adr/](docs/adr/) — 十一个不可逆决策（0001 提取式迁移 / 0002 概念同一性 / 0003 记忆四收二 /
  0004 循环是 workflow / 0005 全局 KB·消解 LearningTask / 0006 用户显式题型覆盖 / 0007 稳定资源修订与
  item 身份 / 0008 修订化文档树·精确溯源·分层知识图 / 0009 Local-first Web Interface /
  0010 长期学习事实与完整运行 Trace 分离 / 0011 受限 Required Claims 判卷契约）

## 常用命令

依赖 [uv](https://docs.astral.sh/uv/) 管理：

```bash
uv sync --dev                                # 创建 venv 并安装依赖
uv run pytest                                # 全部测试
uv run pytest tests/test_smoke.py::test_version  # 单个测试
uv run ruff check .                          # lint（CI 不带 --fix）
uv run ruff format .                         # 格式化（CI 用 --check）
uv run pyright                               # 类型检查（strict 模式）
uv run pre-commit install                    # 安装提交钩子（ruff + pyright）
.venv/bin/grandquiz audit-doc --help         # 只读核验 DS-S3/4 dogfood trace + DB
```

CI（`.github/workflows/ci.yml`）在 push / PR 上跑 lint + format check + typecheck + test，全绿才能合并。
pytest 配置了 `asyncio_mode = "auto"`，异步测试不需要手动加 `@pytest.mark.asyncio`。

## 架构核心：事件总线是脊柱

这是整个系统最重要的设计判断——hook、trace、流式输出、eval replay **不是四个独立模块，
而是同一条 `AgentEvent` 事件流的四个消费者**：

- Runner 在每个生命周期节点发射结构化 `AgentEvent`
- trace = 事件的持久化；hook = 事件的订阅者；SSE/CLI 流式输出 = 事件的网络投影；eval replay = 事件的回放

任何新基建模块都应建立在这条事件流上，而不是另起一套回调系统。

## 分层结构（规划中，kernel 先行）

```text
src/grandquiz/
├── kernel/       # 通用 Agent Runtime：events / runner / tools / hooks / context /
│                 #   memory / recovery / trace / subagent / approval
├── providers/    # LLM provider（OpenAI 兼容 + DemoEcho）、Record/Replay、token 用量
├── domain/learning/  # 学习领域：全局 KB（LearningResource → KnowledgeItem）+ 考核 / 记忆 / 难度
├── interfaces/   # 可插拔通道：api/（FastAPI REST+SSE）、cli/（开发期主力界面）、asr/（语音）
└── evals/        # 用例 DSL（YAML）+ 规则断言 / LLM judge + harness
```

**分层守卫：`kernel/` 禁止 import `domain/`**（已由 import-linter 在 CI 强制）。
搭建顺序按依赖关系排定（见 architecture.md 末尾）：trace / 事件 / replay 最先建，
hook、recovery、eval 全部建在其上。

## 写代码时必须遵守的设计约束

来自 architecture.md 已对齐的决策，不是建议：

- **核心循环是 workflow，不是自由 ReAct**（[ADR-0004](docs/adr/0004-core-loop-is-workflow-not-free-react.md)）：
  考核链路是确定性骨架，LLM 只在"出题""判卷"两个槽里被调用；状态机转移、选题候选集、Learning Memory
  写入全是代码。自由 ReAct 只用于开放编排。一句话：**LLM 判卷，代码记账。**
- **事件是信封**：`AgentEvent` = type + 元数据 + 不透明 payload，kernel 泛型分发、不认识具体类型；
  领域事件在 domain 层定义、经 kernel `emit()` 上同一条脊柱（kernel 保持领域无关）。
- **Hook 分两类语义**：interceptor（`before_*`，可改参可阻断）vs observer（`on_*`/`after_*`，只读）。
  Hook 抛异常必须被隔离，不能炸掉整个 turn。
- **确定性基建第一天做对**：时钟 / 随机数走注入（`Clock` 抽象 + 种子化 RNG），否则 replay 永远对不齐。
- **先定 trace schema 再写功能**：`turn_id / span_id / parent_span / type / input / output / tokens /
  latency / error`，span 成树。错误本身是一种 AgentEvent，自然进 trace。
- **跨轮次裁剪**：历史只保留最终 assistant 回答，丢弃 tool 调用中间过程（旧仓库的已知坑）。
- **注入防护进 MVP**：抓取的网页 / GitHub 内容是不可信输入——打"不可信"标记 + system prompt
  硬约束 + fetch 层大小 / 超时 / 域名限制。
- **subagent 判据 + 结构化输出契约**：subagent 仅用于"隔离大上下文 + 可验证输出"（MVP 唯一 subagent
  是 Reader；出题 / 判卷是工具）；其返回值用 pydantic schema 强制校验，失败自动重试。
- **审批门是可挂起 / 可恢复的 turn**：发 ApprovalRequested 事件 + 持久化待决状态 + 凭 token 恢复，
  不是阻塞 `input()`；CLI MVP 可用阻塞 prompt 实现，但接口形状第一天按 suspend/resume 定。
- **SQLite 迁移**：版本号 + 顺序 SQL 文件，不上 alembic。
- **Prompt 版本管理**：prompt 模板独立于代码存放，trace 记 prompt 版本号。

## 代码参考

[ADR-0001](docs/adr/0001-extract-not-slim.md) 记录为何使用独立骨架建立本仓库；
[docs/reference-map.md](docs/reference-map.md) 只记录当前项目采用的公开外部参考。参考实现不是依赖，
任何借鉴都必须重新适配本仓库的事件脊柱、分层守卫和确定性测试契约。

## 开发节奏与代码树约定

- **走骨架，竖切先穿透**：trace/replay 先行后，立刻拉一条最小可跑的考核竖切（搭建顺序 step 3），
  kernel 各层由真实 domain 拉动着逐层加硬——step 3 里可用 dict 假装 memory、阻塞 prompt 假装审批门，
  step 4-7 再换正式实现。**不要在竖切跑通前打磨任何 kernel 层。** 每处临时假实现打
  `# SKELETON(Mx):` 标记并记入 [docs/skeleton-ledger.md](docs/skeleton-ledger.md)（走骨架替换台账），
  防止"跑通即遗忘"；替换 PR 同步销记录。
- **一个 PR 一个可验收行为**：每个 PR 对应 architecture.md 搭建顺序里的一条验收标准，保持 CI 全绿；
  build order（step 1→8）即 backlog，不提前建满 issue。
- **测试分工**：确定性核心（状态机 / 选题 / 事件信封 / 销账）走 TDD（红-绿-重构），是 eval 命门；
  LLM 的两个槽（出题 / 判卷）不 unit-TDD，靠 replay 录放 + eval harness 验证。
- **代码树跟依赖规则和真实文件走，不跟 aspiration 走**：保持 `kernel/providers/domain/interfaces/evals`
  分层（它本身就编码了"领域无关 runtime"这一卖点，比扁平铺开更讲故事）；单文件概念保持扁平，
  **不预建空文件夹**。`domain/learning/` 的嵌套即使只有一个领域也保留（标示 runtime 领域无关）。
- **子文件夹按角色分组，但用 git 共同改动历史验证边界是否为真**（2026-07-13，`domain/learning/`
  拆出 `ingest/`(fetch+web_fetch+reader+pipeline)/`assessment/`(engine+question+grading+routing+
  selection)/`tools/`(每个 ReAct 工具一个文件) 三个子包后的复盘）：候选分组先用
  `git log --name-only` 查文件两两共同出现次数——真被同一个改动理由驱动的文件（如
  `assessment.py`↔`selection.py` 5/5 次一起改）该分组；只是"长得像同一种模式"但从未一起改过的
  （如 `store.py`/`memory.py`/`preference.py`/`asked_questions.py` 这套 Protocol+Dict+Sqlite
  三段式，两两共同出现趋近于 0）不该只因形状相似就分组——那是审美分类，不是 CCP
  （Common Closure Principle）意义上的真边界，摊平反而更诚实。子包内彼此 import 一律走精确
  子模块路径（如 `assessment.selection` 而非包顶层 `assessment`）；包 `__init__.py` 只在**不产生
  循环 import** 时才 re-export 主入口（`ingest/__init__.py` 转出 `ingest_resource`；
  `assessment/__init__.py` 刻意留空——因为 `memory.py`（顶层）依赖 `assessment.grading`、
  `assessment.engine` 又依赖 `memory.py`，若 `__init__` 贸然拉起 `engine.py` 会成环）。

## 工程规范

- **提交规范**：conventional commits；issue 驱动开发，每个 issue 对应一个独立可验收的 PR
- **决策记录**：架构级决策写入 `docs/adr/`（模板见 `docs/adr/0000-template.md`）
- **密钥纪律**：凭证只走 `.env`（已 gitignore），任何 key 不进 git 历史

## Agent skills

### Issue tracker

个人开发的 PRD 与 issues 默认放在 gitignored 的 `.scratch/<feature-slug>/`；协作者参与或仓库所有者明确
要求公开跟踪后，才把稳定、可执行的事项发布到 GitHub Issues。稳定产品方向进入 `docs/roadmap.md`，
不可逆决策进入 `docs/adr/`。See `docs/agents/issue-tracker.md`.

### Triage labels

五个标准 triage 角色用默认标签名（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。See `docs/agents/triage-labels.md`.

### Domain docs

单 context：根 `CONTEXT.md`（领域语言权威表）+ `docs/adr/`。See `docs/agents/domain.md`.

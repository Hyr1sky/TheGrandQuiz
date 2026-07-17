# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目是什么

**考核驱动的个人学习工具**，工程内核是一个可观测、可恢复、可评测的 Agent Runtime（Python 3.12+）；
作者本人是用户 #1，同时作为 AI/Agent 工程师方向的简历项目。核心循环是"考核"：学完材料 → 被拷问
→ 暴露薄弱概念 → 记入记忆 → 下次优先考薄弱点。

**当前状态（2026-07-17）**：可观测/可恢复/可评测的 Agent Runtime 已落地——`kernel/`（events/runner/tools/
hooks/context/clock/recovery/trace/db）+ `providers/`（OpenAI 兼容 + Record/Replay）+ `domain/learning/`
（考核竖切 ingest→深读→出题→判卷→薄弱记账）+ `interfaces/cli/`（ingest/quiz/react/report/trace 子命令）
+ `evals/`（Tier-1 规则 harness）。**最小 ReAct 对话核（R1）与全局 KB 重构均已落地**（`grandquiz react`
可真机跑：自然语言选材料 + 定题型的持久全局知识库考核）；上下文压缩、真实网络抓取、跨会话去重与
自适应难度第一阶段也已完成。[稳定性加固](.scratch/stability-hardening/PRD.md) S1-S9 与长文 Reader
预算内分块已实现；受影响 cassette 已用真实模型重录，并新增难度激活回放。五门全绿，全量 pytest 为
`721 passed`。生产 DB 已备份、迁移到 schema v8，并由三份真实材料审批重建为 88 个 KnowledgeItem；
一次真实考核闭环完成前，不得声称稳定性加固全部收口。设计权威仍在 `docs/` 与 `CONTEXT.md`。
**动手写代码前按序读**：

- [CONTEXT.md](CONTEXT.md) — 领域语言权威表（先读这个统一术语）
- [docs/architecture.md](docs/architecture.md) — 目标架构、两条核心设计判断、搭建顺序
- [docs/roadmap.md](docs/roadmap.md) — MVP 考核竖切、领域模型、eval 用例
- [docs/adr/](docs/adr/) — 七个不可逆决策（0001 提取式迁移 / 0002 概念同一性 / 0003 记忆四收二 /
  0004 循环是 workflow / 0005 全局 KB·消解 LearningTask / 0006 用户显式题型覆盖 / 0007 稳定资源修订与
  item 身份）

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

## 代码出处与参考

本仓库以提取式迁移自 scholarmate-digital-human 建立（[ADR-0001](docs/adr/0001-extract-not-slim.md)），
旧仓库冻结为只读参考（本机 `~/桌面/DevStation/scholarmate-digital-human`）。
[docs/reference-map.md](docs/reference-map.md) 记录每个待移植模块的出处、外部参考仓库，
以及**明确不要带过来的旧坑**——移植前先查这份清单。移植不是照搬：runner 进新仓库时同步做事件化改造。

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

本地 markdown：issues 与 PRD 存于 `.scratch/<feature-slug>/`（PRD.md + issues/NN-slug.md）。See `docs/agents/issue-tracker.md`.

### Triage labels

五个标准 triage 角色用默认标签名（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。See `docs/agents/triage-labels.md`.

### Domain docs

单 context：根 `CONTEXT.md`（领域语言权威表）+ `docs/adr/`。See `docs/agents/domain.md`.

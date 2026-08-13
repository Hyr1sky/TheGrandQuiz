# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目是什么

**考核驱动的个人学习工具**，工程内核是一个可观测、可恢复、可评测的 Agent Runtime（Python 3.12+）；
作者本人是用户 #1，同时作为 AI/Agent 工程师方向的简历项目。核心循环是"考核"：学完材料 → 被拷问
→ 暴露薄弱概念 → 记入记忆 → 下次优先考薄弱点。

**当前基线：v0.5.0**。材料入库与人工审批、修订化文档树与精确 Evidence、材料对话、逐题考核、
薄弱记账、判决纠正、材料发现、可审查语音回答、Local Web、Trace/Replay/Eval 均已落地。完整能力与限制见
[README](README.md) 和 [v0.5.0 Release Notes](docs/releases/v0.5.0.md)；实现与 dogfood 证据只在
[devrecords](docs/devrecords/) 保存，不在本文件重复。

当前明确关闭或受 gate 阻挡：自动 Demand Judge、自动 ambiguity/clarification classifier、Required Claims
默认判卷、KnowledgeRelation/CanonicalConcept、实时双工语音与数字人。未经新的 Eval 或产品消费者证据，
不得把这些能力重新描述成已批准路线。当前计划只读 [roadmap](docs/roadmap.md)；本地执行状态只读
`.scratch/CURRENT.md` 和它显式引用的 PRD。
**动手写代码前按序读**：

- [CONTEXT.md](CONTEXT.md) — 领域语言权威表（先读这个统一术语）
- [docs/architecture.md](docs/architecture.md) — 目标架构、两条核心设计判断、搭建顺序
- [docs/roadmap.md](docs/roadmap.md) — 当前工作焦点、候选产品竖切与进入条件
- [docs/adr/](docs/adr/) — 十二个不可逆决策（0001 提取式迁移 / 0002 概念同一性 / 0003 记忆四收二 /
  0004 循环是 workflow / 0005 全局 KB·消解 LearningTask / 0006 用户显式题型覆盖 / 0007 稳定资源修订与
  item 身份 / 0008 修订化文档树·精确溯源·分层知识图 / 0009 Local-first Web Interface /
  0010 长期学习事实与完整运行 Trace 分离 / 0011 受限 Required Claims 判卷契约 /
  0012 语音转写是可审查输入而非正式答案）

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

## 分层结构

```text
src/grandquiz/
├── kernel/       # 通用 Agent Runtime：events / runner / tools / hooks / context /
│                 #   memory / recovery / trace / subagent / approval
├── providers/    # LLM provider（OpenAI 兼容 + DemoEcho）、Record/Replay、token 用量
├── domain/learning/  # 学习领域：全局 KB（LearningResource → KnowledgeItem）+ 考核 / 记忆 / 难度
├── interfaces/   # 可插拔通道：api/（FastAPI REST+SSE）、cli/（开发期主力界面）
└── evals/        # 用例 DSL（YAML）+ 规则断言 / LLM judge + harness
```

**分层守卫：`kernel/` 禁止 import `domain/`**（已由 import-linter 在 CI 强制）。
trace、hook、recovery、SSE 与 eval replay 都建立在同一事件脊柱上。

## 写代码时必须遵守的设计约束

来自 architecture.md 已对齐的决策，不是建议：

- **核心循环是 workflow，不是自由 ReAct**（[ADR-0004](docs/adr/0004-core-loop-is-workflow-not-free-react.md)）：
  考核链路是确定性骨架，LLM 只在"出题""判卷"两个槽里被调用；状态机转移、选题候选集、Learning Memory
  写入全是代码。自由 ReAct 只用于开放编排。一句话：**LLM 判卷，代码记账。**
- **事件是信封**：`AgentEvent` = type + 元数据 + 不透明 payload，kernel 泛型分发、不认识具体类型；
  领域事件在 domain 层定义、经 kernel `emit()` 上同一条脊柱（kernel 保持领域无关）。
- **Hook 分两类语义**：interceptor（`before_*`，可改参可阻断）vs observer（`on_*`/`after_*`，只读）。
  Hook 抛异常必须被隔离，不能炸掉整个 turn。
- **确定性基建保持可注入**：时钟 / 随机数走 `Clock` 抽象与种子化 RNG，否则 replay 无法稳定对齐。
- **先定 trace schema 再写功能**：`turn_id / span_id / parent_span / type / input / output / tokens /
  latency / error`，span 成树。错误本身是一种 AgentEvent，自然进 trace。
- **跨轮次裁剪**：历史只保留最终 assistant 回答，丢弃 tool 调用中间过程（旧仓库的已知坑）。
- **注入防护是基线**：抓取的网页 / GitHub 内容是不可信输入——打"不可信"标记 + system prompt
  硬约束 + fetch 层大小 / 超时 / 域名限制。
- **subagent 判据 + 结构化输出契约**：subagent 仅用于"隔离大上下文 + 可验证输出"（当前唯一 subagent
  是 Reader；出题 / 判卷是工具）；其返回值用 pydantic schema 强制校验，失败自动重试。
- **审批门是可挂起 / 可恢复的 turn**：发 ApprovalRequested 事件 + 持久化待决状态 + 凭 token 恢复，
  不是普通工具成功或仅靠阻塞 `input()` 表达；Web 已按 suspend/resume 实现，CLI 是同步适配器。
- **SQLite 迁移**：版本号 + 顺序 SQL 文件，不上 alembic。
- **Prompt 版本管理**：prompt 模板独立于代码存放，trace 记 prompt 版本号。

## 代码参考

[ADR-0001](docs/adr/0001-extract-not-slim.md) 记录为何使用独立骨架建立本仓库；
[docs/reference-map.md](docs/reference-map.md) 只记录当前项目采用的公开外部参考。参考实现不是依赖，
任何借鉴都必须重新适配本仓库的事件脊柱、分层守卫和确定性测试契约。

## 开发节奏与代码树约定

- **竖切拉动基础设施**：先证明真实 domain / interface 消费者，再加硬 kernel seam；不要为假想能力预建框架。
  临时实现必须带 `# SKELETON` 并记入 [docs/skeleton-ledger.md](docs/skeleton-ledger.md)，正式替换时同步销账。
- **一个 PR 一个可验收行为**：每个 PR 对应一条用户可见行为或承重不变量，保持 CI 全绿。
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

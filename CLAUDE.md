# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目是什么

学习型数字人：一个**可观测、可恢复、可评测**的学习 Agent Runtime（Python 3.12+）。
当前处于 Pre-MVP 脚手架阶段——`src/grandquiz/` 还只有 `__init__.py`，全部设计都在 `docs/` 里。
**动手写代码前先读 [docs/architecture.md](docs/architecture.md)**（目标架构与搭建顺序）和
[docs/roadmap.md](docs/roadmap.md)（领域模型 / Subagent / 工具规划）。

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
├── domain/learning/  # 学习领域：LearningTask → ResourceCandidate → LearningResource → KnowledgeItem
├── interfaces/   # 可插拔通道：api/（FastAPI REST+SSE）、cli/（开发期主力界面）、asr/（语音）
└── evals/        # 用例 DSL（YAML）+ 规则断言 / LLM judge + harness
```

**分层守卫：`kernel/` 禁止 import `domain/`**（后续以 import-linter 强制）。
搭建顺序按依赖关系排定（见 architecture.md 末尾）：trace / 事件 / replay 最先建，
hook、recovery、eval 全部建在其上。

## 写代码时必须遵守的设计约束

来自 architecture.md 已对齐的决策，不是建议：

- **Hook 分两类语义**：interceptor（`before_*`，可改参可阻断）vs observer（`on_*`/`after_*`，只读）。
  Hook 抛异常必须被隔离，不能炸掉整个 turn。
- **确定性基建第一天做对**：时钟 / 随机数走注入（`Clock` 抽象 + 种子化 RNG），否则 replay 永远对不齐。
- **先定 trace schema 再写功能**：`turn_id / span_id / parent_span / type / input / output / tokens /
  latency / error`，span 成树。错误本身是一种 AgentEvent，自然进 trace。
- **跨轮次裁剪**：历史只保留最终 assistant 回答，丢弃 tool 调用中间过程（旧仓库的已知坑）。
- **注入防护进 MVP**：抓取的网页 / GitHub 内容是不可信输入——打"不可信"标记 + system prompt
  硬约束 + fetch 层大小 / 超时 / 域名限制。
- **结构化输出契约**：subagent 返回值用 pydantic schema 强制校验，失败自动重试。
- **SQLite 迁移**：版本号 + 顺序 SQL 文件，不上 alembic。
- **Prompt 版本管理**：prompt 模板独立于代码存放，trace 记 prompt 版本号。

## 代码出处与参考

本仓库以提取式迁移自 scholarmate-digital-human 建立（[ADR-0001](docs/adr/0001-extract-not-slim.md)），
旧仓库冻结为只读参考（本机 `~/桌面/DevStation/scholarmate-digital-human`）。
[docs/reference-map.md](docs/reference-map.md) 记录每个待移植模块的出处、外部参考仓库，
以及**明确不要带过来的旧坑**——移植前先查这份清单。移植不是照搬：runner 进新仓库时同步做事件化改造。

## 工程规范

- **提交规范**：conventional commits；issue 驱动开发，每个 issue 对应一个独立可验收的 PR
- **决策记录**：架构级决策写入 `docs/adr/`（模板见 `docs/adr/0000-template.md`）
- **密钥纪律**：凭证只走 `.env`（已 gitignore），任何 key 不进 git 历史

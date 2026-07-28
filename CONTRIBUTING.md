# Contributing to TheGrandQuiz

TheGrandQuiz 当前是个人维护、issue 驱动的 local-first 学习 Agent。欢迎可复现的 bug、文档修正和范围明确
的改进；大型新能力请先讨论问题与验收，不要直接提交实现。

## Setup

```bash
git clone https://github.com/Hyr1sky/TheGrandQuiz.git
cd TheGrandQuiz
uv sync --dev
cd web && npm ci
```

真实凭证只放 `.env`。普通测试、Eval 和 Web Scenario Bot 不需要真实 API、Docker 或生产数据库。

## Before changing code

依次阅读：

1. [CONTEXT.md](CONTEXT.md)；
2. [docs/architecture.md](docs/architecture.md)；
3. 对应 [ADR](docs/adr/)；
4. 相关 GitHub Issue 与 [roadmap](docs/roadmap.md)。

GitHub Issues 是公开 backlog 与协作状态的唯一权威。一个 PR 对应一个可验收行为；稳定产品方向写入
roadmap，架构级决策写 ADR，领域术语只在 `CONTEXT.md` 维护权威定义。本地草稿不得成为代码或公开文档
的依赖。

## Architecture rules

- `kernel/` 不得依赖 `domain/`、`interfaces/` 或 `evals/`；
- trace、hook、SSE 和 eval replay 复用同一条 `AgentEvent` 事件脊柱；
- 核心考核是确定性 workflow：LLM 判卷，代码记账；
- 浏览器只消费稳定、安全的事件投影，不暴露内部 payload；
- 不可信网页、Markdown 和第三方内容必须 fail closed；
- 不为简历展示引入产品不需要的框架、数据库或多 Agent。

## Quality gates

提交前至少运行与改动相称的目标测试，合并前保持完整门禁全绿：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
uv run pytest
uv run python -m grandquiz.evals

cd web
npm run lint
npm run api:check
npm test
npm run typecheck
npm run build:package
npm run test:sites
npm run test:e2e
```

改变公开 CLI、package data 或运行依赖时，还必须构建 wheel，并从仓库外运行 `grandquiz --help` 和
`grandquiz report`。

## Record/Replay cassette

Cassette 是真实外部 I/O 的可审计快照，不是随手更新的 golden 文件。

- 只有 prompt、tool schema、provider 模型或预注册真实行为发生有意变化时才重录；
- 使用 `scripts/record_*.py` 中的对应脚本；
- 重录需要真实调用授权，并在 PR 中记录模型、原因、token 成本和人工复核结果；
- 不得在 cassette 中保存 API Key、header、完整第三方文章或无权再分发的内容；
- ReplayMiss 必须大声失败，不能通过 fallback 掩盖。

## Commits and pull requests

- 使用 conventional commits，例如 `fix(evals): package replay assets`；
- PR 关联对应 GitHub Issue；
- 描述用户可见行为、测试证据、架构影响和数据迁移；
- UI 改动附截图；运行问题附脱敏 `trace_id`；
- 不提交 `.env`、数据库、Playwright artifact、个人绝对路径或供应商凭证。

当前项目没有承诺通用 Runtime SDK 兼容性。若改动内部接口，请说明实际调用者和 blast radius，而不是默认
增加兼容层。

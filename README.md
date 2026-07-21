# TheGrandQuiz

学习型数字人：一个**可观测、可恢复、可评测**的学习 Agent Runtime。

用户输入学习目标 → Agent 发现资源 → 用户审批 → 构建知识库 → 追踪学习行为 → 调度技能（测验 / 面试 / 总结 / 路线规划）→ trace 与 eval 持续改进系统。产品形态不绑定 Web——Runtime 是核心，REST / CLI / 语音都是可插拔通道。

## 项目状态

🟢 稳定性加固、修订化文档树、Agentic Search 与 GroundedDocumentAnswer 已收口（2026-07-19）。Runtime 以 `AgentEvent` 为唯一事件脊柱，具备 trace、恢复、Record/Replay、持久全局知识库、精确 DocumentNode citation、考核 workflow 与开放 ReAct 编排。

Eval Harness 现有 17 条用例：全部运行 Tier-1 确定性规则门，case15 额外运行校准优先的 Tier-2 `grounded_answer` LLM grader；case16 离线保护 Acquisition 接口，case17 回放真实模型的 search → 用户选择 → ingest 决策与质量失败零 KB 污染。真实响应已录入 cassette；日常 pytest 和 `grandquiz report` 只做离线 Replay，分别显示 Rule/Quality、execution/judge tokens、rubric、逐维理由和逐字审计依据。

Web Acquisition 的 WA-S1–S5 已完成：Trafilatura 正文抽取、结构化质量门、可选 Tavily / SearXNG `web_search`、Search/Fetch Record-Replay 均已接入原有事件脊柱与确定性 ingest workflow。Tavily 只需无需信用卡的免费 API key；SearXNG 提供 loopback-only 最小单容器配置，但 Docker 仍不是基础依赖。两种 provider 的真实搜索与 search → 用户选择 → ingest ReAct dogfood 均已验收。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | 初始产品与架构路线图（领域模型 / Subagent / 工具 / 开发阶段） |
| [docs/architecture.md](docs/architecture.md) | 目标架构：四层分层 + 事件总线脊柱，五大基建模块设计要点 |
| [docs/reference-map.md](docs/reference-map.md) | 参考实现映射（scholarmate-digital-human 移植清单 + 外部参考仓库） |
| [docs/adr/](docs/adr/) | 架构决策记录 |
| [.scratch/tier2-eval-judge/PRD.md](.scratch/tier2-eval-judge/PRD.md) | 已完成：校准优先的 Tier-2 LLM grader 与质量报告闭环 |
| [docs/devrecords/](docs/devrecords/) | 各轮长任务的实现、真实 dogfood、成本与门禁记录 |

## 开发

依赖 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --dev          # 创建 venv 并安装依赖（自动获取 Python 3.12）
uv run pytest          # 测试
uv run ruff check .    # lint
uv run ruff format .   # 格式化
uv run pyright         # 类型检查（strict）
uv run pre-commit install  # 安装提交钩子
uv run grandquiz search "MySQL 面试高频考点"  # 不经 LLM 验证已配置的搜索 provider
uv run grandquiz report    # 离线 Replay 全部 Eval，导出 ~/.grandquiz/eval-report/index.html
open ~/.grandquiz/eval-report/index.html
```

Web Search 默认不启用。在 `.env` 配置 `TAVILY_API_KEY` 即可使用 Tavily；也可按
[`deploy/searxng/README.md`](deploy/searxng/README.md) 启动本机 SearXNG。两者同时存在时必须用
`WEB_SEARCH_PROVIDER=tavily|searxng` 显式选择，避免静默改变供应商。

CI 在每次 push / PR 上跑 lint + format + typecheck + test，全绿才能合并。

## 工程规范

- **分层守卫**：`kernel/` 禁止 import `domain/`（已由 import-linter 在 CI 强制，第 5 道门）
- **提交规范**：conventional commits；issue 驱动开发，每个 issue 对应一个独立可验收的 PR
- **决策记录**：架构级决策写入 `docs/adr/`（现 8 篇），领域术语沉淀在 [CONTEXT.md](CONTEXT.md)
- **密钥纪律**：凭证只走 `.env`（已 gitignore），任何 key 不进 git 历史

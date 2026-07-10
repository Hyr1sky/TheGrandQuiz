# TheGrandQuiz

学习型数字人：一个**可观测、可恢复、可评测**的学习 Agent Runtime。

用户输入学习目标 → Agent 发现资源 → 用户审批 → 构建知识库 → 追踪学习行为 → 调度技能（测验 / 面试 / 总结 / 路线规划）→ trace 与 eval 持续改进系统。产品形态不绑定 Web——Runtime 是核心，REST / CLI / 语音都是可插拔通道。

## 项目状态

🟢 MVP 竖切已跑通（2026-07-10）：可观测/可恢复/可评测的 Agent Runtime 已落地——`kernel`（事件脊柱 /
runner / tools / hooks / context / recovery / trace）+ `providers`（OpenAI 兼容 + Record/Replay）+
`domain/learning`（考核竖切：喂材料 → 深读入库 → 出题 → 判卷 → 薄弱记账）+ `cli`（`ingest` / `quiz` /
`react` / `report` / `trace` 子命令）+ `evals`（Tier-1 规则 harness）。**最小 ReAct 对话核 + 全局知识库**
已落地：`grandquiz react` 可真机跑——自然语言在持久全局库里选材料、定题型、按薄弱点考核。五门（ruff /
format / import-linter / pyright / pytest）全绿。下一程：让 eval 成为迭代 gate + 上下文压缩。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | 初始产品与架构路线图（领域模型 / Subagent / 工具 / 开发阶段） |
| [docs/architecture.md](docs/architecture.md) | 目标架构：四层分层 + 事件总线脊柱，五大基建模块设计要点 |
| [docs/reference-map.md](docs/reference-map.md) | 参考实现映射（scholarmate-digital-human 移植清单 + 外部参考仓库） |
| [docs/adr/](docs/adr/) | 架构决策记录 |

## 开发

依赖 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --dev          # 创建 venv 并安装依赖（自动获取 Python 3.12）
uv run pytest          # 测试
uv run ruff check .    # lint
uv run ruff format .   # 格式化
uv run pyright         # 类型检查（strict）
uv run pre-commit install  # 安装提交钩子
```

CI 在每次 push / PR 上跑 lint + format + typecheck + test，全绿才能合并。

## 工程规范

- **分层守卫**：`kernel/` 禁止 import `domain/`（已由 import-linter 在 CI 强制，第 5 道门）
- **提交规范**：conventional commits；issue 驱动开发，每个 issue 对应一个独立可验收的 PR
- **决策记录**：架构级决策写入 `docs/adr/`（现 6 篇），领域术语沉淀在 [CONTEXT.md](CONTEXT.md)
- **密钥纪律**：凭证只走 `.env`（已 gitignore），任何 key 不进 git 历史

# TheGrandQuiz

一个考核驱动、local-first 的个人学习 Agent：把材料变成带精确原文证据的知识库，通过逐题考核暴露
薄弱概念，并在下一轮优先复考。

TheGrandQuiz 同时包含一套可观测、可恢复、可评测的 Agent Runtime。Runtime 是产品的工程内核，
不是对外承诺稳定 API 的通用框架。

## v0.1.0 能做什么

- 从本地 Markdown / Text 深读并人工筛选 KnowledgeItem；
- 按修订化 DocumentNode 保存原文结构，Evidence 可精确回到 revision/node/source span；
- 用 CLI ReAct 或 Local Web 针对当前材料 Chat，回答不能静默扩大到全库；
- 逐题进行选择题、开放问答和薄弱复考，由代码负责状态转移与 Learning Memory 记账；
- 用 trace、Record/Replay 和 17 条 Eval 离线审计工具顺序、scope、引用和回答质量；
- 在浏览器阅读文章、查看大纲、揭示 Evidence、完成考核并观察安全投影后的运行状态。

首个版本是本机单用户候选版，不支持账号、多用户、云同步、公网服务、Web Acquisition/审批、
Web 文章/知识点管理或连续“掌握度分数”。Web Acquisition 后端和 CLI 路径已存在，但浏览器中的
可恢复审批属于 v0.1.0 后续功能。

## 数据与外部服务

TheGrandQuiz 默认把数据保存在本机：

```text
~/.grandquiz/learning.db   # 材料、revision、KnowledgeItem、Evidence、Learning Memory
~/.grandquiz/trace.db      # 用户消息、模型/工具事件、token、错误与执行树
~/.grandquiz/eval-report/  # 离线 Eval HTML
```

配置真实 LLM 后，system prompt、用户消息、选定材料节点和工具上下文会发送给 `.env` 中配置的
OpenAI-compatible 服务。不要导入无权发送给该服务的私人、受限或机密材料。

Web Search 只是返回候选，不代表允许抓取或入库；选中 URL 后的内容仍按不可信输入处理，并经过
大小、域名、质量、prompt-injection 和人工审批边界。Trace 不保存完整抓取网页正文，但可能包含
用户消息、工具参数、模型输出和引用片段，因此也应按敏感数据保护。

默认 Web 服务只监听 `127.0.0.1`，没有账号或鉴权。不要把它直接暴露到局域网或公网。完整安全模型、
漏洞报告和密钥处理见 [SECURITY.md](SECURITY.md)。

## Quickstart

### 1. 准备环境

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和一个 OpenAI-compatible LLM。Docker 不是基础依赖；
只有选择自托管 SearXNG 时才需要。

```bash
git clone https://github.com/Hyr1sky/TheGrandQuiz.git
cd TheGrandQuiz
uv sync
cp .env.example .env
```

编辑 `.env`。`LLM_*` 是 basic 角色（判卷、基础判断），`ENRICH_LLM_*` 是 enrich 角色（Reader、出题）。
两个角色可以指向同一个 provider/model，但仍需分别填写两组变量，避免隐藏 fallback。

### 2. 导入第一份材料

建议先用你有权处理的本地 Markdown 或纯文本：

```bash
uv run grandquiz ingest ./notes/agent-runtime.md --task "Agent Runtime"
```

Reader 会展示候选 KnowledgeItem；只有你确认保留的条目才会原子写入知识库。拒绝或失败不会留下半份
知识快照。

### 3. 对话或考核

```bash
uv run grandquiz react "Agent Runtime 学习"
uv run grandquiz quiz "Agent Runtime 复习" --rounds 3
```

`title` 只是本次会话横幅，不划分知识库或材料范围。需要指定材料时，在 ReAct 中明确选择，Web 中以顶栏
“当前材料”为 exact scope。

### 4. 查看 Trace 与离线 Eval

每次运行结束会打印 `trace_id`：

```bash
uv run grandquiz trace <trace_id>
uv run grandquiz report
```

`report` 使用安装包自带的 Replay cassette，默认不读取 `.env`、不访问公网、不调用真实模型。

### 5. 启动 Local Web

已安装的 package 或仓库环境都可以用一条命令启动同源生产工作台：

```bash
uv run grandquiz-web
```

浏览器打开 `http://127.0.0.1:8000`。服务同时提供打包后的 React 页面与 `/api/v1`，并且只监听
loopback。CLI 继续作为 ingest、恢复和 trace 审计入口；v0.1.0 的 Web 尚不提供 Acquisition/审批或
资源管理。

修改前端源码时再使用两个终端进入开发模式：

```bash
# terminal 1
uv run grandquiz-web

# terminal 2
cd web
npm ci
npm run dev
```

开发页面位于 `http://127.0.0.1:5173`，Vite 把 API 请求代理到本地后端。

## 可选 Web Search

不配置 Search provider 时，本地材料、Chat、Quiz 和 Eval 都可正常使用。

- Tavily：在 `.env` 设置 `TAVILY_API_KEY`；
- SearXNG：按 [deploy/searxng/README.md](deploy/searxng/README.md) 启动本机服务并设置
  `SEARXNG_URL`；
- 两者同时存在时必须设置 `WEB_SEARCH_PROVIDER=tavily|searxng`，避免静默切换供应商。

可先只搜索候选，不调用 LLM、不抓取或入库：

```bash
uv run grandquiz search "MySQL 面试高频考点"
```

## 备份、恢复与清除

操作数据库前先停止 CLI/Web 进程。备份整个数据目录可以同时保留学习状态与 trace：

```bash
cp -a ~/.grandquiz ~/.grandquiz-backup
```

恢复时先保留当前目录，再把备份复制回来。需要清除本地数据时，优先把目录移动到一个可恢复的位置，
确认无误后再由操作系统删除：

```bash
mv ~/.grandquiz ~/.grandquiz-retired
```

删除 `.env` 只会移除本机配置，不会撤销已经发送给外部服务的数据；外部服务的数据保留规则由其供应商决定。

## 开发与质量门

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
uv run pytest
uv run python -m grandquiz.evals

cd web
npm ci
npm run lint
npm run api:check
npm test
npm run typecheck
npm run build:package
npm run test:sites
npm run test:e2e
```

CI 在每次 push / PR 上运行 Python、Eval、Web、OpenAPI 和 Playwright 门。贡献约定、cassette 重录纪律和
PR 要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 架构与项目资料

| 文档 | 内容 |
| --- | --- |
| [CONTEXT.md](CONTEXT.md) | 产品领域语言权威表 |
| [docs/architecture.md](docs/architecture.md) | 分层、事件脊柱与核心设计判断 |
| [docs/roadmap.md](docs/roadmap.md) | 发展路线与 walking skeleton |
| [docs/adr/](docs/adr/) | 不可逆架构决策 |
| [docs/devrecords/](docs/devrecords/) | 实现、dogfood、成本与门禁记录 |
| [docs/open-source-release-checklist.md](docs/open-source-release-checklist.md) | v0.1.0 发布门 |

项目以提取式迁移自作者的旧 ScholarMate Digital Human 仓库建立，迁移边界与未带入的问题记录在
[ADR-0001](docs/adr/0001-extract-not-slim.md) 和 [reference-map.md](docs/reference-map.md)。

## 许可证

TheGrandQuiz 以 [MIT License](LICENSE) 发布，Copyright © 2026 Hyr1sky。允许个人与商业使用、
修改、再发布和闭源集成，但必须保留原版权与许可证文本。

项目由作者此前维护的 ScholarMate Digital Human 仓库提取式迁移而来；迁移边界记录在
[ADR-0001](docs/adr/0001-extract-not-slim.md) 与 [reference-map.md](docs/reference-map.md)。
依赖和打包资产继续适用各自许可证；例如 Web 星图资产的第三方声明随 wheel 一同分发。

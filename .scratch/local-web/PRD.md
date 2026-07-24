# PRD：Local-first Web 学习工作台

Status: in progress（2026-07-24；LW-S1 API 已交付并全门通过，下一节点为 LW-S2 视觉 HITL）
Triage: ready-for-human

## Problem Statement

TheGrandQuiz 已经具备可观测、可恢复、可评测的 Agent Runtime，以及 ingest、Document Structure、
Agentic Search、GroundedDocumentAnswer、考核循环、Web Acquisition 和 Eval Harness，但主要入口仍是
CLI。CLI 适合开发、trace 审计和熟练用户，却无法自然承载以下产品行为：

- 一边阅读文章结构和原文，一边针对当前材料提问并查看精确 citation；
- 在答题时临时揭示材料证据，又不把整段原文永久摊开；
- 管理文章、知识点、薄弱状态、搜索候选与待审批内容；
- 以学习轨迹而不是传统 KPI dashboard 的方式回顾考核与回归结果；
- 用直观表单配置本地数据库、模型和可选搜索 provider，而不是只编辑 `.env`。

若直接让 React 访问 SQLite、复制 CLI 编排或把内部 `AgentEvent` 原样暴露到浏览器，会破坏现有领域边界、
事件脊柱和安全约束。项目需要的是一个 local-first 的正式产品通道，而不是套在数据库上的 CRUD dashboard。

## Solution

在同一 monorepo、同一 `main` 分支内增加 FastAPI + React/Vite Web 通道：

```text
React workspace
  ↓ versioned REST + SSE
FastAPI interface adapter
  ↓ application runs / stable event projection
domain workflows + LearningPersistence + EventEmitter
  ↓
learning.db / trace.db / configured external providers
```

首条 tracer bullet 是：

```text
资源库 → 文档大纲/节点 → 对指定材料提问
→ GroundedDocumentAnswer → run 状态 + SSE → 精确 citation 回到文章位置
```

FastAPI 只负责 HTTP 契约、依赖装配、run 生命周期和安全事件投影。资源检索、grounding、citation、
考核记账和审批仍由现有领域 Module 负责。React 不拼 SQL、不复制 Pydantic 模型；OpenAPI 是生成
TypeScript client 的唯一契约源。

## User Stories

1. 作为用户，我想在浏览器看到本地知识库里的文章列表，以便选择当前学习材料。
2. 作为用户，我想看到文章标题、来源、状态、主题和当前 revision，以便判断材料是否可用。
3. 作为用户，我想按原文层级浏览 DocumentNode 大纲，以便渐进披露而不是一次加载全文。
4. 作为用户，我想展开一个节点并读取有界原文，以便理解 citation 的上下文。
5. 作为用户，我想对一份或多份明确选择的材料提问，以便答案不会偷偷扩展到全库。
6. 作为用户，我想看到回答运行中的搜索、读取和生成阶段，以便知道系统是否卡住。
7. 作为用户，我想在回答完成后点击 citation 回到 section_path 和逐字证据。
8. 作为用户，我想在没有证据时得到明确的 fail-safe 结果，而不是模型常识补写。
9. 作为用户，我想刷新页面后仍能通过 run_id / trace_id 审计已完成运行。
10. 作为用户，我想取消仍在运行的长任务，并知道取消是否真正生效。
11. 作为用户，我想在考核时一次只处理一道题，以保留现有逐题 workflow。
12. 作为用户，我想在忘记答案时悬停或主动揭示被玻璃遮罩覆盖的相关材料。
13. 作为用户，我想让“揭示证据”成为可观察学习动作，而不是不可审计的 UI 特效。
14. 作为用户，我想查看薄弱、观察中和已销账的状态轨迹，而不是一个虚假的掌握度分数。
15. 作为用户，我想搜索 Web 候选并先筛选，再 Fetch / Reader / 审批，避免自动批量污染 KB。
16. 作为用户，我想在 Web 中恢复待审批 run，而不是依赖阻塞终端 `input()`。
17. 作为用户，我想管理文章和知识点，但删除或替换必须尊重 revision、evidence 和历史 trace。
18. 作为用户，我想在页面中理解哪些数据会发给外部 LLM、保存在哪里、如何备份或清除。
19. 作为开发者，我想让 CLI 和 API 复用同一领域 workflow 与持久层 owner，避免行为漂移。
20. 作为开发者，我想让浏览器只消费稳定的 UI 事件，不依赖内部 payload 的偶然字段。
21. 作为开发者，我想从 OpenAPI 生成 TypeScript 类型和 client，避免前后端重复定义契约。
22. 作为开发者，我想让默认 Web 服务只监听 loopback，未经显式配置不暴露到局域网或公网。
23. 作为开发者，我想让 API 错误包含稳定 code、可读 message、retryable 和 trace_id。
24. 作为开发者，我想在 fake provider 与临时 SQLite 上离线验证完整 HTTP/SSE 行为。
25. 作为开发者，我想保留 CLI 作为调试和恢复通道，即使 Web 成为主要产品入口。
26. 作为开源用户，我想用一条明确命令启动后端和前端，并在 README 中找到数据与配置说明。

## Implementation Decisions

### 1. 仓库与目录

- 继续只维护 `main`，不拆前后端仓库，也不长期维护临时 Codex 分支。
- Python API 位于 `src/grandquiz/interfaces/api/`；React/Vite 位于根目录 `web/`。
- React 使用 feature-first 结构：`app/`、`routes/`、`features/`、`shared/`；不建立按
  components/services/types 横切的巨大公共桶。
- 本地开发允许前后端两个进程；发布形态优先由 FastAPI 托管已构建静态资源，同源减少 CORS 和配置面。

### 2. API 是 interface adapter

- 路径从 `/api/v1` 开始，内部 Python API 不是外部稳定框架承诺。
- handler 只做 schema 校验、领域调用、run 映射和错误投影，不在路由里复制搜索、citation 或记账规则。
- `LearningPersistence` 是 learning.db 的唯一连接 owner；API 生命周期创建并关闭它。
- CLI 与 API 出现第二个真实共同消费者后，才把共享装配从 CLI 物理位置移到中立 `interfaces` 层；
  不提前抽象其余只有 CLI 使用的 Runner 配置。

### 3. HTTP 契约

第一阶段契约：

- `GET /api/v1/health`
- `GET /api/v1/resources`
- `GET /api/v1/resources/{resource_id}`
- `GET /api/v1/resources/{resource_id}/outline`
- `GET /api/v1/resources/{resource_id}/nodes/{node_id}`
- `POST /api/v1/resources/{resource_id}/questions`
- `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/events`
- `POST /api/v1/runs/{run_id}/cancel`

列表返回稳定、显式的 DTO，不把 `raw_content` 默认带出。节点读取按单个 node 和有界正文返回。
问题接口接受用户自然问题与显式资源 scope；检索短语可由调用者给出，但不得在点名失败后扩大范围。

### 4. Run 生命周期与 SSE

- 长操作先返回 `202`，响应含 `run_id`、`trace_id`、`status`。
- 状态固定为 `queued | running | needs_input | succeeded | failed | cancelled`。
- 第一阶段 run registry 可在单进程内管理运行任务和 UI event backlog；权威执行证据仍写入 `trace.db`。
- 同一进程内已完成 run 可继续按 run_id 读取；服务重启后仍可按 trace_id 审计完整事件树，但首条竖切不从
  trace 逆向重建 HTTP `RunView`。跨进程 run/result 恢复与 `needs_input` 持久化在审批竖切实现。
- SSE 发送版本化 UI projection，例如 `run.started`、`search.completed`、`node.read`、
  `answer.completed`、`run.failed`，不直接序列化任意 `AgentEvent.payload`。
- 每条 UI event 带单调序号，支持客户端重连后从 backlog 继续；正文、system prompt 和模型完整输出
  不默认进入 SSE。

### 5. 错误与取消

- 错误 envelope 固定为 `code`、`message`、`retryable`、`trace_id`。
- 404、输入校验、scope invalid、no evidence、provider failure 和内部失败使用不同稳定 code。
- 取消是显式 best-effort 行为；成功取消必须进入 run 状态和事件投影。已终态 run 重复取消保持幂等。
- FastAPI validation error 也归一到公共错误 envelope，避免前端解析两套错误形状。

### 6. Provider 与配置

- 生产 API 默认从现有 `.env` 创建 OpenAI-compatible provider；测试从 app factory 注入 fake provider。
- 默认 bind `127.0.0.1`，不增加账号、多用户、远程部署或鉴权假象。
- API 不返回 secret 值；后续配置页只显示“是否已配置”、公开 endpoint 和 provider 类型。
- 任何发送到外部 LLM 的材料节点、用户问题和 prompt 都沿用现有隐私说明与 trace 纪律。

### 7. 前端体验

- 视觉方向是“纸面学习工作台 × 软质仪器控件 × Evidence 玻璃遮罩”。
- 文章是主画布；对话作为页边批注或浮层，不采用传统左右 chat dashboard。
- neumorphism 只用于少量可触摸控件，不铺满页面；状态、层级和 citation 必须保持对比度。
- 统计表达学习状态迁移和证据链，不堆 KPI 卡片。
- 正式 React 视觉实现前生成三种可比较的一屏方案，由用户选择；选择后固化
  `docs/design/web-visual-language.md`。

### 8. 考核与审批保持领域语义

- Web 考核调用 `AssessmentSession` / 确定性 workflow；不把逐题循环交给自由 ReAct。
- evidence reveal 产生领域或界面动作事件，后续可用于分析“独立答对”与“看材料后答对”。
- Web Acquisition 继续 search → 用户选择 → fetch → Reader → 审批；不自动批量入库。
- 跨进程审批必须用可持久的 suspend/resume token，不把 FastAPI request 阻塞到人工点击。

## Testing Decisions

- 严格按 TDD 由 HTTP 公共接口向内推进，一次只增加一个失败测试和最小实现。
- FastAPI 测试使用 app factory、临时 learning.db / trace.db 和 fake provider；不访问生产 DB、`.env`
  或公网。
- 资源、outline、node 测试通过真实 `LearningPersistence` 和真实迁移建立数据，不 mock store。
- Grounded question 只 mock LLM provider 系统边界，真实运行 DocumentSearch、FTS、预算、citation、
  EventEmitter 与 TraceStore。
- SSE 测试断言稳定 UI 事件类型、顺序、终态、重连 backlog 和敏感字段缺失，不断言内部队列实现。
- 取消测试使用可控阻塞 provider，证明 task 终止、run 状态和事件一致。
- OpenAPI snapshot / schema test 固定路径、DTO、错误 envelope 和 run 状态；生成 TypeScript 后在前端 CI
  检查无 drift。
- React 使用 Vitest + Testing Library 测用户行为；关键文章问答流用 Playwright 做同源端到端测试。
- 既有 841 项 pytest、Eval 17/17、ruff、format、pyright、import-linter 必须保持全绿。

## Out of Scope

- 多用户、登录、权限系统、云托管、远程数据库和公网部署。
- 通用 Agent Runtime 对外 SDK 或稳定 semver 承诺。
- WebSocket；第一阶段单向进度使用 SSE，用户动作走普通 HTTP。
- 浏览器直接连接 SQLite，或为每张表生成 CRUD。
- 向量库、图数据库、CanonicalConcept、KnowledgeRelation 默认启用。
- 自动删除历史 revision / trace，或无确认的破坏性 KB 操作。
- 在视觉方向 HITL 前完成正式 React 主题和组件系统。
- 为 Web 重写核心考核为自由 ReAct。

## Delivery Order

1. FastAPI contract + 文章问答 run/SSE tracer bullet。
2. 三种视觉方向 HITL，固化 Web visual language。
3. React Article Workspace 贯通第一条 tracer bullet。
4. 考核工作台 + Evidence reveal。
5. Web Acquisition + 可恢复审批。
6. 文章/知识点/设置/学习轨迹管理。
7. 构建产物、同源启动、开源文档和 v0.1.0 发布门。

## Further Notes

- 本 PRD 覆盖并替代发布清单中旧的 “CLI-only v0.1.0 / Web 延后”判断；发布清单仍是最终发布门。
- 首条 API 竖切不要求真实模型录制。真实外部 LLM dogfood 在 fake/replay 自动门全绿后单独请求用户验收。
- `localtemp/trace.db` 与生产 `~/.grandquiz/trace.db` 可用于人工验收，但自动测试绝不能依赖它们。

# ADR-0009: Local-first Web interface 与稳定事件投影

- 状态：已接受
- 日期：2026-07-24

## 背景

CLI 已经能够完整驱动 ingest、ReAct、quiz、trace 和 eval，但文章阅读、精确 citation、证据揭示、审批与
学习轨迹需要持续可见的空间界面。目标架构从立项起就在 `interfaces/api/` 预留 FastAPI REST + SSE；
事件脊柱也明确把网络流式输出定义为 `AgentEvent` 的投影。

现在需要确定 Web 是否成为 v0.1.0 的正式产品通道，以及它如何在不复制领域 workflow、不暴露 SQLite 和
不把内部事件 schema 偶然稳定化的前提下接入现有系统。

## 决策

### 1. Web 是同仓 local-first 产品通道

- 在同一仓库、同一 `main` 维护 FastAPI + React/Vite。
- Python adapter 位于 `src/grandquiz/interfaces/api/`，前端位于根 `web/`。
- 默认只监听 `127.0.0.1`；v0.1.0 不承诺多用户、鉴权、云部署或公网服务。
- CLI 保留为调试、恢复、批处理和 trace 审计通道。

### 2. FastAPI 不成为第二个业务层

- handler 只拥有 HTTP schema、依赖装配、run 生命周期、错误和事件投影。
- 资源、DocumentNode、GroundedDocumentAnswer、AssessmentSession、Web Acquisition、审批和记账继续由
  domain Module 拥有。
- learning.db 连接由 `LearningPersistence` 管理；React 不接触 SQLite。
- CLI/API 的第二个真实共同消费点出现时，把对应装配移到中立 `interfaces` 层；不为假想消费者移动其余代码。

### 3. REST 负责动作，SSE 负责单向进度

- API 从 `/api/v1` 开始，OpenAPI 是生成 TypeScript client 的契约源。
- 长操作创建有身份的 run，返回 `run_id`、`trace_id` 与状态；普通 HTTP 承担创建、查询、取消、审批恢复。
- 单向执行进度使用 SSE，不引入 WebSocket。
- Provider 原生 delta 先归一为 kernel `AgentEvent`，再投影为 SSE；不得让 SDK chunk 绕过事件脊柱。
- Chat 取消是按 `turn_id` 的幂等 HTTP command，必须等待后端 task 与活动 span 进入 cancelled 终态；
  关闭浏览器 SSE 连接不等于取消。
- run 状态固定为 `queued/running/needs_input/succeeded/failed/cancelled`。

### 4. 浏览器只看到稳定 UI event

- 内部 `AgentEvent` 仍是 trace/hook/eval/CLI 的唯一事实流。
- API 订阅同一 `EventSink`，把明确白名单事件投影为版本化 UI event；不得把任意 payload 原样透传。
- UI event 带 run/trace/sequence/type/data，支持 backlog 后继续实时订阅。
- 文本流只暴露稳定 `chat.message_delta`；tool 参数碎片在 Provider 边界完成组装，不成为浏览器契约。
- system prompt、完整模型 messages/output、完整网页/节点正文和 secret 不进入默认 SSE。

### 5. v0.1.0 第一条竖切是 Article Workspace

资源列表 → 文档大纲/节点 → 显式 scope 材料问答 → run/SSE → 精确 citation。它复用
`GroundedDocumentAnswer` 的有界 search/read/citation workflow，不让外层 ReAct 自由决定是否扩大 scope。
考核、Acquisition/审批和管理界面在此接口形状稳定后按竖切追加。

## 备选方案

### 前后端拆仓或长期双分支

会增加版本协调、契约漂移和个人项目维护成本；当前没有独立发布周期或团队边界证明收益。拒绝。

### React 直接访问 SQLite 或通用 CRUD API

实现快，但把 schema 当产品契约，绕过 revision、evidence、审批、事件和记账不变量。拒绝。

### 把整个 CLI/ReAct 封进一个流式 endpoint

能快速“网页聊天”，却无法表达文章大纲、精确 citation、逐题 workflow 和可恢复审批，也会把自由 ReAct
放到所有行为之前。拒绝。

### 原样 SSE 所有 AgentEvent

调试信息最丰富，但会泄露大 payload，并迫使前端依赖内部事件字段，阻碍 runtime 演进。拒绝；使用白名单投影。

### 首版使用 WebSocket

双向协议对首条问答进度没有必要，连接与恢复复杂度更高。拒绝；动作使用 HTTP，进度使用 SSE。

## 后果

### 好处

- Web 与 CLI 共享领域行为、SQLite owner、事件和 trace，不产生第二套学习产品。
- Article Workspace 能把 Document Structure 与精确 citation 的工程能力转成可感知产品价值。
- 稳定 UI projection 允许内部 `AgentEvent` 继续演进，又保留可观测性。
- OpenAPI 生成 client 降低前后端契约漂移。
- loopback 默认与 local-first 数据模型匹配，避免过早引入账户和部署复杂度。

### 代价与风险

- run registry、SSE 重连、取消和 provider 生命周期需要明确 owner；不能把裸 `asyncio.create_task` 当完成设计。
- 真正跨进程审批仍需持久 suspend/resume token，是后续明确 skeleton 债。
- Web 纳入 v0.1.0 会扩大发布检查面：Node toolchain、静态资源打包、OpenAPI drift 和浏览器测试。
- UI event 白名单需要随产品行为维护；调试深度仍应通过 trace 工具获得。

### 重新审视信号

- 需要手机/多设备访问或多人共享 KB，届时重新评估鉴权、bind、并发和远程数据库。
- SSE 无法满足真实双向低延迟行为，再评估 WebSocket。
- 前后端出现独立团队或发布节奏，再评估拆仓。
- 单进程 run registry 无法满足崩溃恢复，按审批竖切引入持久 run/command log，而不是直接引入分布式队列。

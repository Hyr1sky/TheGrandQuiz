# PRD：Web Runtime 上下文连续性与运行观测

Status: done
Triage: ready-for-human
Created: 2026-07-28

## Problem Statement

真实 Web dogfood 暴露了三条相互关联、但必须分别验收的问题：

1. 顶栏选中的 LearningResource 没有进入 Chat turn，上层 ReAct 无法理解“当前材料”；
2. Chat 每轮重新订阅 SSE 都从 `after=0` 开始，第二轮会回放并停在上一轮终态；
3. `trace.db` 与静态 HTML 能事后审计，但运行中只能在 DevTools、后端日志和 SQLite 之间来回切换。

这些问题都位于 Local Web interface adapter，不应通过修改核心考核 workflow、复制 trace schema、让 React
直连 SQLite，或增加第二条日志总线来解决。

## Solution

沿用 ADR-0009 的 REST + SSE 与安全 UI projection：

- 每条 Chat message 可携带可选的 `active_resource_id`；API 验证资源存在后，把 exact id 放进动态 system
  Partition，专门解析“当前材料/本文”，不把前端标题或隐藏 prompt 拼进用户消息。
- ChatPanel 持有 session 级 SSE sequence cursor；新 turn 与断线重连都从最后确认的 sequence 继续。
- 在 `interfaces/api` 增加 `TraceObservatory`，作为既有 `AgentEvent` 脊柱的 observer。它把事件投影成
  脱敏 `TraceUiEvent`，并通过版本化 REST/SSE 提供当前 trace 的摘要、span 时间线与增量事件。
- React 在底部罗盘打开“运行观测”抽屉，渐进披露状态、耗时、model/tool 调用、token、错误/恢复与 span；
  默认不显示 prompt、工具参数、模型正文、用户正文、材料正文或 secret。

## Interface Seams

1. `POST /api/v1/chat/sessions/{id}/messages`：输入用户文本 + optional active resource id。
2. `GET /api/v1/chat/sessions/{id}/events?after=N`：跨 turn 单调 cursor。
3. `GET /api/v1/observability/traces/{trace_id}`：安全 snapshot。
4. `GET /api/v1/observability/traces/{trace_id}/events?after=N`：安全增量 SSE。
5. React DOM：当前材料可用自然语言引用；第二轮消息只出现一次；罗盘抽屉能实时展示运行摘要与时间线。

## Architecture Decisions

- `TraceStore` 继续是 append-only 权威持久化；`TraceObservatory` 是 interface projection，不改 trace schema。
- 新可观测能力通过 `EventSink.subscribe()` 注册，不侵入 Runner、AssessmentSession、GroundedDocumentAnswer。
- 安全投影只携带 allowlist 字段：event type、sequence、timestamp、span/parent、tool name、token、latency、
  success/recovered。任何 raw payload 默认不可达浏览器。
- Web 只支持 loopback 单用户；不增加鉴权、多租户、OTLP/Phoenix/Langfuse。
- 观测抽屉不是通用 dashboard；它服务当前运行的调试与学习过程解释。

## Delivery Order

1. WR-O1 当前材料 turn context。
2. WR-O2 Chat SSE cursor continuity。
3. WR-O3 安全 trace projection REST/SSE。
4. WR-O4 罗盘观测抽屉、真实 dogfood 与开发记录。

## Global Acceptance

- [x] “基于当前材料考我”在不重复点名标题时使用顶栏 exact resource scope。
- [x] 第二轮 Chat 从上一轮 sequence 继续，不回放第一轮消息或导航。
- [x] 运行中可看到 model/tool 数、token、耗时、错误/恢复与 span 时间线。
- [x] 浏览器契约中不存在 prompt、messages、arguments、output、正文或 secret。
- [x] React 不读取 SQLite；核心 workflow 与 kernel/domain 分层守卫不变。
- [x] OpenAPI、Vitest、TypeScript、Vite build、Python 静态四门与全量 pytest 全绿。

## Completion Evidence

- 真实 Chat trace：`9cc3cf6c33d94ecc8f18e095689212d9`
- 真实 Assessment trace：`2f2b2c06ec1e455f911e66e9a26e4e0e`
- 浏览器验证：exact 当前材料回答、第二轮不重放、`start_assessment` 后真实题目出现、罗盘自动切换到
  assessment trace。
- 自动门：Python `889 passed`，Web `30 passed`，ruff / format / pyright / import-linter /
  TypeScript / Vite build / Sites worker 全绿。

## Out of Scope

- 修改 assessment workflow、选题、判卷或 Learning Memory 语义。
- 通用 trace 搜索后台、KPI dashboard、成本计费。
- 原样浏览内部 AgentEvent payload。
- OTLP exporter、第三方观测平台、远程部署。

# PRD: 让 runtime 可见（Runtime Visibility · Tier A①）

Status: ready-for-agent
Created: 2026-07-06

> "可观测 runtime" 目前只是宣称——trace / span 树 / token / latency 全被捕获、却从不 surface，
> 且真机 dogfood 的 CLI 根本没订阅 TraceStore（只有 eval 装配订阅），即真实会话不落 trace。
> 本 PRD 把它变成**可点开的证据**：真机会话落 trace → 事件脊柱订阅者形式化成可插拔、异常隔离的
> processor 管线（给 OTLP 留口）→ 自建自包含静态 HTML 查看器（span 树 + 事件流 + token/latency），
> eval 报告与真机 trace 共用同一渲染器。术语以根 [CONTEXT.md](../../CONTEXT.md) 为准；
> 遵循 architecture.md "事件总线是脊柱"（新能力 = 加订阅者、不另起回调系统）+ kernel 领域无关。

## Problem Statement

作为 runtime 开发者（也是简历项目作者），我这套 runtime 最值钱的三个卖点——可观测的事件脊柱、
trace/span 树、token/latency——**captured 了却从不可见**。更糟的是：`TraceStore` 只在 `evals/` 的测试
装配里被订阅，真机 CLI 的 `quiz` 只挂了 Rich printer、`ingest` 一个订阅者都没有——**我每天 dogfood 跑的
考核会话根本不落 trace**。于是"可观测 runtime"在生产路径上恰恰不可观测，简历叙事里最强的一句只能靠嘴说、
拿不出一张能点开 / 截图 / 链接的证据（"show, don't tell"）。同时 `EventSink` 扇出**不隔离订阅者异常**——
一个订阅者抛异常会炸掉整轮（当初 Rich markup 崩就是这个坑）。

## Solution

把 runtime 变得**可见且可信**，分四步（全部建在既有事件脊柱上，不另起系统）：

1. **真机落 trace**：真机 CLI 的 `ingest` 与 `quiz` 会话把 `AgentEvent` 流持久化到 trace SQLite——重启后
   仍可回看任意一次真实会话。
2. **订阅者形式化为可插拔 processor 管线**（对标 openai-agents 的 TracingProcessor：`on_event` /
   `on_span_start` / `on_span_end`）：TraceStore 持久化、CLI Rich 投影、eval 事件收集都成为 processor；
   **每个 processor 调用被异常隔离**——一个 processor 抛异常被捕获 + 记录、绝不炸掉整轮（顺带闭掉
   EventSink 不隔离订阅者异常的已知坑）。管线为后续 OTLP 导出留好口子（新增一个 processor 即可）。
3. **自建自包含静态 HTML 查看器**（无外部服务 / 无 CDN / 无 JS 依赖）：把一条 trace 渲染成
   span 森林（turn → model → tool → subagent）+ 事件流 + 每 span 的 token/latency。**eval 报告（逐用例）
   与真机 trace 视图共用同一渲染器**——因为一个 eval 用例本身就是一条 trace。对标 inspect_ai 的 Inspect View。
4. **CLI 子命令产出**：一条命令把 eval run 或某次真机会话导成可点开的 HTML。

## User Stories

1. 作为 runtime 开发者，我想让真机 `quiz` 会话把发射的 AgentEvent 流持久化到 trace 存储，以便重启后仍能回看那次考核发生了什么。
2. 作为 runtime 开发者，我想让真机 `ingest` 会话同样落 trace，以便深读 / 审批 / 入库链路可事后追溯。
3. 作为 runtime 开发者，我想让真机会话的 trace 与 eval 的 trace 存在**独立于 learning.db 的** trace 存储里，以便可观测数据与领域数据分离、各自演进。
4. 作为 runtime 开发者，我想把 EventSink 的订阅者形式化为一个 processor 协议（消费 AgentEvent / span 生命周期），以便"加一种可观测能力 = 加一个 processor"，不改脊柱、不另起回调系统。
5. 作为 runtime 开发者，我想让每个 processor 的调用被异常隔离——一个 processor 抛异常被捕获并记录、不冒泡、不中断本轮，以便一个坏订阅者（如渲染层 markup 崩）不炸掉整轮考核。
6. 作为 runtime 开发者，我想让 processor 管线为后续 OTLP 导出留好口子（新增 processor 即接），以便 Tier C 接 Phoenix/Langfuse 时不改脊柱。
7. 作为 runtime 开发者，我想把一条 trace 渲染成 span 森林（turn → model → tool → subagent，含每 span 的起止 / latency / token），以便一眼看清一次运行的结构与成本。
8. 作为 runtime 开发者，我想在同一 HTML 视图里看到底层的 AgentEvent 事件流（按 seq 有序、含领域事件），以便把"脊柱是唯一真相、树只是投影"讲得可见。
9. 作为 runtime 开发者，我想让 HTML 是**自包含**的（内联 CSS / JS、无外部请求），以便它能 commit 进仓库、截图、作为简历 artifact 链接分享，且不依赖任何运行中的服务。
10. 作为 runtime 开发者，我想让 eval 报告与真机 trace 视图**共用同一渲染器**，以便"eval 用例即 trace"这一设计判断落成一份代码、两处复用。
11. 作为 runtime 开发者，我想用一条 CLI 子命令把 eval run 导成 HTML 报告（逐用例 pass/fail + token 成本 + prompt 版本 + 可展开的 span 树 / 事件流），以便回归结果可视化、可分享。
12. 作为 runtime 开发者，我想用一条 CLI 子命令把某次持久化的真机会话按 trace_id 导成 HTML，以便复盘那次 dogfood。
13. 作为 runtime 开发者，我想让 HTML 渲染是一个**纯函数**（trace 数据 → HTML 字符串，不碰时钟 / 随机 / 网络），以便可确定性单测、可回放。
14. 作为 runtime 开发者，我想让 processor 管线与 HTML 渲染器都住在 **kernel（领域无关）**、只认 AgentEvent 信封与 span，不认识 learning 领域类型，以便它们能被任何领域复用（呼应"领域无关 runtime"卖点）。
15. 作为 runtime 开发者，我想让 token 成本在 span 与报告里真实可读（复用 `Usage.total_tokens` computed_field），以便"带成本列"的可观测承诺兑现到可见处。
16. 作为 runtime 开发者，我想在真机会话结束时被告知 trace 存到了哪（trace_id / 路径），以便随手 `grandquiz trace <id>` 复盘。
17. 作为学习者，我想让 dogfood 出问题时能事后打开那次会话的 trace，以便定位是选题 / 出题 / 判卷哪一环出的岔，而不是只能凭记忆。
18. 作为简历项目作者，我想有一张能截图 / 链接的 eval + trace HTML，以便面试时"show, don't tell"地展示可观测 + 可评测的 runtime，而不是口头描述。
19. 作为 runtime 开发者，我想让真机落 trace 是**加订阅者式**的、对既有考核 workflow 逻辑零侵入（`assess_once` / `ingest_resource` 签名不变），以便可观测性是脊柱的投影而非业务耦合。
20. 作为 runtime 开发者，我想让 processor 管线不改变既有事件的发射时序与 payload，以便 10 个 eval 用例与 golden cassette 回放保持字节级确定、全绿。

## Implementation Decisions

**架构基线（来自 architecture.md / ADR）**

- **事件脊柱是脊柱**：新可观测能力 = 在脊柱上加一个 processor，不改 Runner / 编排、不另起回调系统。
- **kernel 领域无关**：processor 协议与 HTML 渲染器住 kernel，只认 `AgentEvent` 信封 + `Span`（`type` +
  元数据 + 不透明 payload），从不查看 payload 里的 learning 领域字段——故任何领域可复用。
- **确定性**：HTML 渲染是纯函数（trace 数据 → HTML 串），不引入时钟 / 随机 / 网络；时序来自持久事件的
  `seq` / `ts`。既有事件发射时序与 payload 不变（保 eval + cassette 回放字节级确定）。
- **自包含**：HTML 内联全部 CSS / JS、零外部请求 / CDN / 运行时依赖；可 commit、可截图、可离线打开。

**modules（新增 / 修改）**

- **processor 管线（kernel，改造既有 `EventSink`）**：定义 processor 协议（消费 AgentEvent；span 生命周期
  `on_span_start` / `on_span_end` 可由事件对派生或显式暴露）。`EventSink.publish` 把每个 processor 调用
  包进隔离边界——processor 抛异常被**捕获 + 记录、不冒泡、不中断扇出与本轮**。现有三个订阅者
  （TraceStore 持久化、CLI Rich 投影、eval 事件收集）改造为 processor。管线可注册任意多 processor，
  为 Tier C 的 OTLP processor 留口。**注意**：这只做 observer 侧异常隔离，不做 M4 HookManager 的
  interceptor 语义（`before_*` 改参 / 阻断）——那是 Tier B。
- **真机 trace 落库（interfaces/cli）**：`run_quiz` / `run_ingest` 注册一个由**独立 trace SQLite 库**
  （与 learning.db 分开，各自 `PRAGMA user_version` 与迁移）支持的 TraceStore processor；每次会话一个
  `trace_id`。会话结束打印 trace_id / 库位置。
- **HTML 渲染器（kernel，纯函数）**：输入 = 一条 trace 的（有序 AgentEvent 列表 + `build_span_tree` 投影的
  span 森林 + 汇总 token/latency 元数据），输出 = 自包含 HTML 字符串。渲染 span 森林（可折叠：turn →
  model → tool → subagent，每 span 显 type / 起止 / latency / token）+ 底层事件流（按 seq）。被 eval 报告
  与真机 trace 视图共用（eval 报告 = 每用例一段 + 汇总表：pass/fail + token 成本列 + prompt 版本列）。
- **CLI 子命令**：新增导出命令——一条把 eval run 跑完导成 HTML（复用 `evals` harness + 渲染器），一条按
  `trace_id` 从 trace 库读出某次真机会话导成 HTML（形如 `grandquiz report` / `grandquiz trace <id>`，
  最终名以实现为准）。

**关键契约 / 交互**

- 真机落 trace 经"注册 processor"实现，`assess_once` / `ingest_resource` 的签名与逻辑**一行不改**
  （可观测是脊柱投影，非业务耦合）。
- HTML 是纯函数产物：同一 trace 数据 → 同一 HTML（可确定性断言其结构内容）。
- trace 库 schema 复用既有 `kernel/db.py` 迁移机制（版本号 + 顺序 SQL，无时间戳列外的额外约束）与
  既有 `events` 表 / `build_span_tree`——本 PRD 不改 trace schema，只是把真机流也写进去。

## Testing Decisions

好测试只断言外部行为、不耦合实现细节。沿用仓库三条缝 + 一条新的纯函数缝：

- **缝 1 — 事件 / trace 流（主缝）**：脚本化假 provider 驱动真机 `run_quiz` / `run_ingest`（或其可测入口），
  断言会话结束后 trace 库里持久化了预期的 AgentEvent 流（含领域事件），且 `build_span_tree` 能重建预期
  span 森林。**被测**：CLI 的 trace 注册 + TraceStore 落库端到端。
- **缝 2 — 纯函数缝（新，HTML 渲染器）**：喂固定的（事件列表 + span 森林 + 元数据），断言产出 HTML 的
  **结构内容**存在（span 类型、token 总数、判决值、prompt 版本、事件条数），**不做字节级比对**（脆）。
  Prior art：`build_span_tree` 纯函数单测、`render_report` 文本表。
- **缝 3 — processor 隔离缝**：注册一个**故意抛异常**的假 processor + 一个正常 processor，断言：正常
  processor 仍收到全部事件、本轮 / 本次 run 正常完成、异常被记录而非冒泡。**被测**：processor 管线的
  异常隔离（闭掉 EventSink 不隔离的坑）。
- **回归护栏**：既有 10 个 eval 用例（`test_evals`）与 golden cassette 回放（`test_assess_replay`）在
  processor 改造后仍字节级全绿——证明发射时序 / payload 未变。

**被测模块**：kernel 的 processor 管线 + HTML 渲染器、interfaces/cli 的 trace 注册。确定性核心（渲染纯
函数、隔离逻辑）走 TDD；HTML 不追求像素级、只断言承载信息在。

## Out of Scope

- **OTLP exporter 本体 / Phoenix / Langfuse 接入**（Tier C）——本 PRD 只把 processor 管线的**口子**留好，
  不实现 OTLP processor。
- **M4 HookManager 的 interceptor 语义**（`before_*` 可改参 / 阻断）——本 PRD 只做 observer 侧的异常隔离
  （Tier B 再做 interceptor）。
- **FastAPI REST + SSE / `interfaces/api`**——HTML 是静态自包含产物，不是运行中的 web 服务；无网络投影。
- **交互式 / 实时刷新的 web UI**——只出静态 HTML（可截图 / commit / 链接），不做前端框架 / 实时流。
- **鉴权 / 多用户 / 部署**。
- **trace schema 改造**——复用既有 `events` 表 + `build_span_tree`，不加新列 / 新投影（火焰图等留后）。

## Further Notes

- **对标（学习 by 模仿，见 reference-map）**：HTML 查看器对标 [inspect_ai 的 Inspect View](https://inspect.aisi.org.uk/)（逐 sample 的 transcript + scorer 视图）；processor 管线对标 [openai-agents-python tracing](https://openai.github.io/openai-agents-python/tracing/) 的 `TracingProcessor`（`on_span_start`/`on_span_end` + `add_trace_processor`）。取形状、不 vendor 框架、保持自包含无依赖。
- **这一步兑现"show, don't tell"**：把三个招牌形容词里的"**可观测**"从宣称变成可点开 / 可截图 / 可链接的 artifact——简历叙事最高杠杆的一步（观测性 ~89% 采用率但 portfolio 里几乎无可见 trace/eval artifact）。
- **顺带闭坑**：processor 异常隔离闭掉了 `EventSink` 不隔离订阅者异常的已知坑（当初 Rich markup 崩的根因），也**部分**兑现了 M4 的异常隔离诉求（interceptor 语义仍留 M4）。
- **后续接力**：Tier A② 深化 eval（LLM-judge + A/B，本查看器正好承载 A/B 对比报告）；Tier C OTLP 导出复用本 PRD 留的 processor 口子。
- **验收里程碑**：真机 dogfood 一次考核后，`grandquiz trace <id>` 能导出一张自包含 HTML，看到 span 森林 + 事件流 + token/latency；`grandquiz report` 能把 10 个 eval 用例导成同款 HTML；四门全绿、既有回放不破。

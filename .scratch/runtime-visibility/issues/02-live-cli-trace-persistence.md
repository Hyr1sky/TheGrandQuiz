# 真机落 trace（CLI 注册 TraceStore processor + 独立 trace 库）

Status: done（merge 至 main 43c3ddb；四门全绿 277 passed；assess_once/ingest_resource diff=0）
Type: AFK

> 终审记：run_quiz/run_ingest 经 issue 01 的 register(processor) 注册 TraceStore（register() 首个真
> 生产调用方）落独立 trace.db；assess_once/ingest_resource 字节级不变。修：默认 trace 路径此前无测（生产
> 路径）→ 补默认路径测试（mutation 实测可杀"塌回 learning.db"）；run_ingest store 泄漏 → 挪进 try +
> None-guard；同路径 footgun（trace 静默为空）→ _resolve_trace_db 大声 ValueError。run_quiz 改整会话
> 单 EventEmitter（一条 trace / 每轮一棵 assessment 根）。

> 闭掉调查抓到的洞：真机 dogfood 的 CLI 目前不落 trace（只有 eval 装配订阅 TraceStore）。
> 让真实考核 / 入库会话持久化，重启后可回看。可观测是脊柱投影——考核 workflow 逻辑零侵入。

## Parent

[PRD: 让 runtime 可见（Runtime Visibility）](../PRD.md)

## What to build

真机 CLI 的 `run_quiz` 与 `run_ingest` 注册一个由**独立 trace SQLite 库**（与 learning.db 分开，各自
`PRAGMA user_version` 与迁移序列）支持的 `TraceStore` processor；每次会话一个 `trace_id`；会话结束打印
`trace_id` 与库位置（便于随手 `grandquiz trace <id>` 复盘）。

**`assess_once` / `ingest_resource` 的签名与逻辑一行不改**——落 trace 经"注册 processor"实现（可观测是
脊柱投影，非业务耦合）。trace 库复用 `kernel/db.py` 的迁移机制与既有 `events` 表 / `build_span_tree`，
本 issue 不改 trace schema，只是把真机事件流也写进去。

## Acceptance criteria

- [ ] 真机 `quiz` / `ingest` 会话把 AgentEvent 流持久化到独立 trace SQLite 库（与 learning.db 分开）
- [ ] 每会话一个 `trace_id`；会话结束打印 `trace_id` 与库位置
- [ ] `assess_once` / `ingest_resource` 的签名与逻辑未改（经注册 TraceStore processor 实现）
- [ ] trace 库复用 `kernel/db.py` 迁移机制；不改 `events` 表 schema
- [ ] 缝-1：脚本化假 provider 驱动可测入口 → 断言 trace 库持久化了预期 AgentEvent 流、`build_span_tree` 重建预期 span 森林
- [ ] 四门全绿

## Blocked by

- [01 — Processor 管线 + 异常隔离](01-processor-pipeline-isolation.md)（注册 TraceStore 为 processor，走管线）

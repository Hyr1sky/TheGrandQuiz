# LW-S1 — Article Workspace API 与可观测问答 run

Status: done（2026-07-24；FastAPI contract、run/SSE/cancel/trace、loopback 入口与 859 项全量回归通过）
Type: AFK

## Parent

[PRD：Local-first Web 学习工作台](../PRD.md)

## What to build

交付第一条可独立验收的 HTTP tracer bullet：FastAPI 从真实临时 learning.db 列出资源、读取 DocumentNode
大纲和有界节点正文；用户对显式材料 scope 发起问题后，后台 run 复用
`GroundedDocumentAnswer`、`EventEmitter` 和 `TraceStore`，通过 run 查询与稳定 SSE 投影返回精确 citation。

只抽取 CLI/API 已经共同使用的事件脊柱与 persistence 装配；不移动 Runner、SearchProvider 或终端交互等
仍只有 CLI 消费的配置。覆盖 PRD User Stories：1–10、19–24。

## Acceptance criteria

- [x] app factory 可注入临时 DB 路径和 fake provider，生产 factory 默认 loopback/.env 配置
- [x] health、resource list/detail、outline、node read HTTP 契约与 OpenAPI 可用
- [x] resource DTO 默认不返回 raw_content，node read 有显式字符上限
- [x] question 返回 202 + `run_id` / `trace_id` / `queued`
- [x] run 复用真实 GroundedDocumentAnswer，并把完整内部事件写入独立 trace.db
- [x] run status 覆盖 queued/running/succeeded/failed/cancelled；重复取消幂等
- [x] SSE 提供有序 backlog 和终态，不泄露 system prompt、完整模型消息或节点全文
- [x] answered、no_evidence、invalid_scope 和 provider error 映射为稳定结果/错误语义
- [x] HTTP/validation 错误统一为 code/message/retryable/trace_id
- [x] 只 mock provider 系统边界；SQLite/FTS/citation/event/trace 走真实实现
- [x] ruff、format、pyright、import-linter、目标 pytest 与全量 pytest 全绿

## Blocked by

None - can start immediately.

## Completion notes

- API run 是 `interface.api_run` 父 span，GroundedDocumentAnswer / model span 嵌套其下。
- SSE 支持 `after=N` 恢复，只投影白名单字段；内部完整 payload 只在 trace。
- run registry 当前为单进程内存；重启后按 trace_id 审计，不逆向重建 RunView。
- `uv build` 成功，wheel 包含 API 模块与 `grandquiz-web` console entrypoint。

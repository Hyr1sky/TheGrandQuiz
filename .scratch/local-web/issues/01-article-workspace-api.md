# LW-S1 — Article Workspace API 与可观测问答 run

Status: ready-for-agent
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

- [ ] app factory 可注入临时 DB 路径和 fake provider，生产 factory 默认 loopback/.env 配置
- [ ] health、resource list/detail、outline、node read HTTP 契约与 OpenAPI 可用
- [ ] resource DTO 默认不返回 raw_content，node read 有显式字符上限
- [ ] question 返回 202 + `run_id` / `trace_id` / `queued`
- [ ] run 复用真实 GroundedDocumentAnswer，并把完整内部事件写入独立 trace.db
- [ ] run status 覆盖 queued/running/succeeded/failed/cancelled；重复取消幂等
- [ ] SSE 提供有序 backlog 和终态，不泄露 system prompt、完整模型消息或节点全文
- [ ] answered、no_evidence、invalid_scope 和 provider error 映射为稳定结果/错误语义
- [ ] HTTP/validation 错误统一为 code/message/retryable/trace_id
- [ ] 只 mock provider 系统边界；SQLite/FTS/citation/event/trace 走真实实现
- [ ] ruff、format、pyright、import-linter、目标 pytest 与全量 pytest 全绿

## Blocked by

None - can start immediately.

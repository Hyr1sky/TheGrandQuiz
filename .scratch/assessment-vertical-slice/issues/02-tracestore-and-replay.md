# M2 — TraceStore + Replay Provider

Status: ready-for-agent
Type: AFK

## Parent

[PRD: 考核竖切 MVP](../PRD.md)

## What to build

trace 持久化 + 确定性回放——eval 的数据契约与确定性地基。`TraceStore` 订阅 AgentEvent 落 SQLite，span 成树（turn → model_call → tool_call → subagent）。Record/Replay Provider：录制模式把 LLM 响应按**键 = hash(messages) + role + resolved model id** 落盘，回放模式直接命中——键**必须含 role/model**，否则 basic=deepseek 与 enrich=qwen 的同样 messages 会撞键串模型，毁掉"完全确定"。回放是**事件流回放**——外部 I/O（本阶段 LLM 先行）作为事件落脊柱，重放事件流即重现整条链路。SQLite 迁移用版本号 + 顺序 SQL 文件（不上 alembic）。每 turn token 用量 + prompt 版本号进 trace。

端到端行为：跑一次对话 → 落 trace → 回放逐字节一致、不烧 token。

## Acceptance criteria

- [ ] `TraceStore` 订阅事件落 SQLite，span 树可重建
- [ ] Record 模式按 hash(messages) + role + resolved model id 落盘 LLM 响应（键含 role/model，防跨模型串键）
- [ ] Replay 模式一次对话逐字节回放一致（不消耗 token）
- [ ] 领域事件经 kernel `emit()` 上同一条脊柱、被 trace 泛型持久化（kernel 不认识具体类型）
- [ ] 每 turn token 用量 + prompt 版本号进 trace
- [ ] SQLite 迁移：版本号 + 顺序 SQL 文件
- [ ] trace 树重建 + replay 命中走 TDD
- [ ] CI 全绿

## Blocked by

- [01 — 事件脊柱 + 最小 runner + CLI](01-event-spine-and-runner.md)

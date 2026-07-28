# WR-O3 — 安全 trace projection REST/SSE

Status: done
Type: AFK

## Parent

[Web Runtime 上下文连续性与运行观测](../PRD.md)

## Acceptance criteria

- [x] `TraceObservatory` 作为 EventSink observer 消费同一 AgentEvent
- [x] snapshot 提供状态、时长、event/model/tool/error 数、token 与 span 时间线
- [x] SSE 支持 `after=N` backlog + live continuation
- [x] 服务重启后可从 TraceStore 回填历史 trace
- [x] 投影 allowlist 不包含 prompt/messages/arguments/output/user/material/secret
- [x] Chat session 返回 trace_id；assessment/run 复用既有 trace_id
- [x] FastAPI 公共契约、敏感字段缺失与 SSE resume 均有测试

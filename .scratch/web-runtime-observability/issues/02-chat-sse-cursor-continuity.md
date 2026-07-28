# WR-O2 — Chat SSE 跨 turn cursor 连续

Status: done
Type: AFK

## Parent

[Web Runtime 上下文连续性与运行观测](../PRD.md)

## Acceptance criteria

- [x] ChatPanel 持有 session 级 last sequence
- [x] 第二轮 EventSource URL 使用上一轮终态 sequence，而不是 `after=0`
- [x] 断线重连仍从当前 cursor 继续
- [x] 旧 turn 的 answer/navigation 不重复渲染
- [x] 新 session 重置 cursor
- [x] Vitest 通过两轮用户行为验证公共 DOM 与 EventSource URL

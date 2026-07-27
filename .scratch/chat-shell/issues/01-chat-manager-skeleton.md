# 01 — ChatManager 骨架 + ReAct endpoint

**What to build:** 后端新增 `ChatManager`，持有有状态的 `Runner` 实例，提供 ReAct session 的创建、消息发送和 SSE 事件流。用 curl / httpie 能创建 session、发消息、收到 agent 通过工具调用回答的材料问题。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] `ChatManager` 类：持有单个 `Runner` 实例，管理 session 创建与销毁（新 session 替换旧 session）
- [ ] `POST /api/v1/chat/sessions` 创建 session，返回 `session_id`
- [ ] `POST /api/v1/chat/sessions/{session_id}/messages` 发消息，触发 `run_agent_turn`，返回 `202` + `turn_id`
- [ ] `GET /api/v1/chat/sessions/{session_id}/events` SSE 事件流，投影 `AgentEvent` 为 chat UI 事件（`chat.turn_started`、`chat.tool_call`、`chat.turn_ended` 等）
- [ ] Runner 构造时注册现有领域工具（`register_learning_tools`），不含导航工具
- [ ] Runner 配置 `ContextBuilder`（学情注入）+ `SummarizingHistoryCompressor`
- [ ] 所有 agent 事件进 `trace.db`（复用 `TraceStore` + `EventSink` 注册）
- [ ] `create_app` 中创建 `ChatManager` 并挂到 `app.state`，lifespan 中 `aclose`
- [ ] Fixture script (`run_web_fixture.py`) 同步支持 ChatManager（fake provider 能回答对话）
- [ ] FastAPI TestClient 测试：session 创建/销毁幂等性、消息→SSE 事件流顺序与终态、多轮上下文承接、工具调用事件投影

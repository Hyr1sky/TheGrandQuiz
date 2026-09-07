# ADR-0017: 原生 Anthropic Messages 使用独立协议 Adapter

- 状态：已接受
- 日期：2026-09-07

## 背景

Provider 控制面已经把用途、Profile、Connection、模型身份和恢复策略拆开，但此前唯一 wire API 是
OpenAI Chat Completions。Anthropic Messages 的 system、content blocks、tool_use/tool_result、stop reason
和 SSE 生命周期不同；把它伪装成“另一个 OpenAI base URL”会丢失工具顺序、终态与错误事实。

现有 Chat/Runner 已经是明确消费者：它需要文本、原生流和由 Runtime 执行的 client tools。Adapter 不能
引入第二套工具循环，也不能让 SDK 隐藏重试破坏 ADR-0015/0016 的 attempt 与 fallback 上限。

## 决策

1. `ModelConnection.wire_api` 显式支持 `anthropic_messages`；协议不由 endpoint 域名、模型名或厂商品牌猜测。
   ModelProfile 仍表示具体部署，Connection 仍拥有 endpoint 与凭证引用。
2. 使用官方 `anthropic` Python SDK 作为 HTTP/SSE 与连接层，固定 `max_retries=0`。应用 Model Call Executor
   继续是 retry、Retry-After、取消、fallback 与 Attempt 观测的唯一 owner。
3. Messages Adapter 只接受前置 system 文本、user/assistant 文本、client tool_use/tool_result、并行工具
   调用、usage、complete 与 native stream。工具由 Runner 执行，SDK Tool Runner／Agent loop 不进入系统。
4. assistant tool calls 保留 id、名称、JSON 对象参数与顺序；相邻 tool results 合并为下一条 user blocks，
   必须完整覆盖 pending ids，错误结果显式标记 `is_error=true`。孤立、缺失、重复或插队结果在联网前失败。
5. Anthropic `max_tokens` 来自 Profile 的必填 `max_output_tokens`，不使用 Adapter 隐式常量。缓存创建／读取
   token 计入内部 prompt usage，流式 output usage 按厂商累计值读取。成功响应缺失必填 usage 或累计值倒退
   都视为协议错误，不能用 0 掩盖未知事实。
6. `end_turn`、`stop_sequence` 和与 client tool blocks 一致的 `tool_use` 才能生成 Completion。
   `max_tokens`、context exceeded、`refusal`、`pause_turn` 进入有限安全的响应协议错误，不把截断、拒绝或
   continuation 伪装成成功。
7. thinking/signature、引用／多模态、server tools、MCP、container、厂商侧 fallback 与未知 content
   语义首版 fail-closed。新增能力必须先有公共表示和真实消费者，不能在 Adapter 内静默丢弃。
8. HTTP 与 HTTP 200 后的 stream error 都归一成 provider-neutral `ProviderFailure`；只保留 category、有限
   provider code、status 与 Retry-After。stream 已收到任何事件时标记不可自动重放，不暴露错误正文、
   request id、endpoint 或凭证。
9. 同一个 Model/StreamingModel Interface、Recording/Replay、Budget、Trace 和 Eval 消费两个协议；不为
   Anthropic 建平行事件总线、回放格式或业务调用入口。

## 后果

用户可以在不改业务代码的情况下把现有 Chat/Runner 绑定到原生 Messages，并继续得到同一套错误、重试、
fallback、Trace 与 Replay 行为。代价是首版刻意拒绝一批 Anthropic 特有能力，也要求运维者明确填写输出
上限与能力事实。官方 SDK 是新的运行依赖，但它不拥有策略、工具执行或 Agent 生命周期。

本决策不承诺 Bedrock/Vertex/Foundry 承载方式、thinking、多模态、服务端工具、continuation、厂商侧
fallback、质量路由或真实付费 smoke；这些都需要各自的消费者、授权和 conformance 证据。

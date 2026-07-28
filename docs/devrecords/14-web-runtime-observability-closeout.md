# Web Runtime 上下文连续性与运行观测收口

> 记录日期：2026-07-28
>
> 对应范围：WR-O1–WR-O4
>
> 目标：修复真实 Web dogfood 暴露的当前材料、跨轮 SSE 与考核启动问题，并把既有 trace/event
> 基座投影成运行中可用的安全观测界面。

## 1. 为什么这一轮仍从既有事件脊柱生长

项目已经有完整 `AgentEvent → TraceStore → trace.db/HTML` 链路，缺的不是另一套 logger，而是面向本机用户
的实时安全投影。因此本轮没有引入前端直连 SQLite、轮询原始 payload、Langfuse/Phoenix 或第二条回调总线。

新增 `TraceObservatory` 位于 `interfaces/api`，以 observer 身份注册到各运行已有的 `EventSink`。同一事件仍先
由 durable `TraceStore` 落库，再唤醒 observatory SSE；服务重启后，历史 snapshot 也能直接从 TraceStore
重建。Runner、考核 workflow、Learning Memory 与 trace schema 都没有改变。

浏览器只收到 allowlist 投影：

- event type、sequence、timestamp；
- span / parent span、状态与耗时；
- model/tool/error/recovery 计数；
- tool name 与 token usage。

prompt、messages、用户输入、模型输出、工具参数/结果、材料正文、Evidence quote 和密钥均不进入公共 DTO。
这不是在前端“隐藏字段”，而是后端模型根本没有这些字段。

## 2. 当前材料成为可信 turn context

此前顶栏选中 LearningResource 只影响阅读面板，Chat 收到的仍只有自然语言。用户说“当前材料”时，ReAct
只能在全局 KB 猜测范围。

现在 `MessageRequest` 接受 optional `active_resource_id`。ChatManager 先用 LearningStore 验证 exact id；
不存在时返回 `resource_not_found`，不会退回全库。验证后的 id 进入一个小预算动态 system Partition：

```text
active_resource_id=<exact id>
“当前材料”与“本文”只能指向该资源
```

前端标题和正文不会进入这段受信上下文，用户消息与跨轮 history 也不被改写。没有选中材料时仍保持原有全局
KB 行为。真实 dogfood 中，“当前材料主要讨论什么”正确落到顶栏所选的 Agent Memory 材料。

## 3. 修复跨轮 SSE 回放

Chat UI 原先每次发送后都以 `after=0` 新建 EventSource。第二轮会先收到第一轮的
`chat.turn_ended`，导致旧回答重复，甚至提前关闭当前流。

现在 sequence cursor 属于 Chat session，而不是某次 React send：

1. 每个 `ChatUiEvent` 推进 `lastSequence`；
2. 下一轮以 `events?after=<lastSequence>` 订阅；
3. 同一流内部断线重连继续使用最新 cursor；
4. 新 session 才重置为 0。

Vitest 用两轮真实 DOM 行为验证第二个 URL 为 `after=2`，两个回答各渲染一次。真实 LLM dogfood 也连续完成
“概括材料”与“用三个关键词概括刚才答案”，没有旧 turn 重放。

## 4. 修复考核导航后不出题

真实事件中 `start_assessment → chat.navigation → chat.turn_ended` 已经正确，故障发生在 React
`AssessmentPanel`。开发模式的 StrictMode 会执行 effect setup → cleanup → setup；旧 `started` 布尔值在
第一次 cleanup 后仍为 true，第二次 setup 不再订阅结果，于是页面永远停在“考核准备中”。

修复后，组件按 `resourceId + rounds + questionType` 缓存同一个 start promise。StrictMode 的第二次 setup
复用请求并重新绑定 active consumer，既不会重复创建 assessment，也不会丢失首个结果。对应测试显式在
`StrictMode` 下断言只发出一次 POST 且题目可见。

真实验收已经从 Chat 输入“请基于当前材料出 1 道选择题”，随后工作面板显示题干、四个选项、Evidence
reveal 与提交按钮。Assessment trace 为 `2f2b2c06ec1e455f911e66e9a26e4e0e`。

## 5. 罗盘运行观测

底部罗盘由装饰性容器改为语义 button，补齐 hover 边框/底色、pressed 下压、focus ring 与
`aria-expanded`。点击后打开非模态 `dialog`：

- 顶部 beacon 显示等待、运行、等待输入、完成、失败或取消；
- 六个小指标显示 event、model、tool、token、错误/恢复与总耗时；
- span 时间线按父子执行顺序展示类型/工具名、耗时与 token；
- snapshot 加载后从最后 sequence 继续 SSE，80ms 合并刷新避免每个事件都触发一次请求；
- loading、no trace、API error 与断线重连均有独立文案。

抽屉使用现有主题 token；暗色是深蓝观测舱，亮色是暖纸色星图仪表。它会优先观察当前 Assessment trace，
没有考核时观察 Chat trace。

真实 Chat trace `9cc3cf6c33d94ecc8f18e095689212d9` 在开放问答运行中显示 3 次 model、1 次 tool、
9,943 token、零错误/恢复及完整 `agent_turn → model → answer_from_documents →
learning.grounded_answer` 时间线。切到考核后，抽屉自动展示 assessment trace，并在题目出现后进入
“等待输入”。

## 6. API 与测试

新增公共接口：

```text
GET /api/v1/observability/traces/{trace_id}
GET /api/v1/observability/traces/{trace_id}/events?after=N
```

Chat session 创建响应同时返回 `session_id` 与 `trace_id`；已有 Grounded Answer run 和 Assessment 继续复用
自身 trace id。OpenAPI JSON 与 TypeScript schema 已重新生成。

收口结果：

- Python 全量：`889 passed`
- Web Vitest：`30 passed`
- ruff check / format、pyright、import-linter：通过
- TypeScript、Vite production build、Sites worker：通过
- 真实浏览器：当前材料、两轮 Chat、实时观测、Chat → Assessment → 题目出现、亮暗主题全部通过

## 7. 仍然保留的边界

观测抽屉不是通用 dashboard：不搜索历史 trace、不显示 prompt、不做成本计费，也不承诺远程多用户安全。
它服务当前本机运行的理解与调试。更完整的配置/prompt 编辑、历史 trace 浏览与 Acquisition 可恢复审批，
应各自建立新竖切，不能把内部 payload 逐步“临时”加回这个安全投影。

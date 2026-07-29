# Web Chat 体验收口：从“等完整答案”深化为“可见、可停、会引导”

> 日期：2026-07-29
> 范围：v0.1.0 功能 RC 的 Chat 原生流式输出、turn 取消、首次引导与浏览器验收。

## 1. 这一步为什么值得在 RC 前做

此前 Web Chat 的结果虽然也经 SSE 返回，但模型调用仍是：

```text
等待 Provider 完整 completion → Runner 注册结束事件 → SSE 一次返回完整回答
```

所以“SSE 已存在”并不等于“用户能看到流式回答”。用户发送消息后，页面长时间只有“正在思考”，最后整段
文字突然出现；误触后，关闭浏览器 EventSource 也只是不再看事件，后端模型和工具仍继续运行。

这轮把体验拆成三个可单独验收的问题：

1. 回答能否边生成边显示；
2. 当前 turn 能否真的停止；
3. 第一次打开产品的人，能否知道材料、大纲、Chat 和 Observatory 分别做什么。

没有扩展文章管理、知识点管理或 Web Acquisition，避免 RC 收口重新变成功能开发。

## 2. 流式化不是把 SDK chunk 直接扔给浏览器

本轮新增的关键接口在 `providers/base.py`：

```python
class TextDelta(BaseModel):
    text: str


class CompletionFinished(BaseModel):
    completion: Completion
```

`OpenAICompatProvider.stream_complete()` 使用 OpenAI-compatible SDK 的 `stream=True`。厂商返回的
文本碎片、tool-call id、函数名和 JSON 参数碎片先在 Provider 内组装，Runner 只会看到两种稳定事件：

- `TextDelta`：可以安全展示的文本增量；
- `CompletionFinished`：唯一终点，携带原有的权威 `Completion`。

完整链路因此是：

```text
Provider SDK chunk
  → TextDelta / CompletionFinished
  → Runner: model.output_delta AgentEvent
  → ChatManager: chat.message_delta
  → SSE sequence
  → React 更新同一个回答气泡
```

这保持了项目最重要的设计判断：trace、SSE、观测和回放仍消费同一条 `AgentEvent` 脊柱，没有新增旁路
callback。completion-only 的 Echo/Replay/fake provider 也没有被迫重写；`BudgetedProvider` 会在完成原有
请求预算检查后，把完整回答降级成一个 delta + 一个 finished 事件。

为了避免每个 SDK token 都触发一次 SQLite commit，Runner 会立即发出首个 delta，后续按 48 个字符确定性
合批，并在 `CompletionFinished` 前冲刷剩余文本。这样保留首字反馈，又控制 trace 写放大；没有引入依赖
墙钟的计时器，因此 Replay 和单测顺序仍稳定。

### 为什么没有新建 `adapters/`

`providers/` 本来就是外部模型协议的适配边界。把 SDK stream 另放到顶层 `adapters/`，会让
“模型请求由谁拥有”出现两个答案。现在厂商差异留在 `providers/llm.py`，kernel 只依赖归一协议，职责更清楚。

## 3. “停止生成”现在真的会停止后端

新的 HTTP command 是：

```text
POST /api/v1/chat/sessions/{session_id}/turns/{turn_id}/cancel
```

它按 `turn_id` 精确取消当前 task，并等待取消完成。Runner 会依次闭合活动中的 model/tool/agent span，
写入 `status=cancelled`；Chat 再发出稳定终态 `chat.turn_cancelled`。React 不会提前关闭 SSE，而是等这个
终态到达后才恢复发送状态。

这里特意锁住了三个边界：

- 重复取消同一 turn 是幂等的；
- 用旧 turn id 取消时返回 `turn_not_found`，不能误伤较新的 turn；
- 已取消的 turn 不能随后又发出 `chat.turn_ended`，同一 session 可以马上继续下一轮。

关闭 EventSource 仍只表示“停止接收页面事件”，不再被误当作业务取消。

## 4. 首次引导如何保持轻量

首次进入时会出现 4 步 coach mark，分别锚定：

1. 当前材料；
2. 大纲/考核进度；
3. Chat；
4. Runtime Observatory。

完成或跳过后只在浏览器写入版本化标记 `grandquiz.onboarding.v1=completed`；顶栏 `?` 可以随时重开。
移动端不强行跟随狭窄锚点，改为底部浮层，避免遮住主要内容。

Chat 空状态另有三个示例：

- 请结合当前材料解释核心观点；
- 考我 3 道简答题；
- 怎样查看本次运行过程？

点击示例只填入输入框，不会替用户自动发送。这样既让能力可发现，也保留用户对外部 LLM 请求的最后确认。

## 5. TDD 怎样把四层契约锁住

本轮没有只测最终 DOM，而是在四个 seam 分别先写失败测试：

| Seam | 锁住的行为 |
| --- | --- |
| `StreamingProvider.stream_complete()` | 原生文本 delta、tool 参数碎片组装、唯一最终 Completion |
| `Runner.run_agent_turn()` | delta 成为 AgentEvent，最终文本一致，取消后 span 成对闭合 |
| Chat HTTP/SSE | 取消幂等、旧 turn 不误伤新 turn、稳定 `chat.message_delta/turn_cancelled` |
| React / Playwright | 单一增量气泡、不重复 final、停止按钮、首次引导可跳过/重开 |

确定性浏览器 fixture 也实现了 `StreamingProvider`，因此 Playwright 验收的不是纯前端伪事件，而是真正经过
FastAPI、Runner、TraceStore 和 SSE 的完整链路。

## 6. 本轮验收结果

- Python：`905 passed`；
- Web unit：`41 passed`；
- Playwright：桌面/移动端 `12 passed`；
- Eval：`17/17`（本轮未改变 Eval 用例与离线 Replay 契约）；
- ruff、format、pyright、import-linter、Web lint/typecheck、OpenAPI 生成与 production build：通过。

浏览器初次运行因本机缺少 Playwright 自带 Chromium，按仓库既有开关改用系统 Chrome 后通过；这属于测试
环境缺件，不是产品回归。

## 7. 仍然刻意没有做什么

- 没有把 chain-of-thought 或内部推理暴露给前端；
- 工具卡只展示稳定语义和安全结果，不展示厂商原始 chunk；
- 没有引入 WebSocket、队列或新的顶层 adapter 层；
- 没有改变考核 workflow 的“LLM 判卷、代码记账”边界；
- 没有把文章/知识点管理和 Web Acquisition 偷塞进 v0.1.0。

完成这一步后，功能 RC 的主要交互风险已经从“代码已实现但用户不知道发生了什么”，收敛为可在 3–5 人
小范围测试中直接观察的行为：首次是否看懂、回答等待是否可接受、误触能否恢复、trace 是否能解释。

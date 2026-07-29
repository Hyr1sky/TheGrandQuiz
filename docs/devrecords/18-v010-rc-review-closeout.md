# v0.1.0 RC 审查收口：让“能运行”与“可发布”重新对齐

> 日期：2026-07-29
> 范围：Web Chat 流式输出、取消终态、安全 SSE 投影、浏览器验收与 production bundle。

## 1. 为什么自动测试全绿，审查仍然发现阻断项

上一轮功能已经能流式回答、停止生成并显示新手指南，但代码审查发现三个“正常演示不容易碰到”的边界：

1. 部分 OpenAI-compatible 模型会先输出一句文字，再发起 tool call；流式适配器却把两者当成互斥。
2. Chat UI 没有渲染工具参数，但后端仍把完整参数发到了浏览器 SSE。
3. React 源码已经更新，Python wheel 内的 production Web bundle 仍是旧文件。

这说明“源代码功能测试通过”只证明开发态路径可用；发布还必须检查供应商协议、安全投影和最终安装产物。

## 2. Provider：文本与 tool call 是两条可并存的信号

旧实现用 `mode = text | tools` 强制二选一。这样遇到“我先查一下”之后调用工具的模型，就会抛
`ProviderStreamProtocolError`。

现在 Provider 分别累计两类信息：

```python
if content:
    text_parts.append(content)
    yield TextDelta(text=content)

for raw_tool_call in raw_tool_calls:
    # 在 Provider 内组装 id / name / arguments 碎片
    ...
```

流末尾仍只产生一个 `CompletionFinished`，其中 `text` 与 `tool_calls` 都是权威结果。Runner 的接口没有变，
OpenRouter 或其他 OpenAI-compatible adapter 的差异继续被藏在 `providers/` module 内。

顺手把 complete/stream 共用的 model、messages、thinking 开关和 tools 准备收进
`_PreparedChatRequest`；两条路径也统一调用 `_decode_tool_arguments()`。这不是增加新抽象层，而是把同一份
厂商协议知识放回唯一的实现位置。

## 3. Chat：内部参数留在 trace，浏览器只拿稳定语义

工具参数对 Runtime 审计有价值，所以 `TOOL_CALL_STARTED` 的内部 `AgentEvent` 和 trace 仍保留完整参数；
但浏览器的 `chat.tool_call` 现在只投影：

```json
{
  "turn_id": "...",
  "name": "query_weak_concepts"
}
```

也就是说，DevTools 不再收到 query、resource id 或其他内部调用参数。需要排障时看 trace，而不是不断扩大
浏览器契约。

## 4. 终态：Runner 事件是唯一事实来源

此前 Runner 已发出 `AGENT_TURN_ENDED`，`ChatManager._run_turn()` 又根据 Python task 的返回或异常自行生成
`chat.turn_ended/chat.error/chat.turn_cancelled`。两条判断路径短期结果相同，但以后很容易漂移。

现在链路统一为：

```text
Runner 发出 AGENT_TURN_ENDED
  → ChatManager._project_event()
  → succeeded / failed / cancelled 的稳定 Chat UI event
  → SSE
```

失败只向浏览器暴露稳定的 `turn_failed`，内部异常类和详细堆栈仍进入 trace。取消的幂等集合也在 cancelled
事件投影时更新，因此 HTTP cancel 必须等事件脊柱真正完成终态后才能返回。

## 5. 浏览器验收不再只等最终结果

Playwright 新增两条关键断言：

- 跳过首次指南后刷新页面，指南不会再次自动出现，但仍可用 `?` 主动重开；
- fixture 在首段 delta 后保留一个确定性窗口，浏览器必须在 final 前看到 partial answer。

这让“退回一次性 completion”或“忘记读取 localStorage”的回归能够真正把浏览器测试打红。

## 6. 为什么这次不继续优化移动端

v0.1.0 的用户是在个人电脑上启动 loopback 服务，桌面浏览器是主要产品环境。现有移动端基础回归继续保留，
但窄屏导航、引导锚点和跨浏览器矩阵不在本次 RC 扩展。出现托管、远程访问或多设备需求时，再把响应式信息
架构作为独立竖切处理，避免现在为尚不存在的部署形态扩大 scope。

## 7. 发布产物与验证

执行 `npm run build:package` 后，`web/dist/client` 与
`src/grandquiz/interfaces/api/static` 已逐文件一致，新 wheel 将包含当前 Web 功能。

最终自动门：

- Python：`906 passed`
- Eval：`17/17`
- Web unit：`41 passed`
- Playwright：桌面/移动基础回归 `12 passed`
- ruff、format、pyright、import-linter、OpenAPI、Web lint/typecheck、production package build、
  Sites adapter tests：通过

自动门完成后仍不自动 push 或创建 tag。下一步由仓库所有者使用真实 Provider 做桌面 dogfood；通过后先进入
`v0.1.0-rc.1` 小范围体验，再决定正式 `v0.1.0`。

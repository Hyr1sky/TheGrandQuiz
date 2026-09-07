# Provider 原生 Anthropic Messages Adapter

日期：2026-09-07。Provider 控制面第六竖切（PCP-06），基线 `0516c54`；独立
`codex/provider-control-plane` worktree，本地提交前验收。

## 交付行为

`model-config.v1` 的 Connection 可显式选择 `anthropic_messages`。Model Runtime 根据 wire API 分配原生
Messages transport；业务仍只接收用途绑定后的 Model，不读取厂商 URL、凭证或 SDK 类型。Messages 必填的
`max_tokens` 来自 Profile `max_output_tokens`，官方 SDK 的隐藏 retry 固定关闭。

Adapter 将前置 system、文本、ToolSpec、assistant tool_use、相邻 user tool_result 与 tool error 在边界内
保真互译；并行工具结果完整覆盖 pending ids 后才联网。现有 Runner 已用本地官方 SSE fixture 完成一次
“调用工具 → Runtime 执行 → 回传结果 → 最终文本”的两轮真实消费路径，工具执行与状态机没有迁入 Adapter。

完整与流式响应都只在 end_turn／stop_sequence／client tool_use 语义闭合时产生 Completion。流按 block
index 组装工具 JSON，校验 block 生命周期、累计 usage 与唯一 message_stop；HTTP 200 后的 error 仍转成
typed ProviderFailure，且收到任意事件后不可自动重放。截断、上下文超限、拒绝、continuation、thinking、
引用／多模态、服务端工具及未知 block 均 fail-closed。

## 验收

| 检查 | 结果 |
| --- | --- |
| Anthropic Adapter 专项 | 33 passed；官方 SDK JSON/SSE 解码走本地 MockTransport |
| Provider/Runner/Replay 交叉回归 | 175 passed |
| Python 全量 pytest | 1385 passed |
| Ruff lint / format | 通过；309 files formatted |
| Pyright strict | 0 errors / 0 warnings |
| Import Linter | 1 kept / 0 broken |
| Eval harness | 17/17 passed |
| Web Vitest | 93 passed / 13 files |
| Web lint / typecheck | 通过 |
| OpenAPI / TypeScript schema | 无漂移 |
| Web production/package build | 通过；4861 modules transformed，Python static 无漂移 |
| Sites adapter tests | 4 passed |
| Playwright | 29 passed / 1 既有 mobile voice skip |
| sdist / wheel smoke | 构建成功；Adapter 与 Anthropic 依赖元数据入包；隔离 offline 安装、CLI、import、17/17 report 通过 |

专项测试还固定了 SDK `max_retries=0`、单次 wire request、并行工具与 error result、缺失 usage 不冒充 0、
累计 output usage 不倒退、HTTP 200 后 error、截断无成功 terminal、取消关流，以及真实现有 Runner 的
两轮工具路径。普通测试全部使用官方 JSON/SSE 形状、假 Model、cassette 或本地 MockTransport；没有真实
厂商网络、收费调用、凭证或用户数据上传。

## 非目标

Anthropic thinking/signature、多模态、server tools、MCP、container、continuation、厂商侧 fallback、
Bedrock/Vertex/Foundry、OpenAI Responses、智能路由和真实付费 smoke。没有修改选择题／简答题命题、判卷、
Evidence 或学习记账逻辑。

这是路线图内、与命题业务解耦的独立支撑轨；不改变 composite/exploratory 的产品优先级。

# Provider 有界重试、尝试观测与故障回放

日期：2026-09-07。Provider 控制面第四竖切（PCP-04），基线 `06a9aba`；独立
`codex/provider-control-plane` worktree，本地提交前验收。

## 交付行为

应用现在是模型传输 retry 的唯一 owner。所有真实模型消费者经 kernel 的统一 Model Call Executor 执行；
既有 `model.started/ended` 仍表示一次业务 Logical Call，真实网络请求展开为
`model_attempt.started/ended` 子 span，决定与等待分别记录 `model.retry_decided` 和
`model_retry_wait.started/ended`。Runner、Reader、出题、判卷、干扰项评审、材料问答、摘要和 Eval 的
提示词、解析、状态机、工具执行与业务修复次数未改变。

默认策略总计最多 3 次传输 attempt（包含首次），logical deadline 90 秒，本地退避从 0.5 秒指数增长到
8 秒、±20% jitter，累计等待最多 30 秒。策略从 `model-config.v1` 的可选 `[retry]` 严格解析；时钟、
sleeper 与 RNG 可注入。仅 retryable 的限流、连接、超时、冲突和 5xx 可恢复；鉴权、权限、坏请求、
not-found、永久额度与 unknown 默认停止。OpenAI SDK `max_retries=0` 由测试钉住，避免隐藏循环乘法。

## Retry-After、取消与流式安全

OpenAI-compatible Adapter 在异常边界读取 `Retry-After` 秒数或 HTTP-date，只保留规范化数值／时间事实；
非法、负数、NaN/Inf 与冲突值不会进入事件。有效服务端等待是最小值，不会被本地 backoff 上限截短；若
超过总期限或累计等待预算则停止，不提前发请求。过去的 HTTP-date 按 clock skew 已满足处理。

请求中或等待中的 `CancelledError` 原样传播并闭合活动 attempt/wait/logical span。Adapter 收到任意上游
stream chunk 就标记 response started；任意 TextDelta 或显式 replay-unsafe 事实都会停止自动重放。
活动流在中途失败和取消时由 Adapter `finally` 关闭。失败未返回 usage 时记录 unknown，不能据此声称远端
没有计费或副作用。

## Replay、Trace 与公共投影

`model-cassette.v4` 按同一 request key 记录 typed ProviderFailure/Completion 序列；v3 成功录制保持只读
兼容。离线测试录制“429 + Retry-After → 成功”，再以相同 policy/clock/RNG 回放，实际 attempt 事件与
等待完全一致，不把最终成功伪装成一次请求。

token 总计仍只读取 logical `model.ended` 的最终成功 usage，attempt usage 不重复累加。安全 Trace、REST、
SSE 与诊断包新增有限的 retry decision、Retry-After、response-started 与 replay-safe 投影，厂商原文、
header、request id 和 vendor code 不公开。一次临时失败若随后恢复成功，运行摘要显示成功／进行中，不把
已吸收的 attempt 错误误报为最终失败。Web Observatory 将决定显示为“传输重试／停止重试”、有限原因和
等待时长。

[ADR-0015](../adr/0015-application-owned-provider-retry.md) 固化唯一 owner、总预算、流式安全和 v4 Replay
决策；[配置指南](../guides/model-profiles.md)、TOML 样例、架构、路线图、README 与面试材料同步更新。

## 本地验收

| 检查 | 实际结果 |
| --- | --- |
| Python 全量 pytest | 1331 passed |
| PCP-04 retry 契约 | 16 passed |
| Ruff lint / format | 通过；305 files formatted |
| Pyright strict | 0 errors / 0 warnings |
| import-linter | 1 kept / 0 broken |
| 离线 Eval | 17/17 |
| Web Vitest | 92 passed，13 个文件（单 worker） |
| Web lint / typecheck | 通过 |
| OpenAPI / TypeScript schema | 已重新生成 |
| Web build:package / 静态资产 | 通过；Observatory 生产资产已同步 |
| Sites adapter | 4 passed |

所有 Provider／HTTP 测试使用假模型、本地 fixture 或 MockTransport；没有真实厂商网络、收费调用、凭证或
用户数据上传。

## 非目标

本票不实现跨 Profile fallback、自动路由、熔断、分布式限流、并发竞速、unsafe replay override 或
Anthropic Messages。业务结构化输出修复、工具参数修复和重新命题保留原计数与语义。下一票 fallback
必须复用本票的 Logical Call 总 deadline、attempt 与 replay-safety，不能按候选重置预算。

这是路线图内、与命题业务解耦的独立支撑轨；不改变 composite/exploratory 的产品优先级。

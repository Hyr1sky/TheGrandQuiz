# Provider 显式授权 fallback

日期：2026-09-07。Provider 控制面第五竖切（PCP-05），基线 `4bd3217`；独立
`codex/provider-control-plane` worktree，本地提交前验收。

## 交付行为

`model-config.v1` 新增默认关闭的 `[fallback]` 与按用途排序的 `[fallback_candidates]`；一次显式
`ModelSelection` 也可同时给出 `fallback_profile_ids`。只 pin 主模型时没有候选链，已配置的 key 不会被
自动发现成备用。缺失、重复、回到主部署或不同别名指向同一配置指纹都会在分配传输前失败。

候选复用 PCP-03 能力门，并增加 operator-declared `context_window_tokens`、`max_output_tokens` 与排除部署
指纹约束。启用链时备用容量不得低于主模型；实际 tool/stream 请求在 Attempt 前再检查候选能力，不合格
候选不发网络请求，也不会通过删 Evidence 或截断材料继续。

统一 Model Call Executor 在同一 Logical Call 中维护当前候选和局部次数。可用性故障在局部上限内先走
同模型 retry，到顶后才按冻结顺序切换；所有候选共享 PCP-04 的总 attempt、deadline 与累计等待预算。
默认只有 rate limit、timeout、connection、server error 能切换。鉴权、配置、永久额度、未知错误、取消、
任意已见 stream event 或 replay-unsafe 事实均停止。

## 观测与回放

新增 `model.fallback_decided`，有限 action/reason 与来源/目标候选序号进入 AgentEvent；每个 Attempt 携实际
脱敏 ModelIdentity，逻辑成功记录最终 selected identity 与切换数，逻辑失败保留有序安全失败链。Trace、
REST、OpenAPI、诊断包和 Web Observatory 只投影 allowlisted 字段，不公开 Profile label、endpoint、模型名、
凭证、request id 或厂商错误正文。

BudgetedModel、completion-as-stream 与 RecordingModel 保留冻结候选 plan。cassette 仍按每个候选身份分键；
候选顺序、fallback policy 和共享 retry policy 进入策略指纹，离线 failure→fallback→success 可重演同一
Attempt/decision 序列。

## 安全边界

fallback 只重发尚未完成的当前模型请求，不重跑 workflow、工具、审批、命题或判卷状态机。没有响应只表示
本地 replay-safety 条件成立，不证明远端零处理、零计费或零副作用；本票没有引入跨工具事务幂等。

## 本地验收

| 检查 | 结果 |
| --- | --- |
| fallback 专项测试 | 11 passed |
| Python 全量测试 | 1352 passed |
| Ruff lint / format | 通过；307 files formatted |
| Pyright strict | 0 errors / 0 warnings |
| Import Linter | 1 kept / 0 broken |
| Eval harness | 17/17 passed |
| Web Vitest | 93 passed / 13 files |
| Web typecheck / lint | 通过 |
| OpenAPI / TypeScript schema | 已重新生成并通过类型检查 |
| Web production build | 通过；4861 modules transformed |
| Sites adapter tests | 4 passed |

所有 Provider 行为使用假 Model、cassette 或本地 MockTransport；没有真实厂商网络、收费调用、凭证或
用户数据上传。

## 非目标

质量不佳自动换模、智能路由、hedging、熔断、健康探测、动态价格、新协议 Adapter 与 unsafe replay
override。没有修改选择题/简答题命题、判卷、Evidence 或学习记账逻辑。

这是路线图内、与命题业务解耦的独立支撑轨；不改变 composite/exploratory 的产品优先级。

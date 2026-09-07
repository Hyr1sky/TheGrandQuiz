# ADR-0015: 应用拥有 Provider 传输重试

- 状态：已接受
- 日期：2026-09-07

## 背景

ADR-0013/0014 已把模型执行、用途绑定与显式选择拆开，ProviderFailure 也能提供有限的失败事实。但一次
业务模型调用仍只有“开始/结束”两个事件：临时限流或网络故障若交给 SDK 暗中重试，Runtime 无法说明实际
请求次数、等待、取消和未知用量；若每个业务调用者各自重试，又会把传输恢复和结构化输出修复混为一谈。

流式调用还存在更强的安全边界：一旦文字或其他上游 chunk 已到达，再次请求可能产生两个不同回答，不能
拼接成一次成功。失败前没有输出也不证明远端没有计费，因此失败 usage 必须保持 unknown。

## 决策

1. kernel 的统一 Model Call Executor 是应用传输 retry 的唯一 owner；OpenAI SDK 固定
   `max_retries=0`，Adapter 只正规化事实，不执行策略。
2. 既有 `model.started/ended` 表示一次 Logical Call；每个实际请求使用
   `model_attempt.started/ended` 子 span，决定与等待走同一 AgentEvent 脊柱。
3. 默认总计最多 3 次 attempt、90 秒 logical deadline、30 秒累计等待；本地退避从 0.5 秒指数增长到
   8 秒并使用注入的 seeded jitter。所有值可由 `model-config.v1` 的 `[retry]` 严格配置。
4. 只有标为 retryable 的 conflict、rate limit、timeout、connection 和 server error 可进入策略；鉴权、
   权限、坏请求、not-found、永久额度和 unknown 默认停止。
5. `Retry-After` 支持非负秒数和 HTTP-date。有效服务端最小等待不被本地上限截短；超出 deadline 或累计
   等待预算时直接停止。非法值只记录有限事实并退回本地退避，不保留原始 header。
6. 任意 TextDelta、任意已见上游 chunk 或显式 replay-unsafe 事实都会禁止自动重放。取消在请求和等待中
   原样传播，并闭合正在进行的 attempt、wait 与 logical span。
7. 只有 logical `model.ended` 的最终成功 usage 参与 Trace 总计；失败 attempt 的缺失用量标为 unknown。
8. `model-cassette.v4` 记录同一 request key 的 typed failure/success 序列，使固定策略、时钟和随机源能够
   离线重演决定；现有 v3 成功录制保持只读兼容。

本决策不实现跨 Profile fallback、熔断、并发竞速、分布式限流或 unsafe replay override。业务结构修复、
工具参数修复和重新命题保留自己的次数与语义，不消耗传输 retry 的业务计数。

## 备选方案

- 保留 SDK 默认 retry：接入最少，但实际 attempt、等待、取消和费用都不可观测，且容易与应用循环相乘。
- 在每个业务槽内重试：可以局部恢复，但会复制生命周期代码，并把网络故障和模型输出不合格混成一类。
- 收到部分流后从头重试：表面成功率更高，却会把两个生成过程拼成一个结果，破坏一致性和可回放性。
- 首版同时加入 fallback：需要候选授权、跨模型能力复检和共享幂等预算，扩大了本次可验证边界。

## 后果

传输恢复现在有单一所有者、明确上限和完整事件证据；普通、流式、领域、Runner 与 Eval 消费者共用同一
入口。安全 Trace、HTTP 与诊断包能区分 logical call、attempt、retry decision 和 wait，token 不会因子
span 重复计算。代价是应用承担等待策略与 cassette 新版本，且没有服务端 usage 时仍无法给出精确费用。

后续 fallback 必须复用同一 Logical Call 总 deadline、attempt 和 replay-safety 事实，不能为每个候选重置
预算；该后续决策已由 [ADR-0016](0016-explicit-authorized-provider-fallback.md) 落实。若未来协议引入
continuation、服务端工具或其他副作用，必须先扩展 replay-safety 契约再允许重试。

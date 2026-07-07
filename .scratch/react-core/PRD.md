# PRD：Phase R1 — 最小 ReAct 核（对话 agent 托考官为工具/子代理）

Status: draft（待 slice 审定后发 issue）
Triage: ready-for-agent

## Problem Statement

学习工具现在是**线性单用途考官**：每次会话都是固定 CLI 子命令驱动的 ingest→quiz 同一条循环，没有一个
理解意图（"入库这篇然后考我薄弱点"）并据此编排考官的对话前端。且"领域无关 runtime"这一卖点目前只由
文件夹结构在宣称，**没有被第二种编排模式证明**。

## Solution

在最外层加一个**最小对话式 ReAct agent**：解析自然语意图 → 把考官（ingest / start_quiz）作为**工具 / 隔离
子代理**调用。**确定性考官内核一寸不动**（LLM 判卷、代码记账不变）。于是一个 runtime **托两种编排模式**——
意图层自由 ReAct、内核确定性 workflow——共享同一条事件脊柱、trace、记忆与 replay。兑现 ADR-0004 里
"自由 ReAct 只用于开放编排"这个一直预留、未填的槽。

## User Stories

1. 作为学习者，我想用自然语说"把这篇入库然后考我"，让系统自动编排 ingest→quiz，而不用记 CLI 子命令。
2. 作为学习者，我想让它先查我的薄弱概念记忆、优先考薄弱点（对话里就能触发，不用手动指定）。
3. 作为学习者，考完我想让它一句话总结这轮暴露了哪些新薄弱点。
4. 作为工程师，我要整条 ReAct 轨迹**全程上同一条事件脊柱**（agent-turn / tool_call / subagent / generation span 成树）。
5. 作为工程师，我要整条多步轨迹**零 token 可 replay**（tool 选择是被录下的 LLM 输出，走同一 ReplayProvider）。
6. 作为工程师，我要考官作为**隔离子代理**运行——它内部几十个 span 照进 trace，但**不进 ReAct 的上下文窗口**（只回结构化结果）。
7. 作为工程师，工具边界要能挂 `before_tool` 拦截（注入防护 / 审批）——M4 的头号真客户终于出现。
8. 作为工程师，工具报错要走 M6 `RecoveryPolicy` 统一裁决（工具失败 → 降级 / 冒泡），不在循环里手写 try/except。
9. 作为工程师，多步循环的上下文要有 M5 ContextBuilder 兜底（分区 + token 预算 + 丢弃工具中间过程），不膨胀。
10. 作为面试者，我要能对着代码讲"一个领域无关 runtime 如何托两种编排、且边界为何守得住 replay/eval"。

## Implementation Decisions

- **runner 增 tool-calling 循环**：LLM →（tool_call）→ 执行工具 → 结果回灌 → 循环至 final。**自由 ReAct 仅限此处**（ADR-0004 的开放编排槽）；考官内部仍是确定性状态机。
- **`Completion` / `Provider` 扩展带 tool_calls**（function calling）：tool 选择是一次经 ReplayProvider 的 LLM 输出，`replay_key = hash(messages)+role+model` 已覆盖 → **循环可 replay**；golden cassette 变成**逐轨迹**（更长的录制）。
- **tool 注册表（`kernel/tools.py`）**：name + pydantic 入参 schema + handler；结构化入参校验失败 → ModelRetry（复用缝 3 契约）。
- **考官作子代理（`kernel/subagent.py`，销台账 #4）**：抽通用"隔离上下文 + 结构化输出契约 + 有界重试"执行器（提取 Reader 的模式泛化）；`ingest` / `start_quiz` 包成工具、内部把确定性考官作为**隔离子代理**跑；考官 span 子树**嵌在 tool_call span 之下、但与 ReAct 上下文隔离**（只回结构化结果）。
- **新 span 类型上脊柱**：`AGENT_TURN`（ReAct 循环根）/ `TOOL_CALL`（before/after，args/result/error）/ `SUBAGENT`；generation 复用现有 MODEL span。工具交互按 **OTel 形状**表达（消息 role + parts：`tool_call` / `tool_call_response`）。
- **接住已建的加硬层**：M4 `before_tool` interceptor 挂工具边界（注入 / 审批）；M6 `RecoveryPolicy` 分类工具报错；trace 脊柱原样承载。
- **M5 ContextBuilder 共建**：分区（system / persona / memory / knowledge / history）+ 每区 token 预算 + **丢弃工具调用中间过程（只留最终结果进 ReAct 上下文）** + Learning/Preference 记忆注入。
- **OTLP 导出 = 一个 Processor**（Tier C）：`AgentEvent → gen_ai.*` 只在**导出边界**映射，kernel 自己的事件 schema 是唯一真相（OTel GenAI 约定尚 development 级，别写死进 kernel）。R1 内只留 Processor 缝 / 薄 stub，导出器本体属 Tier C。
- **确定性**：tool 选择 / 入参走注入 Provider；Clock / RNG 注入；**整条轨迹 record/replay**。
- **分层守卫**：`kernel/tools.py`、`kernel/subagent.py` 零 import domain（import-linter 门）；考官工具在 domain / 组装点注册。

## Testing Decisions

- **缝 1（事件/trace 轨迹，主缝）**：脚本化 / 回放 provider 驱动 ReAct 循环，断言发射的 AgentEvent 轨迹（agent-turn / tool_call / subagent / generation span 成树、参数/结果/错误、隔离边界）。
- **缝 2（确定性核心单元）**：tool 注册表分发、ContextBuilder 分区装配 + 预算裁剪、tool_call 解析——纯函数 TDD。
- **缝 3（结构化契约）**：畸形 tool_call 入参 → ModelRetry；子代理输出 schema 校验失败 → 有界重试。
- **LLM 的 tool 选择不 unit-TDD**：录**逐轨迹 golden cassette**、CI 零 token 重放（轨迹级 replay 是 R2 轨迹 eval 的地基）。
- **不变量测试**：ReAct 绝不触判卷/记账（考官内核字节级不受 ReAct 影响）；子代理上下文隔离（ReAct 上下文不含考官内部 span）。
- 先例：`assess_once` 事件流测试、Reader 子代理结构化输出测试、Record/Replay 测试。

## Out of Scope（R1 不做）

- 轨迹 eval + 迭代 CI gate → **R2**。
- Tier-2 LLM-judge + 指标驱动自进化优化 → **R3**。
- OTLP exporter 本体 / Phoenix·Langfuse 接入 → Tier C（R1 只留 Processor 缝）。
- 审批门 suspend/resume（#3）、Responder suspend/resume（#6）。
- 难度偏好推断器（有了 ReAct 行为信号后再做）。
- FastAPI / SSE 网络投影。

## Further Notes

延伸 ADR-0004 预留的"自由 ReAct 仅开放编排"槽；确定性考官内核在子代理里原样运行。replay 干净延伸——
tool 选择即被录的 completion。基线：main `ae88da9`（阶段二收口后）。参考形状：openai-agents SDK 的
span/tool-loop、OTel GenAI 语义约定（role+parts、gen_ai.*）——照形状写在自有脊柱上，不照搬（ADR-0001）。

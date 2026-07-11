# PRD：Context Compression（工作记忆的预算 + 压缩）

Status: in-progress（协作学习式开发——我搭骨架+讲原理+守门，逐增量推进；2026-07-10 起）
Triage: ready-for-agent

## Problem Statement

react 多回合会话里，工作记忆（ContextBuilder 每轮装配的 messages）只增不减：history 逐轮累积、
学情注入 + 库存 catalog 随薄弱点/资源增长。当前 `ContextBuilder` 有 `Partition.budget` +
`CompressionPolicy` 接缝但**未实现**（恒等透传），history 更是 `build()` 里直接 `extend`、**根本不过
压缩 seam**。长会话终将撑爆上下文窗口（旧仓库已知坑）。

## Solution

给工作记忆装配加**预算 + 压缩**（轴 2；**不做**轴 1 相关性检索——N=1 规模下 YAGNI，留缝后做）：
token 估算 → 分区预算裁剪（大声失败）→ 历史压缩（滑动窗口 + 老轮摘要）。全程确定性（token 估算纯
函数、压缩决策代码定、摘要 LLM 走 Record/Replay）——replay 对得齐。

## 已定分叉（对话中拍板）

- **token 计数**：CJK 感知**确定性启发式**（wide 字符 ~1 token/字、其余 ~4 字符/token），**不用 tiktoken**
  （deepseek/qwen 非 OpenAI、编码对不齐；启发式 provider 无关、零依赖、天然确定，预算够用）。
- **预算超限**：**抛 `ContextBudgetExceeded`**（大声失败，呼应 `MaxIterationsExceeded`），不静默截断。
- **历史压缩**：先滑动窗口（最近 3–5 轮原样），再老轮摘要（轻量 LLM 槽，代码决定摘哪几轮、LLM 只产
  摘要文本，走 Record/Replay）。
- **相关性检索（轴 1）**：out-of-scope（N=1 无痛点）；接缝留好，规模到了再做（可作学习项）。

## 增量（tracer-bullet）

- **C1 TokenCounter**：注入式抽象（`Protocol`）+ `HeuristicTokenCounter`（CJK 感知、确定性纯函数）。
  落 `kernel/context.py`。TDD。无消费者也是可独立单测的确定性单元。
- **C2 分区预算 + 大声失败**：实现 `CompressionPolicy` 按 `Partition.budget` 裁分区内容（截断/丢），
  无法满足 → 抛 `ContextBudgetExceeded`。`ContextBuilder` 注入 counter + policy。装配点接上。
- **C3 历史压缩**〔核心 + seam 缺口〕：扩 seam 让 history 可被压缩（history-as-partition / policy 加
  `compress_history` / 总预算分配器——建时定）。滑动窗口（recent N 原样）先行；老轮摘要引入 summarize
  LLM 槽（Record/Replay + cassette），代码决定摘哪几轮。
- **C4（延后/可选，轴 1）**：记忆相关性检索——规模到了再做。

## Testing Decisions

- 确定性核心（token 估算 / 预算裁剪决策 / 滑窗）走 TDD（红-绿-重构），是 replay 命门。
- 摘要 LLM 槽不 unit-TDD：走 Record/Replay + cassette（我用真 key 录，同 GKB-S3 流程）。
- 不变量：无预算/无 policy 时 `build()` 逐字节等价现状（向后兼容，现有 react/eval/cassette 不动）。

## Out of Scope

- 轴 1 相关性检索（N=1 YAGNI，留缝）。
- 真 tiktoken / provider 精确计费（预算用启发式够）。
- 跨会话的记忆整合/遗忘策略（另议）。

## Further Notes

OSS 对照（learns-by-imitation）：滑窗+老轮摘要 = LangChain `ConversationSummaryBufferMemory`；
分层记忆+递归摘要 = MemGPT/Letta。基线 main `a505a8b`。协作模式：我搭骨架+讲原理+对抗守门，逐增量。

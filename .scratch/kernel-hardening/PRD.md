# PRD：宽口径 kernel 加硬（Kernel Hardening —— 补搭建顺序 M4/5/6）

Status: done（M6 RecoveryPolicy `5f1bbf6`、M4 HookManager `1c2b29a`、M5 ContextBuilder `d2ded87` 已落地）
Triage: ready-for-human（仅归档复核；无待实现 issue）

## Problem Statement

自评：搭建顺序呈 U 形——两端（trace/replay/eval/SQLite）实、腰部（step 4/5/6）塌陷。kernel 规划 10 项能力只落
events+trace(+db/clock/report)，tools/context/memory/recovery/subagent/approval 六项 kernel 侧为空、hooks 只有 observer 半边，
runner 仍是 M1 无工具裸循环。三块招牌里"可恢复"几乎不存在。整条考核 workflow 活在 domain/learning，
**kernel 当前托管的编排模式数 = 0**。"领域无关 runtime"只由文件夹结构在宣称，未被证明。

## Solution

补齐 M4/M5/M6，把 workflow 的已验证机件上提到 kernel，让 runtime 真·托管住 **workflow 这一种模式**。
这三步既是"当前版块的欠账"，又恰好是未来 ReAct loop 的前置跑道——顺序与项目自己的搭建顺序对齐。
**这三步都改 `runner` → 必须串行**（不能并行 worktree，会在 runner 撞合并；每步先 ff-merge 当前 main）。

## 建议顺序与依赖（M6 → M4 → M5）

1. **M6 RecoveryPolicy + ErrorClass**（先，独立见效快）：错误分类枚举 → 按类型统一裁决重试/跳过/挂起；
   销 skeleton-ledger `#7`（CLI `run_quiz` 的 per-round try/except 兜底）；收编 domain 散落的冒泡；错误已是 AgentEvent 进 trace，此步集中裁决。ErrorClass 供 M4/M5 错误路径复用。
2. **M4 HookManager（interceptor 半边）**（中）：`before_*` 可改参/可阻断语义 + 异常隔离；把**已有的**审批门 + 注入防护挂上 `before_*`（当前真实客户，非仅为 ReAct）。observer 半边已在 EventSink，此步补齐 hook 两类语义。为将来 ReAct 的 `before_tool` 立好挂点。
3. **M5 ContextBuilder + 跨轮裁剪硬化**（后）：分区（system/persona/memory/knowledge/history）装配 + 每区 token 预算 + 工具结果截断/渐进披露。**消费 Preference + Learning 双记忆**（依赖窄口径 02 已落地）。当前 runner 的平凡裁剪之所以平凡是因 M1 无工具；这是扩 ReAct 前的硬前置（否则 ReAct 多步累积 tool 中间过程必然 token 爆炸）。

## Out of Scope（明确延后，非遗漏）

- **凭 token suspend/resume**（审批门 `#3` / Responder `#6`）：随 interfaces/api 那一程；M6 先补"策略"，恢复语义那半留后。
- **kernel/subagent.py 提取**（`#4`）：YAGNI，等出现第二个 subagent；ledger 显式标"刻意延后"。
- **难度偏好 + 偏好推断器（confidence 累积）**：钉到 **ReAct 阶段**——需自由对话/开放答题的行为信号才谈得上推断。
- **kernel/tools.py + tool-call 循环 + 自由 ReAct loop**：**下一大阶段**（M4/5/6 是其前置）。现在扩 = 从零建第二套工具系统，"一条 runtime 托两种模式"故事会崩（两个 domain 各自烘焙的编排 ≠ 一个 runtime 托两种模式）。

## 可选备选（性价比排序，视精力插入）

- 补 1 条 Tier-2 LLM-judge eval（哪怕只判"出题语义质量/近重复"），把"规则断言 + LLM judge"从半兑现变闭环。
- golden cassette +1~2 场景（追问轮 / 开放题判卷），缓解只有 2 条对 prompt 漂移脆。
- 真实 fetch source（httpx + 超时 + 域名限制）挂现有守卫后，使 ingest 真端到端、"注入防护含超时"在代码成立。

## Further Notes

自评基线见对话（2026-07-07 全仓审视 + 双评审收敛于 B+）。启动前 ff-merge 当时 main。

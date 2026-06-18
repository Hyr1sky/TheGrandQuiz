# M1 — 事件脊柱 + 最小 runner + CLI

Status: done（M1 实现完成，CI 全绿）
Type: HITL（事件信封 taxonomy 是全系统契约，先做设计评审）

## Parent

[PRD: 考核竖切 MVP](../PRD.md)

## What to build

事件脊柱的最小端到端穿透——repo 第一条可跑链路。定义 `AgentEvent` 信封（`type` 字符串 + 元数据 + 不透明 payload）作为全系统数据契约；runner 在 turn / model-call 生命周期节点发射 AgentEvent（无工具循环，自 scholarmate 移植 + 事件化改造）；`DemoEchoProvider`（不接真 LLM）+ Provider 抽象（为后续 `basic`/`enrich` 命名角色预留形状）；CLI REPL 订阅事件流并呈现；Clock 抽象 + 种子化 RNG 走注入。

端到端行为：CLI 输入 → runner（发射 AgentEvent）→ DemoEcho → 回应，事件流在 CLI 可见。

**为何 HITL**：AgentEvent 信封 taxonomy（有哪些生命周期事件、字段形状、kernel 如何泛型分发不透明 payload）是 trace / hook / replay / eval 全部依赖的契约——agent 动手前先与人评审信封形状与生命周期事件集。遵循 ADR-0004（runner 是 workflow 骨架，自由 ReAct 仅用于开放编排）。

## Acceptance criteria

- [x] `AgentEvent` 信封类型定义（type / ts / trace_id / span_id / parent_span / payload），kernel 不认识具体 payload 类型
- [x] runner 在 turn 起止、model 调用前后发射结构化 AgentEvent
- [x] `DemoEchoProvider` + Provider 抽象（命名角色 basic/enrich 形状预留）
- [x] CLI REPL 能与 echo agent 多轮对话
- [x] 事件流在 CLI 可见（debug 呈现）
- [x] Clock 抽象 + 种子化 RNG 注入（无直接 wall-clock / 全局 random）
- [x] 确定性核心走 TDD：事件信封、runner 状态转移有单元测试（缝 2）
- [x] CI 全绿（ruff / format / pyright strict / pytest）

## Blocked by

None - can start immediately

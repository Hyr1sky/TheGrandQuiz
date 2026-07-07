# Processor 管线 + 异常隔离（kernel）

Status: ready-for-agent
Type: AFK

> 事件脊柱的订阅者形式化：把可观测能力做成"加一个 processor"，并闭掉 EventSink 不隔离订阅者异常的
> 已知坑（当初 Rich markup 崩的根因）。为后续 OTLP 导出留口。与 issue 03 可并行（文件不重叠）。

## Parent

[PRD: 让 runtime 可见（Runtime Visibility）](../PRD.md)

## What to build

把 `EventSink` 的订阅者形式化为一个 **processor 协议**（消费 `AgentEvent`；span 生命周期
`on_span_start` / `on_span_end` 可由 `*.started`/`*.ended` 事件对派生或显式暴露），并让扇出**异常隔离**：
`EventSink.publish` 把每个 processor 的调用包进隔离边界——一个 processor 抛异常被**捕获 + 记录、不冒泡、
不中断其它 processor 的扇出与本轮 / 本次 run**。现有三个订阅者（`TraceStore.record` 持久化、
`QuizEventPrinter` Rich 投影、eval 的事件收集）改造为 processor，行为不变。管线可注册任意多 processor，
为 Tier C 的 OTLP processor 留口。

**只做 observer 侧异常隔离**，不做 M4 HookManager 的 interceptor 语义（`before_*` 改参 / 阻断，Tier B）。
processor 管线住 kernel、只认 `AgentEvent` 信封 + `Span`，不认识任何领域类型（领域无关）。

## Acceptance criteria

- [ ] `EventSink` 订阅者形式化为 processor 协议（消费 `AgentEvent` / span 生命周期）；可注册任意多 processor
- [ ] 每个 processor 调用异常隔离：一个 processor 抛异常被捕获 + 记录、不冒泡、不中断其它 processor 的扇出与本轮
- [ ] 现有三订阅者（TraceStore 持久化 / QuizEventPrinter 投影 / eval 事件收集）改造为 processor，行为不变
- [ ] 缝-3：注册一个**故意抛异常**的假 processor + 一个正常 processor → 断言正常 processor 仍收到全部事件、本轮 / run 正常完成、异常被记录而非冒泡
- [ ] processor 管线住 kernel、只认 `AgentEvent` + `Span`、不 import domain
- [ ] 既有 10 个 eval 用例 + golden cassette 回放**字节级全绿**（发射时序 / payload 未变）
- [ ] 四门全绿（ruff check / ruff format --check / pyright / pytest）

## Blocked by

None - can start immediately（与 issue 03 可并行，文件不重叠）

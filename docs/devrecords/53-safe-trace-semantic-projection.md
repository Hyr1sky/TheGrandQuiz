# 安全 Trace Semantic Projection V1

日期：2026-08-31

## 为什么先建立公开投影

Observatory 过去直接把内部 Trace 归并成通用 span 列表。这个接口虽然没有原样返回 payload，但浏览器仍要理解
内部 span 命名，并且 REST 与 SSE 各自承担了一部分映射责任。选择题质量门开始产生稳定的 `stage / reason_code /
attempt` 后，继续加字段会让前端逐渐变成第二个事件解释器，也会扩大 prompt、答案、Evidence、异常正文等内部
数据误入浏览器的风险。

本竖切把 Observatory 迁移到唯一的 `project_trace(events, *, trace_id)` 入口。TraceStore 继续保存完整
`AgentEvent`，CLI 审计与 Tier-1 Eval 继续读取 raw trace；浏览器只消费新构造的白名单对象。因此这不是第二条
事件流，也没有修改持久化 schema。

## 公开契约

`SafeTraceRunV1` 固定 `schema_version=1`，包含 trace identity、有限运行状态、起止时间、workflow kind、汇总和
语义事件。当前有限 vocabulary 是：

- status：`idle / running / waiting_input / completed / failed / cancelled`；空但已注册的 trace 明确为 `idle`；
- operation：`assessment_run / multiple_choice_generation / distractor_judgement / grading / learning_commit / other`；
- phase：`started / attempt_rejected / ended / waiting_input / event`；
- stage、reason、event status 与 quality label 也全部由 OpenAPI 枚举约束，未知 stage/reason 只能降级为 `other`；
- 事件保留安全运行元数据：`sequence / timestamp / span_id / parent_span_id / attempt / tokens / latency_ms`。

未知内部事件不会被省略，而是保留 sequence 并投影为完全脱敏的 `operation=other`。这让 SSE cursor 与 raw trace
cursor 始终一致，同时不会暴露内部 `AgentEvent.type`。REST snapshot 与 SSE 增量订阅都调用同一 projector；
服务重启后，新的 `TraceObservatory` 仅凭原 TraceStore 即可重建等价语义。

运行状态改为按事件顺序归约，而不是倒序寻找旧终态。`question_asked` 或 `approval.requested` 后追加审计事件不会
冲掉等待态；新的 `*.started` 可以从旧终态重新进入 running；`answer_judged` 表示系统正在处理，不伪装成等待用户。
显式 `status=completed` 后的审计尾事件也保持 terminal。

## 数值与隐私语义

token 聚合区分两种事实：没有任何模型调用时是确定的 `0`；已经发生模型调用但 ended usage 缺失或无效时是
`null`。单个事件没有可配对 started span 时，latency 同样保持 `null`，不补零。started/ended span identity 和
parent hierarchy 由回归测试直接保护；Voice 生命周期测试继续验证终态 span 全部闭合、等待态只允许精确一个
活动 span。

投影器从字段 allowlist 新建 Pydantic 对象，从不复制 raw payload。安全测试把下列 sentinel 放入已知和未知
事件，再断言任何 REST/SSE/序列化 JSON 都找不到它们：prompt、completion、用户 answer、Evidence、URL、文件名、
异常正文、API key、Provider output、内部 event type 与未知 reason 原串。

## 当前刻意未覆盖

- `quality_label` 已被有限 schema 预留，但当前 judge 只有 Provider 输出，没有独立的安全结构化领域事件；为避免
  解析或泄漏 Provider 文案，V1 始终返回 `null`。需要真实消费者时应先让领域事件显式携带公开 label；
- assessment 之外尚未建立稳定语义映射的 Chat、Acquisition、Voice 与 hook 事件统一为 `other`，但运行状态、
  sequence、token 和 latency 仍可安全聚合；
- headline、recommended action、历史/筛选属于 FIE-03；失败卡直达属于 FIE-02；诊断包和数据目录属于 FIE-04；
  workflow descriptor 与图属于 FIE-05。本次没有提前实现这些能力。

## 同步修正的文档漂移

`CONTEXT.md`、architecture 与 ADR-0008 现在统一声明：生产 KnowledgeRelation 仍受 Prototype/Eval gate 阻挡；
首批实验 vocabulary 是 `prerequisite_of / contrasts_with / implements / failure_mode_of / tradeoff_with`。旧
`prerequisite / related / contradicts` 只保留为 ADR 的历史决策上下文，不再被描述成当前生产契约。

## 验证证据

- Python：`1157 passed`；Ruff lint/format、Pyright strict、import-linter 全部通过；
- Eval：离线 Tier-1 harness `17/17` 通过，无真实 LLM 调用；
- Web：Vitest `78 passed`，Playwright desktop/mobile `23 passed, 1 skipped`，ESLint、TypeScript、production build
  与 OpenAPI 生成通过；Sites adapter `4 passed`；
- Package：sdist/wheel 构建成功，wheel 内含 Eval 资产与本次同步后的 Web 静态资源；
- 浏览器真机 fixture：空 trace 显示 `idle` 和零值；发起考核后显示 `waiting_input`、5 个语义事件、1 次模型调用、
  225 tokens；控制台无 warning/error；
- 双轴 code review：修复顺序状态聚合、权威文档漂移、Voice span 配对断言和事件元数据可读性后复核。

这些结果证明 V1 公开边界、恢复性和既有回归门保持成立，不代表 FIE-02 至 FIE-05 已完成，也不构成复合考核或
生产 KnowledgeRelation 的进入证据。

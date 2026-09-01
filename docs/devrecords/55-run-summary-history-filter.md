# 运行摘要、历史与状态筛选

日期：2026-09-01

## 用户行为

Observatory 现在在运行状态下方直接展示结构化摘要和建议动作。选择题生成耗尽时，用户可看到总尝试次数及按
公开 reason code 聚合的拒绝计数，例如“选择题生成失败：3 次尝试；干扰项质量不足 2 次”，不再需要自行阅读
事件表完成计数。

Drawer 同时提供最近 20 次有事件的运行。用户可按 running / waiting_input / completed / failed / cancelled
状态筛选，并从列表打开精确历史 trace；状态筛选通过 REST query 在服务端执行。刷新页面、应用重新创建
Observatory 后，历史仍从 TraceStore 重建，不依赖 React 内存或“最新运行”猜测。

## 摘要与安全边界

`project_trace` 仍是唯一语义投影入口。`headline` 和 `recommended_action` 只读取已经 allowlist 的 operation、
stage、reason_code、attempt、status 与 error count；内部 exception、Provider 文案和任意 raw payload 不参与
文案生成。reason label 是有限映射，未知值先由 FIE-01 归一成 `other`。

选择题生成、判卷、等待输入、失败、取消、完成、运行中分别有稳定摘要；idle 保留原有 `headline=null` 契约。
诊断事件中的敏感 sentinel 测试继续证明自然语言异常不会进入 JSON 或摘要。

## 历史查询契约

kernel `TraceStore.recent_trace_ids(limit=...)` 只负责按 `MAX(ts) DESC, trace_id DESC` 返回确定排序 identity，
不解释领域状态。interface 层 `TraceObservatory.list_runs` 对每个 identity 调用共享 projector，再应用有限状态
筛选并在达到 limit 时停止；REST 默认 limit 20、最小 1、最大 50，非法状态或超界 limit 返回 422。

这样 TraceStore 保持领域无关，detail、SSE 与 list 继续共享一个安全事实投影。关闭并重开 SQLite 后，
completed / waiting_input / failed fixture 的排序和筛选结果保持一致。

## 前端状态与可访问性

Drawer 为历史列表分别维护带 filter identity 的 result/error，筛选切换期间不展示上一个筛选结果。列表有
loading、empty、error 三种诚实状态；历史按钮使用完整 trace id 和状态作为可访问名称，App 是选择 identity
的唯一 owner。

缺失 token 或 latency 显示“未知”，真实 0 仍显示 `0` / `0 ms`。摘要在指标和历史之前，事件表继续保留为
完整文本视图。桌面与 390×844 截图显示摘要、六项指标、筛选与可滚动历史均可读；筛选控件只有一个
`aria-label`，移动端不重复占据标题空间。

双轴审查进一步收紧了诚实语义：只有出现 `multiple_choice_generation` 才称“选择题”；没有公开 attempt 时
省略次数而不是显示 0；建议动作严格对应现有恢复命令——生成降级只建议重试/跳过，判卷降级只建议跳过。
状态筛选以 50 个 identity 为一页扫描，避免一次 `fetchall` 整个 trace 历史；公共 list seam 也独立拒绝无效
limit，而不是只依赖 FastAPI query gate。

后续复核又把题型、attempt 与 reason 限定到最近一次 `waiting_input` 之后的当前出题 slice，并让终态、运行态和
当前等待态优先于历史降级事件。这样 mixed plan 不会把上一道选择题的失败带到开放题，恢复成功后也不会继续
显示已经过期的失败摘要。

## 当前刻意未覆盖

- 不导出诊断包，不展示数据目录；这些属于 FIE-04；
- 不增加 workflow descriptor、node id 或图视图；这些属于 FIE-05；
- 不修改出题、判卷、Learning Memory、Difficulty 或 trace schema；
- 不新增 KnowledgeRelation、AssessmentMode migration，也不进入复合考核 Prototype。

## 验证证据

- Python：`1174 passed`；Ruff lint/format、Pyright strict、import-linter 全部通过；
- Web：Vitest `85 passed`，ESLint、TypeScript、OpenAPI 漂移检查、production/package build 与 Sites adapter
  `4 passed`；
- E2E：新增 generation degraded 摘要与页面刷新后历史 exact trace 路径；完整 Playwright desktop/mobile
  `29 passed, 1 skipped`；最终摘要语义修正后，受影响的 generation degraded desktop/mobile 场景再次
  `2 passed`；
- 浏览器：应用内浏览器因产品策略拒绝 loopback（`ERR_BLOCKED_BY_CLIENT`），按 testing skill 降级使用仓库
  Playwright；桌面与移动截图人工核验通过；
- 双轴 code review：发现并修复恢复动作越权、非 MC/unknown attempt 失真、摘要分支测试不足、状态筛选扫描
  无界及历史三态测试缺口；复核前所有 Standards/Spec finding 已关闭；
- 全部 fixture 离线确定执行，无真实 LLM 调用。

# 真实 Assessment Workflow 运行图

日期：2026-09-01

## 用户行为

Observatory 现在为 Assessment trace 展示领域 workflow 的真实节点、允许边和当前一题的运行状态。用户能直接
看到选择知识点、生成题目、校验证据、等待作答、判卷和提交学习事实；选择题按真实 descriptor 额外显示可选的
干扰项评审节点及校验证据到等待作答的 bypass，开放题不伪造该节点。

每个节点同时给出 completed / running / waiting / failed / pending 的文字状态。只有可证明的尝试次数和耗时才
展示数值；缺失值显示“未知”，真实 `0 ms` 保留。语义事件表、运行摘要、历史筛选与脱敏诊断包继续作为并列的
文本视图存在。

## Domain descriptor 与事件脊柱

`domain.learning.assessment.workflow` 拥有冻结的 `AssessmentWorkflowDescriptor`、有限 `WorkflowNodeId` 以及
open / multiple-choice 两个 descriptor factory。node/edge/descriptor 全部 frozen，集合使用 tuple，调用方无法
通过修改一次返回值污染后续运行。

现有 assessment、出题、干扰项 judge、判卷和学习事实事件 additive 携带有限 `node_id`，没有增加第二条回调或
状态流。安全 projector 同时按 event type 和当前 descriptor 做双重 allowlist；未知事件即使伪造一个合法
`node_id` 也不能给图着色。

解析 `web.assessment_run.*` 与重启后的 trace 属于 interface 适配责任。最终 seam 是
`resolve_assessment_workflow_descriptor(events)`：它先按最新 `assessment.started` 切出当前一题，再选择
descriptor；domain 不反向认识 Web 事件名。同一 trace 先做选择题、再做开放题时，历史 judge/grade/commit
不会覆盖当前等待节点，刷新或重启后结果等价。

## 尝试次数、耗时与失败归属

选择题的结构化 `attempt/attempts` 只归入生成题目节点；干扰项 judge 和判卷分别按真实 model started spans
计数，不把一次 generation 冒充成一次 judge。节点耗时只累计同节点非重叠 leaf spans：外层 generation 4 秒、
内层 model 2 秒时显示 2 秒，不重复相加；跨节点结束的外层 span 也不会冒充 Evidence 校验耗时。

`QuestionError` 现在携带领域 owner 决定的失败 `node_id`。普通输出校验耗尽落在校验证据，干扰项质量耗尽落在
评审干扰项；Web degraded projection 不再覆盖成第二个错误节点。无法可靠拆分的节点耗时和尝试保持 `null`。

## 前端与可访问性

Drawer 只消费 `SafeWorkflowRunV1`。节点是带明确可访问名称的有序列表，例如“判卷，失败”“评审干扰项，
未经过，可选”；颜色和圆点只是辅助。`workflow.edges` 被逐条渲染为“允许路径”列表，包含“可选分支”文本，
因此 optional bypass 不依赖动画、颜色或数组邻接猜测。

Vitest 分别覆盖 MC failed graph、open graph、unknown/zero、真实 edge 与语义事件表保留。Playwright 在 desktop
和 mobile 上覆盖 completed、generation degraded、grading degraded 三类节点状态，并随完整应用回归运行。

## 审查收敛

双轴 code review 首轮发现 mixed multi-round 聚合、domain 识别 Web 事件、前端未消费 edges、干扰项失败归属、
共享可变 descriptor、嵌套 span 双计、未知事件伪造 node、generation/judge attempts 混淆等缺口。全部修复并
增加定向回归后，Standards 与 Spec 复核均为 no findings。原 issue 也记录了 interface resolver/domain factory
的 Contract Clarification，避免文档按旧 seam 再次漂移。

## 当前刻意未覆盖

- 不改变 `assess_once` 控制流、prompt、判卷、Learning Memory、Difficulty 或 recovery policy；
- 不为 composite / exploratory / chaos 提前增加节点；
- 不新增 KnowledgeRelation、图数据库、migration 或通用 workflow runtime；
- 不把 raw payload、prompt、answer、Evidence、异常正文或任意内部事件名暴露给浏览器。

## 验证证据

- Python：`1189 passed`；Ruff lint/format、Pyright strict、import-linter 全部通过；
- Web：Vitest `86 passed`，ESLint、TypeScript、OpenAPI 漂移检查、production/package build 与 Sites adapter
  `4 passed`；
- E2E：完整 Playwright desktop/mobile `29 passed, 1 skipped`；审查修正后的 completed、generation degraded、
  grading degraded 定向场景 `6 passed`；
- 应用内浏览器因产品策略拒绝 loopback，按 testing skill 使用仓库 Playwright 完成真实浏览器和响应式验收；
- 双轴 code review 最终 Standards/Spec 均无 remaining finding；
- 全部 fixture 离线确定执行，无真实 LLM 调用。

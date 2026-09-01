# 失败卡直达精确 Trace

日期：2026-09-01

## 用户行为

Assessment 的题目生成降级、判卷降级和 fatal failure 卡现在都提供“查看本次运行”。按钮不查询运行列表、
不按时间猜测“最新一条”，而是把卡自身的 `trace_id` 交给 App；App 先固定这次 Observatory identity，再打开
既有 Drawer。业务性 `refused` 不是运行故障，继续只显示返回阅读，不增加误导性的 Trace 入口。

这条竖切复用 FIE-01 的 `SafeTraceRunV1`。浏览器仍只读取版本化白名单投影，未扩张 OpenAPI，也没有让 raw
payload、Prompt、答案、Evidence、异常正文或内部事件名进入前端。

## Trace identity 生命周期

App 现在显式持有 `observatoryTraceId`，不再用 `assessment?.trace_id ?? chatTraceId` 在渲染时推断 Drawer
目标：

1. Chat 创建新 session 时，以回调返回的 trace 接管默认 Observatory；
2. Assessment 首次返回及后续状态更新时，以自身 trace 接管；
3. 失败卡点击时再次用卡自身 trace 明确选择并打开 Drawer；
4. 关闭 Drawer 或结束考核返回阅读时不清空选择，因此底部入口仍打开同一次失败运行；
5. 下一次 Chat session 或 Assessment 才会显式替换该选择，不会永久钉死旧失败。

`ObservatoryDrawer` 继续用 `snapshot.trace_id === traceId` 和带 trace identity 的 error 做渲染门。新增回归测试
证明从已加载 trace 切换到新 trace 时只显示新目标的 loading；新目标失败后只显示稳定错误，不会短暂复用旧
snapshot 或回退到 Chat trace。

## 确定性故障 fixture

真实 FastAPI/SSE fixture 新增三个仅由测试提示词激活、预算固定为三次的场景：

- `生成降级`：三次出题均返回非法结构化输出，形成 `question_generation_exhausted`；
- `判卷降级`：开放题正常生成，三次判卷均返回非法输出，形成 `grading_exhausted`；
- `致命失败`：Assessment 的 enrich 调用抛出未分类异常，形成 terminal failed trace。

每个模式在预算耗尽或 fatal 抛出前自行清除，不污染后续正常 fixture。端到端测试从 Assessment POST 响应读取
真实 `trace_id`，再断言按钮触发的 Observatory GET 精确命中该路径。生成降级用例还关闭 Drawer、结束考核、
返回阅读并再次打开，证明第二次 GET 仍是同一 trace。所有未结束的降级考核都在测试尾显式取消，使全局 trace
started/ended 审计保持配对。

## 浏览器验收

在 1440×1024 桌面视口中，失败卡的重试、跳过和“查看本次运行”均有清晰可访问名称；Drawer 展示 3 次重试、
`generation / invalid_json` 以及最终 `question_generation / question_generation_exhausted`。结束考核返回阅读后，
再次打开仍可见同一原因。

在 390×844 移动视口中，Drawer 保持完整标题、关闭按钮、状态、六项指标和可滚动语义事件；底部状态栏仍可用。
两次检查均使用真实本地 API 与 SSE，浏览器控制台无 warning/error。

## 当前刻意未覆盖

- 没有实现失败摘要、运行历史、状态筛选、诊断包、数据目录或 workflow 图；它们仍分别属于 FIE-03 至
  FIE-05；
- 没有修改出题、判卷、Learning Memory、Difficulty 或 TraceStore schema；
- 没有新增生产 KnowledgeRelation、AssessmentMode 或数据库迁移，也没有开始复合考核 Prototype。

## 验证证据

- Python：`1157 passed`；Ruff lint/format、Pyright strict、import-linter 全部通过；
- Web：Vitest `81 passed`，ESLint、TypeScript、OpenAPI 漂移检查、production/package build 与 Sites adapter
  `4 passed`；
- E2E：desktop/mobile 共 `29 passed, 1 skipped`；三类失败入口在两种视口全部通过，trace invariant audit
  闭合；
- 浏览器可见验收：桌面与 390×844 移动视口通过，控制台无 warning/error；
- 双轴 code review：补齐旧失败被新 Assessment 接管的公开交互测试，把 fixture mode/attempts 收敛为有限状态
  对象，并消除重复 Trace CTA 后复核；Standards/Spec 均无未解决项或新发现；
- 全部 fixture 离线确定执行，无真实 LLM 调用。

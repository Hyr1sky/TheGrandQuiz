# 供应商失败解释与 Observatory 聚焦修正

日期：2026-09-01 至 2026-09-05

## 用户问题与根因

一次真实选择题生成在 enrich provider 返回 403 后失败。raw trace 已记录
`AllocationQuota.FreeTierOnly`，但安全 trace projector 只接受显式 `reason_code`，失败摘要又优先退化为通用
“运行失败”，因此 Observatory 和脱敏诊断包都无法告诉用户可行动的根因。前端同时把历史筛选、较长运行列表、
workflow 和事件表全部塞进 Drawer，选中历史记录会重排同一滚动容器，难以保持焦点。

## 安全失败归一化

`OpenAICompatProvider` 现在在 complete 与 stream 两个边界把 SDK 异常正规化为 provider-neutral
`ProviderFailure`：有限 category、HTTP status、内部 vendor code、retryability 与有限 public reason
code。原始 SDK 异常只作 `__cause__`；新异常的 `str/repr` 不含任意 response body。Recovery 仍把
当前 Provider 失败当作 fatal，`retryable` 只是未来策略的事实，不会触发第二层隐式重试。

`TraceReasonCode` additive 增加有限 Provider reason codes。新 trace 消费 typed facts；为兼容已持久化的
历史 trace，仍只在服务端 `model.ended` 边界识别 Dashscope 的有限稳定 quota marker。安全摘要
由 category 和 operation 生成用户指引。

raw 异常和 vendor code 仍只存在于完整 TraceStore。REST、SSE 和诊断包继续从 allowlist 对象重建，
只暴露 category、受限 status、retryability 和 public reason code；不复制异常正文或 vendor code。golden
回归证明 quota message、供应商内部 code/request id、prompt、answer、Evidence 和 key 均不进入导出结果。

独立 review 另外发现 Chat session 会跨轮复用同一 trace，旧 Provider 失败会覆盖后续成功轮的摘要。
projector 现按最新 `turn.started / agent_turn.started / assessment.started` 界定 user-visible execution
segment；历史事件仍在 trace 中，但 headline 只解释当前轮次。

Sep1 真实 trace `cedf1f668b804b04a596b221bcaf4e87` 经新 projector 重放后状态为 failed，headline 与建议
正确，公开事件只出现 `provider_quota_exhausted`。

## Provider review P2 收口

官方错误语义复核发现，code 中出现 `quota` 并不代表永久额度耗尽。DashScope 的
`AllocationQuota.FreeTierOnly` 是明确的 403 免费额度用完即停；但
`Throttling.AllocationQuota` / `insufficient_quota` 可以表示 TPS/TPM 瞬时限流。旧 classifier 的
substring 判断会把后者错误标成 `quota_exhausted/retryable=false`。

Provider 边界现在只把经过验证的 `AllocationQuota.FreeTierOnly` code 和明确的历史
`Free quota exhausted` message fallback 判成永久额度耗尽。新增红绿回归从真实 `complete()` Interface
验证 `Throttling.AllocationQuota` 投影为 `rate_limited/retryable=true`，且上游 message 不进入安全异常。

另一个 review P2 是多个模型消费者各自拼装 `model.ended(ok=False)` payload。新增 Runtime-owned
`kernel.model_events.model_failure_event_payload`，统一 `ok/error` 与 allowlisted Provider facts；Reader、
出题、判卷、干扰项评审、总结、grounded answer、Runner 和 Eval quality judge 只补各自的 `node_id` 等上下文。
普通 `ERROR` 和 `RECOVERY_DECIDED` 没有被强行改成同一 schema，事件语义保持分离。

## 跨 Trace identity 修正

Sep2 复现揭示了第二个独立问题：用户打开的是外层 Chat trace
`ef05ed7c2dce426792f037250db898e7`，而真实失败属于 Assessment trace
`ff04712e73ab458c98f3ec8ad5649645`。前者正常结束且 payload 中没有后者 identity；两者仅在时间上嵌套，不能在
并发场景中可靠推断关联。

`start_assessment` 导航工具现在预分配 `assessment_trace_id`，把它作为结构化 navigation 参数写入外层 Chat
事件；Assessment start request 使用同一 identity，并拒绝复用已存在的 trace。安全 projector 只接受
`navigation.requested + target=assessment + 32 位小写十六进制 identity`，投影为有限 `related_traces`；非法值、
未知事件和 resource 信息不会进入公开对象。Observatory 在外层运行顶部展示“本次聊天启动了考核”，可直接切换
到真实子 Trace，不再按时间猜测。

失败/降级 Assessment 卡会主动读取自己的安全 Trace snapshot，用 projector 的 headline 和操作建议替换通用
占位文案，因此 quota 失败无需先打开 Observatory 就能看到原因。既有 Sep2 子 Trace 重放为
`provider_quota_exhausted`、“选择题生成失败：模型服务免费额度已用尽”及对应补余额建议。

## 聚焦后的运行界面

Assessment 的“查看本次运行”改为正文阅读字体、透明背景和低权重边框，与主阅读区一致。允许路径容器恢复
12px padding；诊断包按钮使用 10px × 16px padding，并与 workflow 留出明确间距。

按钮内部改为 `inline-flex + align-items:center`，SVG 脱离文字 baseline。真实浏览器测量中，图标和文本中心线
差值由截图中的约 4 CSS px 降为 0.125 CSS px；390 × 844 移动视口结果一致。

Drawer 的近期运行降为最近 3 条，只提供摘要和 trace identity，不再内嵌筛选。单条运行与“查看全部运行”都是
带 `target=_blank` 的真实链接，在独立 Observatory 页面打开，不修改当前学习工作区的滚动位置。独立页复用
同一个 `ObservatoryDrawer` 内容组件和安全 API，只切换 presentation；它保留 20 条有界历史、服务端状态筛选、
详情、workflow 与事件表。桌面指标为六列，移动端为两列，底层使用真正的 `main` landmark。

## 验证证据

- Python：`1209 passed`；Tier-1 Eval `17/17`；Ruff lint/format、Pyright strict、import-linter 全部通过；
- Web：Vitest `89 passed`，ESLint、TypeScript、Sites adapter `4 passed` 和 production/package build 通过；
- E2E：完整 desktop/mobile Playwright `29 passed, 1 skipped`，包括 Drawer 到独立页和历史失败运行链接；
- 浏览器实测：默认桌面与 390 × 844 视口均无横向溢出；外层 Chat → 关联 Assessment → 失败摘要可连续导航；
- OpenAPI 和生成的 TypeScript schema 已同步新增安全 Provider facts/reason enum、`related_traces` 与
  可选预分配 `trace_id`；Provider facts 不含 vendor code；
- 全部自动化 fixture 离线确定执行，无真实 LLM 调用。

## 边界

- 不改变供应商计费或 recovery policy，不自动重试 fatal 配额错误；
- 不把任意 provider 自然语言或 vendor code 原样展示给用户；只根据有限 typed category 生成指引；
- 不为旧 Trace 做时间窗口猜测或原地回写；历史 `ef05…` 自身仍无关系 identity，真实失败需按已知子 Trace
  `ff047…` 查看，新产生的运行才具有持久化关联；
- 不改变 Assessment workflow、Learning Memory、Difficulty、prompt 或数据库；
- 不提前实现 composite/exploratory/chaos、KnowledgeRelation 或图存储。
- 不提前实现 ModelProfile、ProviderRetryPolicy、Retry-After、自动 fallback 或 Anthropic Messages
  Adapter；这些能力继续由路线图 P5 或真实恢复消费者拉动。

# 脱敏诊断包与本机数据位置

日期：2026-09-01

## 用户行为

Observatory 现在为当前 exact trace 提供“导出脱敏诊断包”入口。下载是一个版本化 JSON attachment，用户可以把
它用于复现和沟通运行故障，而无需复制原始 prompt、answer、evidence、Provider 错误正文或密钥。

应用设置新增“本机数据位置”，只读显示当前进程实际使用的 `learning.db`、`trace.db` 与 `voice.db` 路径。路径
只对真实 loopback peer 返回；非 loopback 请求得到 `data_locations=null`。Settings PATCH 保持
`extra=forbid`，不存在从浏览器改写数据路径的命令。

## 诊断包契约

`DiagnosticBundleExporter` 是唯一 bundle 组装入口。它只消费 FIE-01 的 `TraceObservatory.snapshot()` 安全
投影和既有 `ProviderSettingView` allowlist，再输出：

- `schema_version=diagnostic_bundle.v1` 与 exact `trace_id`；
- 应用版本、`settings.v1` 和 provider role/configured/model/endpoint host 组成的安全配置 identity；
- `SafeTraceSummaryV1` 与 `SafeTraceEventV1[]`；
- 独立的 `manifest.created_at`。

route 不读取 `TraceStore` raw payload；不存在的 trace 继续返回稳定 `trace_not_found`。下载文件名固定为
`grandquiz-trace-diagnostic.json`，避免把不可信 trace identity 放入 response header。

## 脱敏与确定性证据

golden fixture 故意把四个不同 sentinel 放入 raw event payload 的 prompt、answer、evidence 与 api_key 字段。
exporter 单测和 REST 字节响应逐一证明它们没有跨越边界，`required_env_vars` 也不会进入诊断包配置 identity。

同一 trace 使用注入式 `ManualClock` 连续导出两次：`manifest.created_at` 分别变化，只移除这两个值后，保留
完整 manifest 的 canonical JSON bytes 逐字节相等。summary、events、provider identity 与字段顺序均不依赖
墙上时钟、随机数或 LLM。

## 路径安全与前端

settings route 只根据 ASGI 的真实 `request.client.host` 做 `ipaddress.is_loopback` 判定，不信任 Host 或
forwarded header。路径在 app composition 时解析为实际绝对路径，projection 中每项固定
`read_only=true`；remote fixture 与路径改写请求均有负向测试。

Observatory 下载链接使用当前 snapshot 的完整 trace identity 并做 URL encoding；Settings Drawer 只渲染
`code` 文本，没有 textbox、file picker 或保存路径动作。desktop/mobile Playwright 同时覆盖路径可见性和 exact
diagnostic URL，既有 failure-card、历史筛选和考核流程保持通过。

## 当前刻意未覆盖

- 不导出 zip、raw AgentEvent、prompt/answer/evidence、异常正文、环境变量值或密钥；
- 不允许任意路径读取、目录浏览、路径迁移或 UI 改写；
- 不增加 workflow descriptor、node id 或图视图；这些属于 FIE-05；
- 不新增 KnowledgeRelation、AssessmentMode migration，也不进入复合考核 Prototype。

## 验证证据

- Python：`1179 passed`；Ruff lint/format、Pyright strict、import-linter 全部通过；
- Web：Vitest `85 passed`，ESLint、TypeScript、OpenAPI 漂移检查、production/package build 与 Sites adapter
  `4 passed`；
- E2E：完整 Playwright desktop/mobile `29 passed, 1 skipped`；审查后 exact trace 下载场景再次 `2 passed`；
- 应用内浏览器仍因产品策略拒绝 loopback，按 testing skill 使用仓库 Playwright 完成真实浏览器与响应式验收；
- 双轴 code review：Standards 无 finding；Spec 发现并修复确定性未按 bytes 比较、E2E 未绑定本轮 exact
  trace 两项缺口，复核后全部关闭且无新 finding；
- 全部 fixture 离线确定执行，无真实 LLM 调用。

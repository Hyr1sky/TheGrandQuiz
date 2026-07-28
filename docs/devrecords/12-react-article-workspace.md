# React Article Workspace 开发记录

> 记录日期：2026-07-24
>
> 对应范围：LW-S2、LW-S3
>
> 目标：在既有 FastAPI/SSE 契约上交付可真实操作的 Article Workspace，同时保持文档结构、
> GroundedDocumentAnswer、事件脊柱和 loopback-only 边界。

## 1. 视觉方向与双主题

先生成三种同信息架构的一屏方案，经 HITL 选择第 3 个“墨迹星图”。选择结果固化到
`docs/design/web-visual-language.md`，暗色与亮色参考稿、最终实现稿和并排 QA 证据均落盘。

前端只维护一棵组件树，通过语义 token 切换“夜墨”和“矿物纸”主题。星图纹理使用独立 WebP
资源，功能图标统一来自 Phosphor；没有用 CSS 图形、emoji 或主题分叉冒充设计资产。

## 2. 规范化 React 工程

新增 `web/` 的 Vite + React 19 + TypeScript 工程，代码按真实职责分为：

```text
src/
├── app/       # 根装配与主题状态
├── routes/    # 页面级入口
├── features/  # Article Workspace 行为与测试
└── shared/    # OpenAPI client、主题 token、通用控件
```

FastAPI OpenAPI 是唯一 HTTP 类型源。`scripts/export_openapi.py` 在不打开生产数据库、不调用真实
provider 的情况下确定性导出契约；`openapi-typescript` 生成 schema，`openapi-fetch` 负责类型安全请求。
SSE 的 `UiEvent` 也显式进入 `text/event-stream` OpenAPI response，避免前端手写第二套 DTO。

## 3. Article Workspace tracer bullet

页面已经贯通：

- 列出并切换明确的材料 scope；
- 读取文档 outline 与单个有界 `DocumentNode`；
- 发起 GroundedDocumentAnswer 后台 run；
- 展示搜索、节点读取、citation 解析和完成阶段；
- SSE 断线后从最后 sequence 续接；
- 取消运行并保留 `trace_id`；
- 点击 citation 回到精确节点；
- 主动揭示 quote/context，默认保持证据遮罩；
- 清楚区分 no-evidence、provider failure 和 cancelled。

文章始终是主画布，问题与回答是页边批注，不复制 CLI/ReAct 编排，也不让浏览器接触 SQLite。

## 4. TDD 与真实本地 fixture

Vitest + Testing Library 按红—绿顺序覆盖 8 个行为：加载与节点阅读、主题持久化、SSE 回答与证据、
精确材料 scope、脱敏 provider failure、取消、断线续接和 no-evidence fail-safe。

`scripts/run_web_fixture.py` 使用临时 learning/trace DB、真实 FastAPI/SSE、真实文档搜索与确定性 fake
provider，供浏览器 QA 和 Playwright 主路径复用。Playwright 同一场景覆盖 1440 × 1024 桌面与
390 × 844 移动 viewport；本轮按浏览器工具约束未直接调用 standalone Playwright CLI。

## 5. 设计与浏览器 QA

真实 fixture 中人工完成了选节点、提问、SSE 完成、citation 定位、Evidence reveal 和主题切换。
第一轮发现并修复标题跨列换行与正文重复 Markdown heading；第二轮将暗/亮参考稿和实现稿放在同一张
comparison 中检查，最终无 P0/P1/P2。稳定视觉规范保留在
[Web Visual Language](../design/web-visual-language.md)，后续运行时观测收口见
[开发记录 14](14-web-runtime-observability-closeout.md)。

## 6. CI 与边界

CI 在原 Python/Eval 门之后增加：

```text
npm ci
npm run api:check
npm test
npm run typecheck
npm run build
npm run test:sites
```

Vite 和 fixture 均默认绑定 `127.0.0.1`。本轮保留 Sites-ready 构建适配器，但没有部署、没有引入账号或
公网暴露，也没有改变 `AgentEvent` 作为 trace、SSE 和 eval 共同脊柱的架构。

## 7. 已知限制与下一步

- HTTP run registry 与 SSE backlog 仍是单进程内存状态，跨进程恢复属于后续审批竖切。
- 当前只呈现 Article Workspace；考核逐题 workflow、答题 Evidence reveal 记账与学习状态轨迹属于 LW-S4。
- Web Acquisition 选择/审批、文章管理、配置页和 FastAPI 同源静态托管分别留在 LW-S5–S7。

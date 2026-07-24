# Local Web API 基座开发记录

> 记录日期：2026-07-24
> 对应范围：`.scratch/local-web/` 的规划与 LW-S1
> 目标：在不复制领域 workflow、不让浏览器直连 SQLite 的前提下，贯通第一条
> Article Workspace HTTP tracer bullet。

## 1. 先锁定产品与架构边界

本轮先用 `zoom-out`、`to-prd` 和 `to-issues` 重新审视仓库，再写代码：

- 新增 `.scratch/local-web/PRD.md` 和 LW-S1–S7 七个竖切 issue；
- 新增 ADR-0009，确认 FastAPI + React 同仓、同 `main`、local-first、REST + SSE；
- 把旧发布清单从 “CLI-only v0.1.0” 修订为包含最小 Article Workspace；
- 明确 FastAPI 是 interface adapter，领域行为仍由现有 Module 拥有；
- 明确正式 React 视觉实现前先做三种可比较方案，由用户完成一次 HITL 选择。

这一步也更新了 `CONTEXT.md`、architecture、roadmap 和 README，避免后续 Agent 继续按“Web 延后”的旧
假设工作。

## 2. HTTP 只读文章工作区

新增 `src/grandquiz/interfaces/api/`，第一阶段提供：

```text
GET  /api/v1/health
GET  /api/v1/resources
GET  /api/v1/resources/{resource_id}
GET  /api/v1/resources/{resource_id}/outline
GET  /api/v1/resources/{resource_id}/nodes/{node_id}
POST /api/v1/resources/{resource_id}/questions
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/events
POST /api/v1/runs/{run_id}/cancel
```

资源列表与详情使用显式 DTO，不返回 `raw_content` 或 content hash。大纲复用
`DocumentSearch.outline()`；节点读取复用 `DocumentSearch.read_node()`，正文上限固定在既有
1..4000 字符契约内，并继续标记为 untrusted。

为支持资源目录，Store 增加 `all_resources()` 深行为；内存和 SQLite Adapter 都按稳定
`resource_id` 排序，并增加 parity 测试。API 没有自行查询 SQLite 表。

## 3. 可观测 GroundedDocumentAnswer run

问题接口先返回：

```json
{
  "run_id": "...",
  "trace_id": "...",
  "status": "queued"
}
```

`RunManager` 由 FastAPI lifespan 持有全部后台 task；服务退出时统一取消和等待，不遗留无 owner 的
`asyncio.create_task`。每个 run 建立一个 `interface.api_run` 父 span，
`GroundedDocumentAnswer` 作为其子 span 执行，因此 search、node read、model、citation、成功/失败/取消
都在同一棵 trace 树内。

run 状态为：

```text
queued / running / needs_input / succeeded / failed / cancelled
```

LW-S1 实际使用除 `needs_input` 外的五种状态；`needs_input` 留给后续可恢复审批。no-evidence 是成功完成的
领域 fail-safe 结果，不被误报为基础设施失败。Provider 异常返回脱敏的 `run_failed`，完整诊断通过
trace_id 审计。

## 4. SSE 是安全投影，不是裸事件转发

SSE 只发送白名单 UI event：

```text
run.queued
run.started
search.completed
node.read
citation.resolved
answer.completed
run.succeeded / run.failed / run.cancelled
```

每条事件带单调 sequence，并支持 `?after=N` 从已知位置续读 backlog。投影不包含用户 query、system
prompt、完整 model messages/output 或节点正文；内部 trace 仍保留调试和评测所需的完整事件。

这项边界是在第二次 `zoom-out` 中加固的：最初 run 生命周期直接写 UI backlog，复盘后改成先发
`interface.api_run.*` AgentEvent，再由同一订阅者投影到 SSE，避免形成第二套事实源。

## 5. 生命周期与启动入口

FastAPI 官方推荐的 lifespan 同时拥有并按顺序收尾：

- `LearningPersistence`；
- `TraceStore`；
- `RunManager`；
- 生产 `OpenAICompatProvider`。

app factory 可注入临时 DB 和 fake provider，测试不读取 `.env`、生产 DB 或公网。新增
`grandquiz-web` 命令，默认只监听 `127.0.0.1:8000`；当前可访问 API 与 OpenAPI，React UI 尚未进入
LW-S3。

## 6. TDD 与验证

按公共 HTTP 行为逐个完成红 → 绿：

- health 与 app factory；
- 资源列表/详情和稳定 404 envelope；
- outline 与有界 node read；
- 202 run envelope；
- GroundedDocumentAnswer result 与精确 citation；
- 安全 SSE、backlog resume；
- 幂等取消；
- 统一 validation error；
- no-evidence / provider failure；
- 完整 trace 与父子 span；
- OpenAPI 路径和 loopback 启动入口；
- Store Adapter parity。

最终结果：

```text
ruff check .              pass
ruff format --check .     pass
pyright                   pass（0 errors / 0 warnings）
lint-imports              pass
pytest                    859 passed
```

FastAPI/uvicorn 进入生产依赖。提交前依赖审计发现 FastAPI 0.139 系列会把 TestClient 切到当天刚发布的
`httpx2` 生态；本轮以稳定性优先，暂时固定 `fastapi<0.139`，测试和既有 Web Acquisition 继续共用成熟的
`httpx`。以后升级必须先通过同一套 HTTP/SSE 与全量回归。

## 7. 明确限制与下一步

- run/result registry 当前是单进程内存状态；服务重启后可按 trace_id 审计，但不会从 trace 逆向重建
  HTTP `RunView`。
- SSE backlog 随进程生命周期存在，尚未实现 TTL/容量回收；个人本机首条竖切可接受，正式长期运行前应加
  有界保留策略。
- `needs_input` 和跨进程 suspend/resume 尚未实现；Web Acquisition 审批不得用阻塞 HTTP 冒充。
- 当前没有正式 React 页面。下一节点是 LW-S2：基于已经稳定的 Article Workspace 信息架构生成三种视觉
  方向，等待用户选择后再推进 LW-S3。

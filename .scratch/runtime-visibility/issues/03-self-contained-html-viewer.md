# 自包含 HTML 渲染器（kernel 纯函数）

Status: ready-for-agent
Type: AFK

> "show, don't tell" 的载体：把一条 trace 渲染成可点开 / 截图 / commit 的自包含 HTML。
> eval 报告与真机 trace 视图共用同一渲染器（eval 用例本身就是一条 trace）。对标 inspect_ai Inspect View。
> 与 issue 01 可并行（新增 kernel 模块，不碰 events.py）。

## Parent

[PRD: 让 runtime 可见（Runtime Visibility）](../PRD.md)

## What to build

一个 kernel **纯函数**渲染器：输入 = 一条 trace 的（有序 `AgentEvent` 列表 + `build_span_tree` 投影的
span 森林 + 汇总 token/latency 元数据），输出 = **自包含 HTML 字符串**（内联全部 CSS / JS，零外部请求 /
CDN / 运行时依赖，可离线打开）。渲染：

- **可折叠的 span 森林**（turn → model → tool → subagent；每 span 显 type / 起止 / latency / token）；
- **底层 AgentEvent 事件流**（按 `seq` 有序，含领域事件），体现"脊柱是唯一真相、树只是投影"。

设计成被 **eval 报告（逐用例 + 汇总）与真机 trace 视图共用**。住 kernel、只认 `AgentEvent` + `Span`、
不认识领域类型（领域无关）。token 成本复用 `Usage.total_tokens` computed_field。

## Acceptance criteria

- [ ] 纯函数：同一 trace 数据 → 同一 HTML（不碰时钟 / 随机 / 网络；时序来自事件 `seq`/`ts`）
- [ ] HTML **自包含**：内联 CSS / JS，零外部请求 / CDN，可离线打开
- [ ] 渲染可折叠 span 森林（turn→model→tool→subagent，每 span type / 起止 / latency / token）+ 事件流（按 seq）
- [ ] 住 kernel、领域无关：只认 `AgentEvent` + `Span`，不 import domain
- [ ] token 成本复用 `Usage.total_tokens` computed_field、真实可读
- [ ] 缝-2：固定输入（事件列表 + span 森林 + 元数据）→ 断言 HTML 的**结构内容**存在（span 类型、token 总数、事件条数、判决值等），非字节级比对
- [ ] 四门全绿

## Blocked by

None - can start immediately（与 issue 01 可并行，新增模块不碰 events.py）

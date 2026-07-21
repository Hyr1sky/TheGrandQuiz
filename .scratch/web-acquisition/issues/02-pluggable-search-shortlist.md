# WA-S2 — 可拔插搜索与候选清单

Status: done（2026-07-21；SearchProvider、SearXNG adapter、可选 web_search 与事件审计完成）
Type: AFK

## Parent

[PRD：Web Acquisition（原生 Fetch / Search + 可选 MCP Adapter）](../PRD.md)

## What to build

定义供应商无关的 `SearchProvider` / `SearchResult` 契约，并交付首个 SearXNG 直接 adapter 与
`web_search` ReAct 工具。搜索只返回有界候选，不自动抓取、Reader 或入库；用户 / agent 选择 URL 后
仍复用现有确定性 ingest workflow。SearXNG 是可选外部服务，不成为基础安装、Docker 或本地进程的
强制依赖。

覆盖 PRD User Stories：1–2、8–9、11–12、21。

## Acceptance criteria

- [x] `SearchProvider` 返回稳定的标题、URL、摘要、adapter、rank / 可选 metadata 模型
- [x] 查询、结果上限、域名约束和超时显式且有保守边界；异常映射为稳定失败分类
- [x] SearXNG adapter 只依赖配置的 endpoint，通过 JSON API 映射内部模型，不保存凭证或不稳定客户端对象
- [x] `web_search` 只返回候选，不自动 fetch / ingest，不绕过用户筛选和 KnowledgeItem 审批
- [x] 仅在配置 SearchProvider 时注册 `web_search`；默认 ToolRegistry schema 与现有 cassette 保持不变
- [x] search started / ended / failed 进入事件脊柱，记录 adapter、查询摘要、结果数和失败分类，不记录 secret
- [x] adapter contract 由可控 transport fake 验证，不要求 CI 启动 SearXNG、Docker 或访问公网
- [x] 商业搜索、GitHub Search 与 MCP 后续可按同一接口加入，无需修改 Reader / KB workflow

## Blocked by

None - can start independently; integrate with WA-S1 for the end-to-end path.

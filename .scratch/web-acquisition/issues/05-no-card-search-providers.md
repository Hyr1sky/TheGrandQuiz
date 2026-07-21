# WA-S5 — 免信用卡搜索启动路径

Status: done（2026-07-21；Tavily / SearXNG 真实候选搜索、trace 审计与容器清理均完成）
Type: HITL

## Parent

[PRD：Web Acquisition（原生 Fetch / Search + 可选 MCP Adapter）](../PRD.md)

## What to build

在既有 `SearchProvider` seam 上补齐两条普通用户可接受的启动路径：无需信用卡的 Tavily 免费 Key，以及
loopback-only 的最小单容器 SearXNG。增加不经 LLM 的直接搜索命令，用同一正式工具与事件脊柱分别验证
provider 连通、候选归一化和 trace，同时不触发 Fetch、Reader 或入库。

## Acceptance criteria

- [x] `TavilySearchProvider` 使用 Bearer key 调用官方 Search API，并映射既有 `SearchResult`
- [x] 固定使用 1-credit 的 basic search，不请求 generated answer 或 raw content
- [x] API key 不进入 URL、请求 body、SearchResult、事件、trace、cassette 或 Git
- [x] 只配置一个 provider 时自动启用；同时配置 Tavily / SearXNG 时必须显式选择
- [x] `grandquiz search` 不调用 LLM、Fetch、Reader 或 learning store，并复用正式 search 事件
- [x] 最小 SearXNG 配置只监听 `127.0.0.1`，单容器、无 Valkey、默认开放 JSON API
- [x] CI / pytest 不访问公网、不要求 Docker；两种 provider 的 HTTP contract 均由 fake transport 验证
- [x] 使用 `.env` 中真实 Tavily Key 完成一次候选搜索，保存非敏感 trace 审计结论
- [x] 启动最小 SearXNG 并完成同 query 的候选搜索，验收后停止并移除容器与临时网络
- [x] 静态四门、import-linter、全量 pytest 与离线 Eval 全绿

## Real verification

- Tavily trace：`9e06cd8df5bc40bda0dc3de8a942dfaa`，返回 5 条候选，事件审计中无 key 前缀。
- SearXNG `2026.7.19-6da6eee26` trace：`00c92ec25f484ab6b0238731a9d4d798`，返回 5 条候选，首条为 JavaGuide MySQL 面试题。
- 两条 trace 都只有 `learning.web_search.started/ended`，adapter 与结果数正确，query 只保存 hash。
- Compose 只发布 `127.0.0.1:8080`；验收后 `docker compose ps` 为空，官方 GHCR 镜像保留。

## Architecture constraints

- 搜索只发现候选，不自动 Fetch / Reader / 入库。
- Tavily 返回的 content 仅作不可信 snippet；正文仍由内部 Fetch + Trafilatura 取得。
- 不随机选择公共 SearXNG 实例，不把用户 query 静默发送给未知第三方。
- 不引入 Tavily SDK；复用现有 `httpx`，保持 adapter 小且可 Record/Replay。

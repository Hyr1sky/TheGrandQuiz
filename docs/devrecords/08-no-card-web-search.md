# 免信用卡 Web Search 开发记录

> 记录日期：2026-07-21
> 范围：WA-S5 Tavily adapter、显式 provider 选择、直接搜索 CLI 与最小本地 SearXNG。
> 当前边界：Tavily 与 SearXNG 的真实直接搜索均已完成；剩余 HITL 是 WA-S4 的真实模型 ReAct 决策与 cassette。

## 1. 产品约束

本轮把“不绑定信用卡也能使用 Web Search”提升为正式启动路径。搜索仍然只负责发现候选：不会自动抓取、调用 Reader 或写入全局 KB。用户或开放 ReAct 选择 URL 后，正文仍由内部 Fetch → Trafilatura → 质量门处理，随后才进入确定性的 Reader → 审批 → 入库 workflow。

没有配置搜索能力时，`web_search` 继续不注册；已知 URL 入库和全部离线能力不受影响。Docker、Tavily 和 SearXNG 都不是基础运行依赖。

## 2. Tavily adapter

新增 `TavilySearchProvider`，直接复用现有 `httpx`，没有引入 Tavily SDK。请求固定使用 `basic` search，显式关闭 generated answer 与 raw content，并把 limit / include_domains 传给官方 Search API。返回的 title、URL、content snippet 与 score 被映射为既有 `SearchResult`；选定 URL 后仍由内部 Fetch 重新取得并规范化正文。

API key 只从 `TAVILY_API_KEY` 读取，只进入 Bearer header。测试明确断言 key 不进入 URL 或请求 body；adapter 输出、事件与 trace 只知道公开的 adapter 名，不保存凭证。

## 3. Provider 选择

环境装配支持以下行为：

- 只配置 `TAVILY_API_KEY`：启用 Tavily。
- 只配置 `SEARXNG_URL`：启用 SearXNG。
- 两者都不配置：不注册 `web_search`。
- 两者同时配置：必须设置 `WEB_SEARCH_PROVIDER=tavily|searxng`，否则大声失败。

这里没有设置隐藏优先级，也没有自动 fallback。供应商切换、成本和失败语义保持可观察。

## 4. 直接验证命令

新增 `grandquiz search`：它不调用 LLM、Fetch、Reader 或 learning store，只复用正式 `web_search` 工具列出候选。Search started / ended 事件仍进入独立 trace DB，因此诊断命令没有绕开事件脊柱。

```bash
uv run grandquiz search "MySQL 面试高频考点"
uv run grandquiz search "MySQL interview" --domain github.com --limit 3
```

该命令适合先验证 Key、endpoint、域名过滤和搜索质量，再进入更昂贵的真实 ReAct dogfood。

## 5. 最小本地 SearXNG

`deploy/searxng/` 提供可选 Compose 配置：

- 单个 SearXNG 容器，不启动 Valkey、Caddy 或其他服务。
- 只发布 `127.0.0.1:8080`，不接受外部主机连接。
- 关闭公共实例才需要的 limiter / image proxy。
- 显式开启 JSON API。
- 默认使用官方 GHCR 镜像，可通过 `SEARXNG_IMAGE` 固定版本或切换官方 Docker Hub 镜像。

配置测试锁住 loopback、单容器、无 Valkey 与 JSON API 四个约束。Docker Compose `config` 已成功展开；官方 GHCR 镜像 digest `sha256:b8ca38ba06eea544d7555e88321e212ddc0d5c3c7de055419cfb2e5c6bf30812` 已拉取。首次 `compose up` 因桌面安全审批通道断开而被拒绝；用户重新明确授权后正常启动并完成验收，随后容器与临时网络均已移除，镜像保留。

## 6. 当前验证结果

```text
ruff check .                pass
ruff format --check .       pass（170 files）
pyright                     pass（0 errors）
lint-imports                pass（kernel layering kept）
pytest                      829 passed
python -m grandquiz.evals   16/16 passed
docker compose config       pass（loopback-only / single container）
Tavily live search          pass（5 results / trace audited）
SearXNG live search         pass（5 results / trace audited）
```

### Tavily

使用 `.env` 中的真实 Key，通过 `grandquiz search "MySQL 面试高频考点" --limit 5` 返回 5 条候选。首条是小林 Coding 的 MySQL 面试题，相关度分数约 0.823。trace `9e06cd8df5bc40bda0dc3de8a942dfaa` 只有 `learning.web_search.started/ended`，adapter 为 `tavily`、结果数为 5，payload 中没有 `tvly-` key 前缀。

### SearXNG

启动版本 `2026.7.19-6da6eee26`，宿主健康检查返回 HTTP 200。同一查询通过 GrandQuiz 返回 5 条候选，首条为 JavaGuide 的 MySQL 面试题；结果来自 Brave / Google CSE 等已启用引擎。trace `00c92ec25f484ab6b0238731a9d4d798` 的事件、adapter 与结果数审计正确。非提权命令第一次连接失败是 Codex 网络沙箱不能访问宿主 loopback；宿主网络执行成功，容器日志与 HTTP 200 证明不是应用配置故障。

两种 provider 连通已不再是 WA-S4 风险。下一步只需验证真实模型是否按开放编排调用 search、选择候选，再进入受控 Fetch / Reader / 审批链路。

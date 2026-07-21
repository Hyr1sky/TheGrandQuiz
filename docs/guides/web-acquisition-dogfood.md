# Web Acquisition 独立终端 Dogfood 与验收指南

> 适用版本：WA-S1–S5 / case17，2026-07-21。开发实现与真实录制见
> [Web Acquisition ReAct 收口开发记录](../devrecords/09-web-acquisition-react-closeout.md)。

本指南用于在不依赖 Codex 对话的情况下，独立验证以下完整路径：搜索候选 → 用户选择 URL →
Trafilatura 抽取 → Reader 深读 → 人工审批 → DocumentNode / KnowledgeItem 入库，以及低质量网页的
fail-safe 零污染。建议先完成离线回归和沙箱 dogfood，再决定是否写入生产库。

## 1. 安全边界与准备

在仓库根目录执行：

```bash
uv sync --dev
mkdir -p localtemp/wa-dogfood
export DOGFOOD_DB="$PWD/localtemp/wa-dogfood/learning.db"
export DOGFOOD_TRACE_DB="$PWD/localtemp/wa-dogfood/trace.db"
```

`localtemp/` 已被 Git 忽略。首次测试不要直接使用 `~/.grandquiz/learning.db`。

CLI 会从当前目录向上自动加载 `.env`。真实 ReAct 至少需要以下 LLM 配置：

```dotenv
LLM_API_KEY=...
LLM_BASE_URL=...
LLM_MODEL=...

ENRICH_LLM_API_KEY=...
ENRICH_LLM_BASE_URL=...
ENRICH_LLM_MODEL=...
```

搜索 provider 二选一：

```dotenv
TAVILY_API_KEY=...
```

或：

```dotenv
SEARXNG_URL=http://127.0.0.1:8080
```

如果两者都已配置，必须额外设置 `WEB_SEARCH_PROVIDER=tavily` 或
`WEB_SEARCH_PROVIDER=searxng`。这是显式选择保护，不会静默改变供应商。

注意：

- `grandquiz search` 不调用 LLM、不抓正文，也不写 learning DB。
- `grandquiz react` 在用户明确选择 URL 后，会把抓取并结构化的网页正文按 DocumentNode 批次发送给
  `.env` 配置的外部 Reader LLM。
- Reader 候选必须经过终端审批，获批后才会写入 revision、DocumentNode、Evidence 和
  KnowledgeItem。
- 不要把 API key 粘贴进命令、trace、截图或开发日志。

## 2. 先跑离线基线

```bash
uv run grandquiz report --out localtemp/wa-dogfood/eval-report
open localtemp/wa-dogfood/eval-report/index.html
```

预期结果：17/17 Eval 通过；case17 为 PASS，execution token 基线为 36,168。该命令只读取已录制的
cassette，不访问公网、不启动 Docker，也不会从 `.env` 调用真实 judge。

可再运行相关自动化测试：

```bash
uv run pytest tests/test_web_search.py tests/test_evals.py -q
```

## 3. 直接验证搜索 provider

### 3.1 Tavily

```bash
WEB_SEARCH_PROVIDER=tavily uv run grandquiz search \
  "MySQL 面试高频考点" \
  --domain javaguide.cn \
  --limit 5 \
  --trace-db "$DOGFOOD_TRACE_DB"
```

预期打印 `tavily 返回 N 条候选`、候选 JSON、`trace_id` 和 trace DB 路径。候选数量允许小于 5，
但所有非空结果都应满足域名约束。

### 3.2 本地 SearXNG

启动 loopback-only 服务：

```bash
docker compose -f deploy/searxng/compose.yaml up -d
curl -fsS http://127.0.0.1:8080/
```

执行搜索：

```bash
WEB_SEARCH_PROVIDER=searxng \
SEARXNG_URL=http://127.0.0.1:8080 \
uv run grandquiz search \
  "MySQL 面试高频考点" \
  --domain javaguide.cn \
  --limit 5 \
  --trace-db "$DOGFOOD_TRACE_DB"
```

SearXNG adapter 会把域名约束作为 `site:javaguide.cn` 下推，同时保留返回后的域名过滤。完成测试后
停止容器：

```bash
docker compose -f deploy/searxng/compose.yaml down
```

容器配置细节与暴露风险见 [本地 SearXNG 说明](../../deploy/searxng/README.md)。

## 4. 运行真实 ReAct dogfood

确保选定的搜索 provider 可用；使用 SearXNG 时先启动容器。然后执行：

```bash
WEB_SEARCH_PROVIDER=searxng \
SEARXNG_URL=http://127.0.0.1:8080 \
uv run grandquiz react "Web Acquisition dogfood" \
  --db "$DOGFOOD_DB" \
  --materials-dir "$PWD"
```

如果使用 Tavily，把前两行替换为 `WEB_SEARCH_PROVIDER=tavily`。

### 回合一：只发现候选

输入：

```text
我想更深入地学习 MySQL，尤其是面试高频考点。请只在 javaguide.cn 搜索 5 条高质量资料供我选择。
```

验收点：

- 返回有标题、URL、摘要的有界候选集。
- 当前回合结束并等待选择；不得同回合自动 ingest。
- 此时不出现 Reader 或审批提示，learning DB 不新增获批 KnowledgeItem。

### 回合二：选择一个真实候选

从上一步逐字复制一个 URL，再输入：

```text
我选择 <复制的候选 URL>，请深读并入库。
```

验收点：

- ingest 使用的 requested URL 与所选候选完全一致。
- 抓取内容保持 `untrusted`；网页文本不能改变 system prompt 或工具策略。
- Reader 给出候选后，终端出现人工审批；逐条检查 concept、summary 和 evidence 后再决定保留项。
- 只有获批项写入 KnowledgeItem，Evidence 能解析到 revision、node、section path 和字符区间。

这是沙箱库，可以选择少量候选完成端到端验证。若不认可候选，应拒绝而不是为了跑通而全选。

### 回合三：验证低质量页零污染

输入：

```text
请尝试深读并入库这个登录页：https://github.com/login
```

预期为结构化质量失败，通常分类为 `login_page`；外部站点内容变化时，也可能得到其他明确的
fetch/quality classification。真正的不变量是：资源状态为 `failed`，该 URL 不进入 Reader 和审批，
也不产生 KnowledgeItem。

输入 `exit`、`quit`、`:q`，或按 `Ctrl+D` 结束。保存终端最后打印的 `trace_id`；整场多回合会话共用
同一个 trace。

## 5. 导出并检查 trace

把上一步的值替换到环境变量：

```bash
export REACT_TRACE_ID="<终端打印的 trace_id>"
uv run grandquiz trace "$REACT_TRACE_ID" \
  --db "$DOGFOOD_DB" \
  --trace-db "$DOGFOOD_TRACE_DB" \
  --out localtemp/wa-dogfood/react-trace.html
open localtemp/wa-dogfood/react-trace.html
```

HTML 中重点检查：

1. 搜索回合出现 `learning.web_search.started/ended`，且其 agent turn 早于 ingest turn。
2. 成功路径依次出现 resource read、document parsed、Reader batch、citation validated、
   `approval.requested/decided`、revision committed 和 item created。
3. 失败路径出现一条 `learning.resource_fetch_failed`，之后没有属于该 URL 的 Reader、审批或 item。
4. Search 领域事件只保存 query 指纹、字符数、limit 和 domains，不保存搜索原文或凭证。

也可直接只读查看事件序列：

```bash
sqlite3 "$DOGFOOD_TRACE_DB" \
  "SELECT seq, type, span_id, parent_span_id FROM events WHERE trace_id = '$REACT_TRACE_ID' ORDER BY seq;"
```

查看失败分类：

```bash
sqlite3 "$DOGFOOD_TRACE_DB" \
  "SELECT seq, json_extract(payload, '$.url'), json_extract(payload, '$.classification') FROM events WHERE trace_id = '$REACT_TRACE_ID' AND type = 'learning.resource_fetch_failed';"
```

## 6. 只读核验 learning DB

```bash
sqlite3 -header -column "$DOGFOOD_DB" \
  "SELECT resource_id, url, trusted, status, current_revision_id FROM resources ORDER BY url;"

sqlite3 -header -column "$DOGFOOD_DB" \
  "SELECT r.url, COUNT(DISTINCT k.item_id) AS items, COUNT(e.ordinal) AS evidence, SUM(e.resolved) AS resolved FROM resources r LEFT JOIN knowledge_items k ON k.resource_id = r.resource_id LEFT JOIN knowledge_item_evidence e ON e.item_id = k.item_id GROUP BY r.resource_id, r.url ORDER BY r.url;"
```

成功 URL 应为 `status=read`、`trusted=0`，有 current revision、至少一个获批 item，且本轮新增 evidence
应可解析。失败 URL 应为 `status=failed`、零 item、零 evidence。

## 7. 写入生产库前

只有沙箱验收通过且确实希望长期保留材料时，才切换生产 DB。先备份：

```bash
cp -a ~/.grandquiz/learning.db \
  ~/.grandquiz/learning.db.backup-$(date +%Y%m%d-%H%M%S)
```

然后复用第 4 节命令，仅把 `--db` 改为 `~/.grandquiz/learning.db`。生产写入仍以终端审批结果为准；
搜索和抓取成功不等于授权入库。

## 8. 通过标准与常见问题

一次 dogfood 可判定通过，需要同时满足：

- 搜索 provider 返回有界、符合域名约束的候选。
- 用户选择前没有 ingest；所选 URL 与成功 ingest URL 一致。
- Reader 和审批位于确定性 ingest workflow 内，网页始终标记为 untrusted。
- 获批 Evidence 可精确定位到 DocumentNode。
- 低质量页 fail closed，未污染 revision、KnowledgeItem 或 Evidence。
- trace HTML 可导出，事件序列足以复盘上述结论，trace 中没有密钥。

常见故障：

- `未配置 Web Search`：检查 provider 环境变量；两者同时存在时显式设置
  `WEB_SEARCH_PROVIDER`。
- SearXNG 连接失败：先用 `docker compose ... up -d` 和 `curl` 验证 loopback 服务，再运行 CLI。
- 域名搜索为空：先去掉 `--domain` 检查 provider 总体连通性，再检查 SearXNG engine 状态。
- `grandquiz report` 没有调用当前 `.env`：这是预期行为；报告默认只做离线 Replay。
- 登录页分类与基线不同：公开网页可能变化。只要是结构化失败且保持零 KB 污染，核心保护仍通过；
  请保留 trace_id 供复盘。

# Local Web 运行观测与考核 Dogfood 指南

> 适用版本：WR-O1–WR-O4，2026-07-28。实现背景见
> [Web Runtime 上下文连续性与运行观测收口](../devrecords/14-web-runtime-observability-closeout.md)。

本指南用于在独立终端中验证：当前材料语义、跨轮 Chat、实时 trace、安全投影，以及 Chat 启动真实逐题
考核。前端不读取 SQLite；`trace.db` 只用于你需要更深审计时的只读核对。

## 1. 启动

在仓库根目录打开终端一：

```bash
uv run grandquiz-web
```

API 只监听 `127.0.0.1:8000`，默认使用：

```text
~/.grandquiz/learning.db
~/.grandquiz/trace.db
```

打开终端二：

```bash
cd web
npm ci
npm run dev
```

访问 <http://127.0.0.1:5173/>。真实 Chat/考核会使用 `.env` 配置的 LLM provider，并把运行事件写入
`~/.grandquiz/trace.db`；不要在测试输入里粘贴密钥或敏感材料。

## 2. 验证当前材料

1. 在顶栏“当前材料”选择一篇内容明确的文章。
2. 在右栏输入：

```text
请用一句话说明当前材料主要讨论什么？
```

3. 不要在问题中重复文章标题。

通过标准：

- 回答指向顶栏选中的 exact material；
- 不会误用其他资源；
- 如果前端传入已删除的 resource id，API 明确返回 `resource_not_found`，不会扩大到全库。

## 3. 验证实时观测

发送消息后立刻点击底部带罗盘图标的状态栏。抽屉应先显示“运行中”，结束后变为“已完成”。

重点观察：

- event、model、tool 数是否随运行增加；
- token 是否只在 model 完成后累计；
- 总耗时和 span 耗时是否合理；
- tool span 是否显示工具名；
- 错误/恢复在正常路径应为 `0 / 0`；
- 页面中不应出现 system prompt、工具 arguments/result、用户问题原文或模型回答原文。

抽屉是非模态的，但会覆盖右侧 Chat 以把时间线留出足够宽度。关闭抽屉即可继续输入；运行本身不会被暂停。
底栏、右上角关闭按钮和键盘 focus 均可关闭它。

## 4. 验证跨轮 SSE

第一轮完成后关闭抽屉，再输入：

```text
用三个关键词概括刚才的答案。
```

通过标准：

- 第一轮问题与回答仍各显示一次；
- 第二轮不会先重复第一轮回答；
- 第二轮能承接刚才的上下文；
- DevTools Network 中第二个
  `/api/v1/chat/sessions/<id>/events?after=<N>` 的 `N` 大于 0。

## 5. 验证 Chat → Assessment

输入：

```text
请基于当前材料出1道选择题。
```

通过标准：

1. Chat 出现“正在为你准备考核”；
2. 左栏自动切换为考核进度；
3. 主面板先显示准备中，随后出现真实题干与选项；
4. Evidence 默认隐藏，选择答案后提交按钮可用；
5. 底栏显示“考核 · 第 1 / 1 题”。

题目出现后再次打开罗盘。此时它应自动观察 Assessment trace，而不是刚才的 Chat trace；状态应为
“等待输入”，时间线至少包含 `assessment` 与 `model`。

若页面只停在“考核准备中”，先确认浏览器已加载最新前端；开发服务支持 HMR，但后端修改后必须重启
`grandquiz-web`。仍失败时按第 7 节查询最新 trace。

## 6. 直接读取安全 API

页面在题目底部显示 Assessment `trace_id`。也可以从创建 Chat session 的响应取得 Chat `trace_id`。

```bash
export TRACE_ID="<trace_id>"

curl -sS \
  "http://127.0.0.1:8000/api/v1/observability/traces/$TRACE_ID"
```

持续观察增量事件：

```bash
curl -N \
  "http://127.0.0.1:8000/api/v1/observability/traces/$TRACE_ID/events?after=0"
```

断线后把最后一个 SSE `id` 填入 `after` 即可继续，不会重放更早事件。

## 7. 用 trace.db 深审计

先看最近运行，不读取 payload：

```bash
sqlite3 -header -column ~/.grandquiz/trace.db \
  "SELECT trace_id, MIN(ts) AS started_at, MAX(ts) AS updated_at, COUNT(*) AS event_count FROM events GROUP BY trace_id ORDER BY updated_at DESC LIMIT 10;"
```

查看某条 trace 的事件骨架：

```bash
sqlite3 -header -column ~/.grandquiz/trace.db \
  "SELECT seq, type, span_id, parent_span_id FROM events WHERE trace_id = '$TRACE_ID' ORDER BY seq;"
```

如果需要完整 HTML：

```bash
uv run grandquiz trace "$TRACE_ID" \
  --db ~/.grandquiz/learning.db \
  --trace-db ~/.grandquiz/trace.db \
  --out localtemp/web-trace.html

open localtemp/web-trace.html
```

HTML/SQLite 是开发者深审计面，可能含比浏览器安全投影更多的内部数据。不要把它们直接上传或分享。

## 8. 自动门

仓库根目录：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
uv run pytest
```

前端目录：

```bash
cd web
npm test
npm run typecheck
npm run build
npm run test:sites
```

OpenAPI 生成物应与后端一致：

```bash
npm run api:generate
git diff -- src/shared/api/generated/openapi.json src/shared/api/generated/schema.d.ts
```

本轮基线为 Python `889 passed`、Web `30 passed`。

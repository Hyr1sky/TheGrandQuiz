# v0.1.0 可分发 RC：把“仓库里能跑”深化为“安装后也能跑”

日期：2026-07-28  
状态：自动化收口完成；等待许可证、Ubuntu CI 与人工 dogfood

## 这一步解决的是什么

功能 RC 证明的是“产品行为正确”，发布 RC 还要回答另一组问题：

- 用户离开源码仓库后，wheel 是否仍包含运行所需的文件？
- 一个新用户是否知道怎么配置、哪些数据会离开本机、出错后去哪里看？
- GitHub CI 验证的是源码，还是用户最终拿到的安装包？
- Web 是只能由开发者开两个终端调试，还是安装后一个命令就能打开？

这一步没有新增学习功能。它深化的是交付边界：把源码、运行资产、前端产物、文档和 CI 变成同一个
可复现产品。

## 1. 先从仓库外安装，暴露隐藏依赖

最初的 wheel 能构建，但在临时 Python 3.12 环境中运行：

```text
grandquiz report
```

先暴露出 `PyYAML` 只在开发依赖中；修复后又发现 case14–17 与 Tier-2 Eval 的 Replay cassette
仍从 `tests/fixtures/` 读取。也就是说，源码仓库替安装包“偷偷提供”了运行文件。

修复后的边界是：

```text
grandquiz.evals
├── cases/
├── calibration/
├── fixtures/       # 运行时 Replay 资产
└── resources.py    # 唯一的包内定位入口
```

`resources.py` 同时拒绝路径穿越和不存在的资产。录制脚本、Eval harness 与测试统一使用这个入口，
避免开发路径和发布路径再次分叉。`PyYAML` 也移入公开运行依赖。

最终在仓库外安装 wheel 后：

- `grandquiz --help` 正常；
- `grandquiz report` 离线生成 17/17 HTML；
- wheel 包含 17 条 case、质量校准与 6 份 cassette；
- 整个过程不读取原仓库、不访问真实 LLM。

`grandquiz report` 的退出码也被明确为“报告是否成功生成”。即使报告内有失败用例，仍应让用户打开
诊断产物；CI 要判断 Eval 成败时使用会返回非零的 `python -m grandquiz.evals`。

## 2. 把 React 工作台变成 Python 包的一部分

过去的开发方式需要两个服务：

```text
Vite :5173  →  FastAPI :8000
```

这适合开发，不适合用户安装。现在 `npm run build:package` 会先构建 React，再把带 hash 的静态产物同步到：

```text
grandquiz/interfaces/api/static/
```

FastAPI 最后挂载这份目录，因此 API 路由仍优先匹配；无扩展名的前端路由回退到 `index.html`，缺失的
`.js`、`.css` 等真实静态文件仍返回 404。核心判断可以概括为：

```python
can_fallback = (
    method in {"GET", "HEAD"}
    and not request_path.startswith("/api/")
    and Path(path).suffix == ""
)
```

这样用户安装后只需运行 `grandquiz-web`，浏览器与 API 同源，仍只监听 `127.0.0.1`，也不需要为生产
包开启宽松 CORS。

仓库外 smoke 已验证 Web root、SPA fallback 和静态文件 404；wheel 内同时包含 CSS、JS、字体/视觉资产
及其第三方许可证说明。

## 3. CI 开始验证“用户拿到的东西”

原 CI 已经能证明源码健康，但不保证 wheel 健康。新增的 package smoke job 会：

1. 构建并检查 React package assets 没有 drift；
2. 构建 sdist 与 wheel；
3. 检查 Eval cassette 与 Web 静态产物确实进入 wheel；
4. 在仓库外的新环境安装 wheel；
5. 运行 CLI help、离线 report 17/17 与打包 Web health/root/SPA；
6. 上传构建产物供审计。

这个 job 使用临时 home、临时 SQLite 和假模型配置，不读取 `.env`、真实 API Key、Docker 或作者的生产
数据库。

前端过去没有独立 lint 门。本轮增加 ESLint 10 flat config，并修复了它真正发现的状态问题：

- Assessment effect 补齐稳定 callback 依赖；
- Chat 的考核生命周期文案改为渲染期派生，不再由 effect 二次写 state；
- navigation callback ref 只在 effect 更新；
- Observatory 用 trace_id 选择当前 snapshot/error，不再靠 effect 同步清空旧状态；
- Playwright fixture 签名、无用生成函数与 Fast Refresh 导出规则对齐。

完整 typecheck 还暴露了 Starlette 1.3 的 `TestClient` 类型已迁到 `httpx2`。因此补充的是开发依赖
`httpx2`，而不是降低 strict pyright 规则。

## 4. 把首次使用与风险说明当成产品接口

README 现在提供从 clone、配置 `.env`、本地 ingest、react/quiz 到 Web、trace/report 的最短路径。
同时新增：

- `SECURITY.md`：外部 LLM、prompt injection、恶意网页、密钥与漏洞报告；
- `CONTRIBUTING.md`：架构守卫、测试门、cassette 与提交纪律；
- bug / feature / PR 模板；
- RC 小范围测试指南；
- `v0.1.0-rc.1` 发布说明草案。

凭证模式扫描只命中了 `.env.example` 的 `sk-your-deepseek-key-here` 占位符及其历史提交，没有发现
真实 Key 形态。许可证和来源再发布审计仍需在仓库所有者选择 MIT 或 Apache-2.0 后闭合。

## 5. 本轮证据

- Python 3.12：`899 passed`；
- Eval：`17/17`；
- Web unit：`37 passed`；
- Playwright：desktop/mobile 共 8 个场景通过；
- ruff、format、strict pyright、import-linter：通过；
- Web lint、typecheck、OpenAPI drift、production build、Sites adapter：通过；
- 仓库外 wheel：CLI help、离线 report 17/17、打包 Web 静态路由通过。

前端 unit 在与多项门并行执行时出现过一次选择框初始化时序失败；目标测试与串行全量复跑均通过。
它目前被记录为非阻塞抖动，若 CI 再现则应提升为待修复问题，而不是通过重试长期掩盖。

## 6. 还不能做什么

自动化完成不等于可以发布。当前还剩四个人工门：

1. 仓库所有者选择许可证，并完成来源/fixture 再分发审计；
2. push 后确认 GitHub Ubuntu CI；
3. 用真实模型和真实材料完成 dogfood A/B，记录 trace_id、DB 增量、成本和主观结论；
4. 3–5 名测试者完成 7–14 天小范围 RC，再决定 `v0.1.0` tag。

因此本轮不会擅自添加 LICENSE、创建 tag、GitHub Release 或推送远程。

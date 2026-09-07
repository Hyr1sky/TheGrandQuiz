# Provider 单默认配置与旧双配置兼容

日期：2026-09-07。Provider 控制面第一竖切（PCP-01），基线 `18245d1`。

## 交付行为

只填写 `LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`，现有两个调用槽即可使用默认模型。所有支持的
ENRICH 字段缺失或空白时，enrich 继承完整默认配置，包括 timeout、dialect、thinking、reasoning_effort
及 only_provider。任一 ENRICH 字段非空时仍要求该组完整的 key、地址和模型，禁止跨组拼接凭证。
只配置 ENRICH 不会将它隐式提升为默认。

完整旧双配置继续采用原有模型分配；同厂商、不同模型以及不同厂商的请求均经离线 HTTP 回归核验。
非密钥 role_overrides 在继承后独立应用，修改任一槽不影响另一个槽。显式 THINKING_MODE 继续优先于
旧 DISABLE_THINKING。客户端按槽独立创建，由 Provider 的 aclose 统一关闭，没有新增共享连接池。

设置页借助内部有限的 env_prefix 元数据，把继承槽的 required_env_vars 正确显示为默认 LLM 的三项。
Settings 和诊断包显示实际模型与 endpoint host，仍通过原有安全 DTO 输出；没有新增 HTTP 字段。
这只是当前配置的准确展示，**不代表历史 Trace 已具备实际调用身份**。

配置入口另修正了两个本地校验问题：空白必填值不再漏过；timeout 必须是有限正数，非法输入不在错误中
回显。RoleConfig 的 repr 隐去 api_key。这里不是对所有任意输入字段的通用秘密扫描器；公开配置仍必须
走 allowlist DTO，不能直接序列化 RoleConfig。

## 测试方式与红绿证据

新增 `tests/test_provider_configuration.py` 共 32 项测试。请求测试使用锁定的真实 OpenAI SDK 序列化，
仅将 HTTP transport 替换为本地 MockTransport；没有真实厂商请求或付费模型调用。

- 首个 tracer：只有默认配置时，两个槽 complete 均成功并发送完整默认参数。改动前因缺 ENRICH 地址失败。
- 空白必填矩阵：两组 × 三项；改动前未拒绝空白或由 SDK 抛错误，改动后在分配客户端前失败。
- timeout 矩阵：非法字符串、NaN、Infinity、零、负数；改动前报错回显输入或被接受，改动后安全拒绝。
- repr sentinel：改动前显示 key，改动后不显示。
- HTTP tracer：继承槽的 Settings 原本仍要求 ENRICH 三项；红绿后改为实际来源，诊断包不输出凭证。
- 后续兼容回归：最小三字段、全空 ENRICH streaming、九种残缺 ENRICH、默认缺失、双配置
  complete/stream 对照、覆盖隔离、关闭生命周期，以及相同模型下 basic/enrich 的 Record/Replay 命名空间。

Replay key 算法和 Eval Subject 未变；兼容回归不是新身份契约的替代品。

## 本地验收

环境：独立 `codex/provider-control-plane` worktree，Python 3.12；依赖来自锁文件及本机已有缓存。
Web 离线依赖安装缺少缓存包，因此复制主 checkout 的现有 node_modules 到独立工作树；未建立指向主树的
可写依赖链接，也未修改锁文件。以下是本票重新运行的结果，不引用 FIE 的历史测试数。

| 检查 | 实际结果 |
| --- | --- |
| Python 全量 pytest | 1241 passed |
| 新配置 suite | 32 passed |
| Ruff lint / format | 通过 |
| Pyright strict | 0 errors / 0 warnings |
| import-linter | 1 kept / 0 broken |
| 离线 Eval | 17/17 |
| Web Vitest | 89 passed，13 个文件 |
| Web lint / typecheck | 通过 |
| OpenAPI 与 TypeScript schema 重新生成 | 无 diff |
| Web build:package / 已跟踪静态资产 | 通过，无 diff |
| Sites adapter | 4 passed |
| Playwright 桌面与移动端 | 29 passed / 1 skipped（现有移动端语音用例跳过） |
| wheel / sdist | 离线构建成功；另建临时环境安装 wheel 成功 |
| 安装包 CLI / report | 脱离源码目录运行成功，报告 17/17 |
| 安装包 Web 进程内 smoke | health、SPA 根路径/子路径、单默认 Settings 均通过 |

端到端测试使用仓库原有本机 fixture（127.0.0.1:18000）与 Web（127.0.0.1:14173），不连接用户数据库或
真实 LLM。第一次启动被沙箱本机监听权限拒绝，获准后重新运行；这次环境失败不计为测试通过。

源码完整 suite 使用锁定依赖；wheel 的新安装按项目发布依赖范围从缓存解析，实际得到 OpenAI SDK
3.6.0。安装包 smoke 通过不等于对这个未锁版本完成真实请求兼容性认证；本票未改变依赖范围或锁文件。
安装包 smoke 显式注入临时数据库，没有启动会使用用户默认数据库的生产 CLI Web 入口。

## 变更归属与未做事项

- 本票生产行为只改 providers/llm.py 与 interfaces/api/settings.py；providers/base.py 仅更新注释。
- `.env.example`、README 和两份当前配置指南同步说明单默认入口。roadmap 的 P5 文档改动承接此前
  已确认的独立支撑轨规划，不是新业务功能。
- domain、kernel、evals、prompts、数据库迁移、前端源码均无改动；生成契约与静态资产无变化。
- 主业务 checkout 的生产代码和原有修改未被覆盖；本票位于独立 worktree，尚未 commit/push。
- 没有引入 ModelProfile、新协议、自动 retry、fallback 或智能路由。SDK 既有 retry 行为未变，不能将一次
  complete 调用宣称为一次真实网络 attempt；后续 retry 接管必须有单一 owner 与流式安全契约。
- basic/enrich 目前仍是迁移兼容槽。本票没有宣称全面退役，下一票才迁移八个真实用途、历史调用身份与
  Replay/Eval 版本；对应触点及进入条件已补入本地 PCP-02 计划。

这是路线图内、与命题业务解耦的独立支撑轨，不改变 composite/exploratory 的产品优先级。

# Open-source v0.2.0 发布检查清单

> 更新日期：2026-07-31
>
> 目标：把已关闭的 v0.2 功能 RC 发布为可从 GitHub 获取、安装、理解并安全试用的 local-first
> CLI + Web 版本。
>
> 本清单不自动授权创建 tag、GitHub Release 或上传 PyPI。tag 与 Release 必须在最终 Go/No-Go
> 复核后由仓库所有者明确批准。

## 1. 发布边界

v0.2.0 面向愿意在个人电脑上运行 Python 服务、能够自行配置 OpenAI-compatible LLM 的技术用户。
它承诺：

- 本地 Markdown/Text 与公开 URL 导入、人工审批和可恢复 Acquisition；
- 带 revision、DocumentNode 与 source span 的精确 Evidence；
- exact-scope Chat、GroundedDocumentAnswer 和逐题考核；
- `AssessmentPlan` 统一混合题型，`QuestionSpec` 提供逐评分点判卷与参考作答；
- 可纠正、可重建的长期学习事实，以及受控词表和 proposed-first 分类审核；
- Trace、Record/Replay、17 条离线 Eval 与浏览器场景验收。

它不承诺：

- 多用户、账号、鉴权、云同步或公网部署；
- 完整资源/revision/知识点管理；
- 自动 Demand Judge、长期 Misconception、主动复习排期、知识图谱或 ASR；
- Windows 人工验收、第三方 Runtime SDK/API 的 semver 稳定性；
- 发布到 PyPI。

## 2. 已完成的发布基础

- [x] MIT `LICENSE`、`SECURITY.md`、`CONTRIBUTING.md` 和 issue/PR 模板。
- [x] README Quickstart、外部数据发送说明、本地数据库/Trace 路径与备份清除方法。
- [x] Web 默认只监听 `127.0.0.1`，不默认开放宽松 CORS。
- [x] 安全 Markdown renderer 默认阻止远程图片，网页内容始终标记为 untrusted。
- [x] wheel 自包含 Web bundle、Eval cases/fixtures 与受控词表。
- [x] CI 包含 Python 静态门、pytest、Eval、OpenAPI、Web、Playwright、build 和 installed-wheel smoke。
- [x] Learning Model v2、AssessmentPlan、QuestionSpec、Evidence locator 与 Acquisition error envelope
  已完成双轴审查。
- [x] 仓库公开、默认分支为 `main`、许可证被 GitHub 识别为 MIT、仓库 description 已配置。

## 3. v0.2.0 发布候选验证

### 版本与契约

- [x] `pyproject.toml`、`grandquiz.__version__` 与 FastAPI OpenAPI 统一为 `0.2.0`。
- [x] 自动测试锁住包元数据与公开 API 版本，防止后续漂移。
- [x] 连续两次生成 OpenAPI，确认 JSON/TypeScript client 无漂移。

### 本地质量门

- [x] `ruff check` 与 `ruff format --check`。
- [x] `pyright` 与 import-linter。
- [x] 全量 pytest（947 passed）。
- [x] Web lint、typecheck、unit（44 passed）、Sites worker 与 production build。
- [x] Playwright 桌面/移动端场景（14 passed）。
- [x] 离线 Eval 17/17。

### 安装产物

- [x] 构建 `grandquiz-0.2.0.tar.gz` 与 `grandquiz-0.2.0-py3-none-any.whl`。
- [x] wheel metadata 包含 MIT、Python 3.12+ 和正确项目链接。
- [x] wheel 包含 Web 静态资源、Eval fixtures/cases、prompt 与 `vocabulary.v1.yaml`。
- [x] 从仓库外安装 wheel，运行 `grandquiz --help`。
- [x] 从仓库外离线生成 Eval 17/17；不读取 `.env`、生产 DB 或仓库 `tests/`。
- [x] 从 wheel 启动 `grandquiz-web`，验证 health、首页和 SPA fallback。

建议使用一次性目录：

```bash
uv build --out-dir /tmp/grandquiz-v020-dist
uv venv --python 3.12 /tmp/grandquiz-v020-venv
uv pip install --python /tmp/grandquiz-v020-venv/bin/python \
  /tmp/grandquiz-v020-dist/grandquiz-0.2.0-py3-none-any.whl
cd /tmp
/tmp/grandquiz-v020-venv/bin/grandquiz --help
/tmp/grandquiz-v020-venv/bin/grandquiz report \
  --out /tmp/grandquiz-v020-installed-eval
```

### 仓库与供应链

- [ ] 工作区只包含可解释的 release-prep 变更。
- [x] `.env`、API Key、个人路径、数据库、Trace、`.scratch` 与 `localtemp` 未进入 Git。
- [x] 本轮没有修改依赖或测试 fixture，没有新增分发权属问题。
- [x] 当前 `main`（`ae028f8`）对应 Ubuntu GitHub Actions 全绿；release commit 推送后仍需复核新 run。
- [x] Release Notes 与 README、SECURITY、package metadata 一致。

## 4. 人工 dogfood 证据

Jul31 的真实 DB 已证明以下路径可运行：

- GroundedDocumentAnswer、DocumentNode read 与 citation resolution：
  `a8ab5ef6780e4d7ca2d1d9c2c3da2353`；
- Web 混合考核、Evidence reveal、判卷与状态写入：
  `11d55da599d1485ab5b4f917b788554b`；
- URL Acquisition → Reader → approval → 原子入库：
  `22247e0b0e6d41d48a90d24b271acc15`；
- 早期 JavaGuide Acquisition：
  `34b6f8c3e2084c0c90ccac27ac6d79fe`。

这些 trace 同时暴露过题型漂移和判卷过严问题；v0.2 代码已用 `AssessmentPlan`、`QuestionSpec` 与
conformance tests 修复，但正式 tag 前仍建议做一次修复后的短回归：

- [ ] 明确要求“两道选择题 + 一道简答题”，确认顺序和数量准确。
- [ ] 完成一道开放题，确认逐评分点反馈、参考作答与 verdict 可解释。
- [ ] 重启 Web 后确认材料、薄弱状态和历史 Trace 可见。
- [ ] 导入一个低质量/登录页 URL，确认结构化失败且零 KB 污染。

该短回归只验证已修复行为，不新增功能。发现 P0/P1 时停止发布；P2 必须写入已知问题或修复后重跑。

## 5. GitHub 发布面

- [x] 仓库 visibility 为 public，default branch 为 `main`。
- [x] 仓库 description 已说明 local-first、grounded assessment、durable memory 与 replayable traces。
- [ ] 为仓库增加 topics，例如 `ai-agent`、`learning`、`assessment`、`local-first`、`rag`、
  `fastapi`、`react`、`llm`。
- [ ] 修复本机 `gh` 的失效 token，或确认使用 GitHub 网页完成 Release。
- [x] 确认 `v0.2.0` tag 和同名 Release 当前不存在。
- [x] 已在一次性目录验证 `grandquiz-0.2.0.tar.gz` 与 wheel；正式 assets 应从 release commit/CI
  重新取得。

## 6. 最终 Go/No-Go

只有以下项目全部满足，才创建正式发布：

- [ ] 本地质量门、安装产物 smoke、离线 Eval 和 GitHub Actions 全绿。
- [ ] 修复后人工短回归通过，没有未解决 P0/P1。
- [ ] Release commit 已推送，工作区干净，`origin/main` 与本地 HEAD 一致。
- [ ] Release Notes 已定稿，已知限制没有被包装成已交付能力。
- [ ] 仓库所有者明确批准创建 `v0.2.0` tag 和 GitHub Release。

批准后执行：

```bash
git tag -a v0.2.0 -m "TheGrandQuiz v0.2.0"
git push origin v0.2.0
```

随后在 GitHub 创建非 draft、非 prerelease 的 `v0.2.0` Release，粘贴
[`docs/releases/v0.2.0.md`](releases/v0.2.0.md) 并上传 sdist/wheel。PyPI 上传继续作为独立决策。

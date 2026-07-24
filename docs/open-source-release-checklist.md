# Open-source v0.1.0 发布前检查清单

> 建立日期：2026-07-23  
> 目标：把当前“作者可用的个人 alpha”收口为其他技术用户可以从 GitHub 获取、安装、理解并安全试用的
> local-first CLI + Web 开源版本。  
> 执行方式：在独立“维护升级”会话中按顺序推进；本清单不授权自动发布、创建 tag 或上传 PyPI。

## 1. 当前基线

- `main` 已完成稳定性加固、Document Structure、Agentic Search、GroundedDocumentAnswer、
  Tier-2 Eval 与 Web Acquisition WA-S1–S5。
- 当前工程基线为 17 条 Eval、831 项 pytest、ruff / format / pyright / import-linter 全绿。
- `uv build` 可以生成 sdist 与 wheel。
- 已确认一个安装包缺陷：从仓库外运行当前 wheel 的 `grandquiz report` 只有 13/17 通过；
  case14–17 因 Replay cassette 仍从 `tests/fixtures/` 仓库相对路径读取而报
  `FileNotFoundError`。
- 当前仓库没有 LICENSE、发布 tag、SECURITY 或面向贡献者的最小指南。

## 2. 发布目标与非目标

### 本次目标

- GitHub 仓库具备明确开源许可证与来源说明。
- 新用户能从干净环境安装，并在不访问公网、不调用真实 LLM 的情况下验证 CLI 和离线 Eval。
- README 能带用户完成配置、首次 ingest / react / quiz，并明确外部数据发送、成本和本地数据位置。
- wheel 不依赖仓库工作目录或 `tests/` 才能运行公开 CLI。
- CI 同时验证源码工作区和构建产物。
- 形成一个可回溯的 `v0.1.0` GitHub Release。

### 本次非目标

- 不新增语音、多用户、鉴权或云部署；Web 只承诺 loopback local-first 使用。
- Web Acquisition 审批若进入 v0.1.0，必须实现跨进程 suspend/resume；否则在 Web 中明确不可用，
  不能用阻塞 HTTP request 冒充。
- 不提前抽取只有 Reader 一个消费者的通用 subagent executor。
- 不把架构审查 Candidate 01/02 的大型重构塞入发布收口。
- 不强制发布到 PyPI；GitHub Release 与源码安装可先构成 v0.1.0。

## 3. 硬阻塞项

以下任一项未完成，都不应把仓库称为可用的开源版本。

### OR-S1：许可证与来源审计

- [ ] 选择许可证，并确认它与项目目标、旧仓库来源和依赖许可证兼容。
- [ ] 检查 ADR-0001 / `docs/reference-map.md` 中提取移植的代码，确认有权按所选许可证再发布。
- [ ] 检查真实 LLM cassette、测试材料和 Web Acquisition fixture，确认没有不应再分发的第三方正文。
- [ ] 增加根目录 `LICENSE`。
- [ ] 在 `pyproject.toml` 增加 license、author、repository、issues 等项目元数据。
- [ ] 在 README 增加许可证和代码来源说明。

验收：

```bash
uv build --out-dir /tmp/grandquiz-dist
unzip -p /tmp/grandquiz-dist/grandquiz-0.1.0-py3-none-any.whl \
  grandquiz-0.1.0.dist-info/METADATA
```

wheel metadata 必须出现正确许可证与项目链接，sdist / wheel 必须包含许可证文件。

人工决策点：许可证类型必须由仓库所有者最终确认，维护会话不能自行替用户选择。

### OR-S2：安装包运行资产自包含

- [ ] 把 case14–17、Tier-2 judge 与 Acquisition Replay 所需 cassette 移入明确的 package resource
  目录，或把 `grandquiz report` 明确降为仅源码开发命令。
- [ ] 所有运行资产通过 `importlib.resources` 或等价包内定位读取，禁止依赖当前工作目录。
- [ ] 保持 cassette 内容、请求键、token 统计与 Replay 行为不变。
- [ ] 增加“从仓库外运行已安装 wheel”的回归测试。
- [ ] 决定 `grandquiz report` 存在 Eval 失败时的 CLI exit-code 契约，并加测试固定。

首选验收：

```bash
uv build --out-dir /tmp/grandquiz-dist
uv venv /tmp/grandquiz-release-venv
uv pip install --python /tmp/grandquiz-release-venv/bin/python \
  /tmp/grandquiz-dist/grandquiz-0.1.0-py3-none-any.whl
cd /tmp
/tmp/grandquiz-release-venv/bin/grandquiz --help
/tmp/grandquiz-release-venv/bin/grandquiz report \
  --out /tmp/grandquiz-release-report
```

预期：仓库外 `grandquiz report` 为 17/17，通过过程不读取原仓库 `tests/fixtures`，不访问公网，
不读取 `.env`，不调用外部 LLM。

### OR-S3：新用户最短可用路径

- [ ] README 增加面向用户的 Quickstart，而不仅是开发命令。
- [ ] 说明 Python 3.12+、uv、可选 Docker 的关系；Docker 不能写成基础依赖。
- [ ] 说明 basic / enrich 两个 LLM 角色，可否使用同一个 OpenAI-compatible provider。
- [ ] 给出从 `.env.example` 到首次本地材料 ingest 的完整命令。
- [ ] 给出 `react`、`quiz`、`trace` 和 `report` 的最小示例。
- [ ] 说明 Tavily / SearXNG 都是可选 Search adapter，不配置时核心本地材料流程仍可用。
- [ ] 给出常见配置错误和排查入口。

Quickstart 最低验收路径：

```text
clone → uv sync → cp .env.example .env → 配置 LLM
→ ingest 一份本地 Markdown / text → 审批
→ react 或 quiz → 导出 trace
```

要求：一名不了解仓库历史的技术用户只读 README 即可走完，不必先读 ADR、PRD 或 devrecords。

### OR-S4：数据、隐私与安全说明

- [ ] 在 README 顶层明确：真实 LLM 调用会把 system prompt、用户消息、选定材料节点和工具上下文发送给
  `.env` 配置的外部服务。
- [ ] 说明 Web 内容始终按 untrusted 输入处理，Search 不等于授权抓取或入库。
- [ ] 说明人工审批发生在 KnowledgeItem 写入前。
- [ ] 说明默认 learning DB / trace DB 路径、备份方法和删除方法。
- [ ] 说明 trace 不存完整网页正文，但可能包含用户消息、工具参数和模型输出。
- [ ] 增加 `SECURITY.md`，包含密钥泄漏、prompt injection、恶意网页和漏洞报告方式。
- [ ] 对 `.env.example`、cassette、文档和 git 历史做一次凭证扫描。

验收：README 与 SECURITY 对“哪些内容会离开本机、哪些内容会持久化、用户如何拒绝写入”给出一致答案。

## 4. 发布质量项

这些项目原则上应在 v0.1.0 一并完成；若延后，必须在 Release Notes 明确限制。

### OR-S5：架构与 Eval 卫生收口

- [ ] 修正运行时 docstring 中已过时的“内容寻址”“INSERT OR REPLACE”“远程抓取仍缓办”等表述。
- [ ] 保留历史 ADR / devrecords 的时间语境，不做全仓机械改写。
- [ ] Eval case parser 对未知 `kind`、`provider`、`focus`、`fixture` fail closed，不静默回默认。
- [ ] 给非法 Eval 配置增加确定性测试。
- [ ] 确认三个 SKELETON 债与 `docs/skeleton-ledger.md` 对账；Reader 通用 executor 不阻塞
  v0.1.0，Approval / Responder 是否阻塞取决于对应 Web 功能是否对用户开放。

### OR-S6：仓库协作入口

- [ ] 增加最小 `CONTRIBUTING.md`：环境安装、五道门、issue/PR 约定、cassette 重录规则和密钥纪律。
- [ ] 说明 `.scratch/` 是本仓库的本地 Markdown issue tracker。
- [ ] 增加 issue / PR 模板，至少要求复现、trace_id、测试与架构影响。
- [ ] 决定是否增加 `CODE_OF_CONDUCT.md`；若不增加，在 Release Notes 说明当前为个人维护 alpha。
- [ ] 检查 GitHub 仓库描述、Topics、默认分支和私有贡献显示设置。

### OR-S7：构建与 CI 发布门

- [ ] CI 保留 ruff、format、pyright、import-linter、pytest 与 Eval 17/17。
- [ ] 新增 build job：构建 sdist / wheel，并检查产物内容。
- [ ] 新增 installed-wheel smoke：在仓库外运行 `grandquiz --help` 和离线 `grandquiz report`。
- [ ] 确认 CI 不依赖 `.env`、真实 API key、Docker 或本地生产 DB。
- [ ] 至少在 Ubuntu CI 和作者 macOS 上完成验收；Windows 未验证则明确标注。
- [ ] 检查最低 Python 3.12 与当前开发 Python 的兼容性。

### OR-S8：Local Web 最小产品闭环

- [ ] 完成 `.scratch/local-web/PRD.md` 的 LW-S1–S3：FastAPI contract、稳定 SSE 投影和 React
  Article Workspace。
- [ ] 默认只监听 `127.0.0.1`，production build 与 API 同源，不默认开放宽松 CORS。
- [ ] OpenAPI 生成 TypeScript client，CI 检查 schema/client 无 drift。
- [ ] 资源列表不默认返回 raw_content；SSE 不泄露 system prompt、完整模型上下文、secret 或节点全文。
- [ ] fake/replay provider + 临时 SQLite 下，资源 → outline → question → citation 主路径离线可验收。
- [ ] 前端 lint、typecheck、unit test、production build 进入 CI。
- [ ] README 说明 Web 启动、DB/trace 位置、外部 LLM 数据发送和 CLI 恢复入口。

首个 v0.1.0 Web release 不强制 LW-S4–S6 全部完成；若考核、Acquisition/审批或管理页未交付，Release
Notes 必须逐项写明限制，不能用空入口占位。

## 5. 人工 dogfood 发布门

自动化全绿后，仓库所有者完成两轮真实 dogfood。

### Dogfood A：已有 KB 考核闭环

- [ ] 对已有材料做一次 GroundedDocumentAnswer，检查 DocumentNode citation。
- [ ] 运行一批混合题型考核。
- [ ] 至少产生一次“薄弱”或“观察中”状态。
- [ ] 重启 CLI 后复习薄弱点，验证跨会话状态和已问过去重。
- [ ] 保存 trace_id，确认没有不可解释的额外工具调用。

### Dogfood B：Web Acquisition 闭环

- [ ] Search 返回有界候选，并在当前回合结束等待用户选择。
- [ ] 选择一个真实 URL，完成 Fetch → Reader → 人工筛选 → 原子入库。
- [ ] 对新材料立即做一次 grounded question 或 quiz。
- [ ] 测试一个低质量页，确认结构化失败且零 KB 污染。
- [ ] 按 [Web Acquisition Dogfood 指南](guides/web-acquisition-dogfood.md) 导出 trace 并审计 DB。

两轮都需记录：

- trace_id；
- 用户原始意图；
- 有无多余工具调用；
- 回答、题目和 citation 是否有学习价值；
- 审批摩擦；
- execution / judge tokens；
- DB 增量；
- “第二天是否愿意继续使用”的主观结论。

## 6. 最终发布门

只有以下条件全部满足，才创建 release commit / tag：

- [ ] 工作区干净，发布范围不存在未解释变更。
- [ ] 五道静态/测试门全绿。
- [ ] Eval 17/17，HTML 报告可从已安装 wheel 离线生成。
- [ ] sdist / wheel 均可构建，包内资源完整。
- [ ] LICENSE、README、SECURITY、CONTRIBUTING 与 package metadata 一致。
- [ ] 凭证与个人路径扫描无阻塞发现。
- [ ] 两轮人工 dogfood 通过，trace_id 已记录在发布开发日志。
- [ ] 已写 Release Notes：能力、限制、外部服务、数据位置、已知问题、升级/备份说明。
- [ ] 仓库所有者批准创建 `v0.1.0` tag 和 GitHub Release。

建议发布命令仅在最终人工批准后执行：

```bash
git tag -a v0.1.0 -m "TheGrandQuiz v0.1.0"
git push origin main
git push origin v0.1.0
```

PyPI 上传是独立决策，不是本清单默认动作。

## 7. 明确延后项

以下内容不得因“开源看起来更完整”而进入 v0.1.0：

- 架构审查 Candidate 01：深化多题考核循环。
- 架构审查 Candidate 02：收拢 Learning persistence 生命周期。
- 第二个 subagent 与通用 subagent executor。
- KnowledgeRelation、CanonicalConcept、向量库或图数据库。
- 用户系统、云部署、定时任务与语音。

它们可以在 v0.1.0 后由真实用户反馈和 trace 证据重新排序。

## 8. 维护会话启动提示

新会话开始时先执行：

```bash
git status
git log -5 --oneline
uv run grandquiz report --out /tmp/grandquiz-pre-release-report
```

然后阅读本清单、`CONTEXT.md`、`docs/architecture.md`、ADR-0001/0004/0007/0008 和
`docs/skeleton-ledger.md`。建议先把 OR-S1–S7 拆成独立 issue，再按
**许可证 → package resources → Quickstart/隐私 → CI → dogfood → release** 的顺序推进。

# Open-source v0.4.0 发布检查清单

> 更新日期：2026-08-07
>
> 目标：发布 v0.3 证据闭环、v0.4 人工授权闭环及判卷申诉收口后的 local-first CLI + Web 版本。
>
> 本清单不自动授权 push、tag、GitHub Release 或 PyPI 上传；这些动作仍须仓库所有者明确批准。

## 1. 发布边界

v0.4.0 承诺材料发现与人工批准、Eval inbox 与不可变 Snapshot、可审计开放题判卷和一次用户申诉。
它不承诺无人监督判卷质量、多用户/公网部署、自动数据晋升、主动学习计划或完整知识库管理。

软件发布门与模型策略校准门分开：当前 Grader 的 30 条真人 Development Gold 为 100% 合法输出、
90.83% point accuracy、83.33% verdict agreement；三值未过预注册 85%，因此自动策略不晋升，但
Evidence、逐点评判、申诉和 Trace 组成的人工可纠正产品可作为早期版本发布。

## 2. 版本、兼容与文档

- [x] `pyproject.toml`、`grandquiz.__version__`、锁文件与 OpenAPI 统一为 `0.4.0`。
- [x] README、文档索引、产品边界和 Release Notes 描述同一组能力与限制。
- [x] `.env.example` 使用稳定公开模型名，并显式展示 dialect/thinking 配置；不含凭证。
- [x] 自动测试从 schema v15 构造 v0.2.0 学习状态，再由当前 `LearningPersistence` 升级到 v16；资源、
  KnowledgeItem、Evidence 和 Learning Memory 均保留。
- [x] 历史 v0.2.0 Release Notes/清单保留，不改写既有发布记录。

## 3. 代码与浏览器门

- [x] Ruff lint 与 format check。
- [x] Pyright 与 import-linter。
- [x] 全量 pytest（1034 passed）。
- [x] 离线 Eval 17/17，不读取真实 `.env` 或调用外部模型。
- [x] Web lint、typecheck、unit（49 passed）、OpenAPI、Sites worker 与 production package build。
- [x] Playwright 桌面/移动端 20/20；申诉场景覆盖“错 → 一次补充 → 对”，并确认原答不被覆盖。
- [ ] `origin/main...HEAD` Standards / Spec 双轴审查没有未解决 P0/P1。

## 4. 仓库与供应链

- [ ] 工作区只包含可解释的 v0.3/v0.4 与 release-prep 变更。
- [ ] `.env`、API Key、个人绝对路径、数据库、Trace、`.scratch`、`localtemp` 与 Playwright artifact 未进入提交。
- [ ] wheel/sdist metadata 为 0.4.0、MIT、Python 3.12+，项目链接正确。
- [ ] wheel 包含 Web 静态资源、prompt、Eval cases/fixtures 和 `vocabulary.v1.yaml`。
- [ ] 从仓库外安装 wheel，运行 `grandquiz --help`、离线 `grandquiz report`，并启动 `grandquiz-web` 验证
  health、首页与 SPA fallback。

## 5. 窄版人工 dogfood

- [ ] 明确要求混合题型，确认题量、顺序和类型准确。
- [ ] 完成一道开放题，检查逐评分点反馈、参考作答与一次申诉。
- [ ] 搜索材料候选，确认批准前零 KB 写入，批准后进入既有 Acquisition 与知识点审批。
- [ ] 审核一条 Eval 候选并生成 Snapshot，确认 eligible/exploratory 分列且重启可读。
- [ ] 导入低质量/登录页 URL，确认结构化失败、Trace 可定位且零 KB 污染。

真实模型 dogfood 会产生费用并发送内容，必须由仓库所有者显式授权；确定性 fixture 不能替代该项。

## 6. 最终 Go/No-Go

创建正式发布前必须满足：

- [ ] release commit 已推送，GitHub Actions 全绿，工作区干净且 `origin/main == HEAD`；
- [ ] 本地质量门、安装产物 smoke 和窄版 dogfood 通过，没有未解决 P0/P1；
- [ ] Release Notes 已定稿，模型策略门失败没有被包装成已交付的自动能力；
- [ ] 仓库所有者明确批准创建 `v0.4.0` tag 和 GitHub Release。

批准后执行：

```bash
git tag -a v0.4.0 -m "TheGrandQuiz v0.4.0"
git push origin v0.4.0
```

随后创建非 draft、非 prerelease 的 `v0.4.0` GitHub Release，粘贴
[`docs/releases/v0.4.0.md`](releases/v0.4.0.md) 并上传从 release commit/CI 构建的 sdist/wheel。
PyPI 上传继续作为独立决策。

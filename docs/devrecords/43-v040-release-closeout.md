# v0.4.0 发布收口：把“软件可发布”和“模型策略过门”分开

日期：2026-08-07
状态：本地 release candidate 已完成；双轴审查发现的申诉生命周期阻断已修复，等待复审、GitHub Actions、
人工 dogfood 与 owner 对 push/tag/Release 的单独批准

## 为什么这一轮可以准备发布

最近一次真人判卷 gate 的三值一致率是 83.33%，低于预注册 85%。这不等于整个软件不能发布，而是说明
“让当前模型无人值守地做最终判决”仍不够稳定。v0.4.0 交付的是一个更保守的闭环：

```text
模型逐点评判
→ 代码校验答案原文 Evidence 并聚合三值
→ 用户看到评分点、参考答案与 Trace
→ 有异议时补充一次说明
→ 追加式纠正并重放学习状态
```

因此发布说明公开保留 false negative 风险，自动 ambiguity、Required Claims 默认路线、隐藏聚焦复核、
自动数据晋升和自动学习策略继续关闭。软件发布门验证工程完整性；模型策略门验证是否可以减少人工兜底，
两者不再混用“release gate”一个词。

## 本轮收口了什么

1. 包版本、`grandquiz.__version__`、uv lock 和 OpenAPI 统一到 `0.4.0`。
2. README、产品边界、路线图、RC 指南、Release Notes 和独立发布清单统一描述 v0.3/v0.4 已交付能力。
3. `.env.example` 保留 DeepSeek/DashScope dialect 与 thinking 配置，但公共默认模型使用稳定的
   `deepseek-chat`；实验模型只留在审计记录和显式 CLI override 中。
4. 新增 v0.2.0 schema v15 → v0.4.0 schema v16 兼容测试：用真实旧表形状写入资源、KnowledgeItem、
   Evidence 与薄弱状态，再只通过公开 `LearningPersistence` 打开；迁移后全部可读，新 inbox 表存在。
5. 新增桌面/移动端申诉 Scenario：先用无关答案得到“错”，再补充 exact 机制得到“对”，并确认原回答文本
   没有被覆盖。
6. Web fixture 的题目加入稳定场景编号，并固定提供最高难度所需的 6 个选项；避免两个 viewport 共用
   SQLite 后，历史去重或已累积的难度状态把固定输出耗尽。
7. 双轴审查发现最终题完成后再申诉时，重判仍挂在已经闭合的 Assessment span 下，且关闭页面无法取消
   appeal task。现改为每次申诉独立开启/闭合根 span，Trace 可见 running/resolved/failed/cancelled；临时
   Provider 失败可用同一冻结命令安全重试，取消后不会继续写 Verdict Correction 或学习状态。

## 本地验证证据

- Python：`1036 passed`；
- 离线 Eval：`17/17`；
- Ruff lint、format，Pyright，import-linter：通过；
- Web lint、typecheck、unit：`50 passed`；Sites worker：`4 passed`；production package build：通过；
- Playwright：完整桌面/移动端 `20/20`，包含申诉、取消、发现、Eval Snapshot、Chat 与安全 Markdown；
- v0.4.0 sdist/wheel：构建成功；wheel 仓库外安装成功；CLI help、安装包内离线 Eval 17/17、FastAPI
  health、Web 首页和 SPA fallback 均通过；
- wheel SHA-256：`894ea024540135538f62f8130fdc272dc60c3bf7b33dd4e999813192597c394f`。

wheel 不包含本记录，因此该哈希可复现。sdist 会包含源代码文档，若把自己的哈希写回本文会形成自引用并
再次改变哈希；其最终 SHA-256 应随 GitHub Release asset/CI manifest 发布，不写回源树。正式 Release
仍必须从最终 release commit 或 CI 重新构建，不能把本地临时文件直接当权威产物。

## 仍未执行的外部动作

- 未调用真实 LLM，因本轮没有新的费用/数据外发授权；
- 未 push、未创建 tag、未创建 GitHub Release、未上传 PyPI；
- `.scratch` 中的 PRD/issues、临时构建目录和 Playwright artifacts 均不进入提交。

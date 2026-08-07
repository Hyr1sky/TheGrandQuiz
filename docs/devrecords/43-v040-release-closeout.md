# v0.4.0 发布收口：把“软件可发布”和“模型策略过门”分开

日期：2026-08-07
状态：本地 release candidate 已完成；等待 release commit 后的双轴审查、GitHub Actions、人工 dogfood 与
owner 对 push/tag/Release 的单独批准

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
6. Web fixture 的题目加入稳定场景编号，避免两个 viewport 共用 SQLite 时，历史题目去重把固定四题耗尽。

## 本地验证证据

- Python：`1034 passed`；
- 离线 Eval：`17/17`；
- Ruff lint、format，Pyright，import-linter：通过；
- Web lint、typecheck、unit：`49 passed`；Sites worker：`4 passed`；production package build：通过；
- Playwright：申诉定向 `2/2`、取消回归 `2/2`；完整 `20/20` 留到 release commit 后做最终确认；
- v0.4.0 sdist/wheel：构建成功；wheel 仓库外安装成功；CLI help、安装包内离线 Eval 17/17、FastAPI
  health、Web 首页和 SPA fallback 均通过；
- sdist SHA-256：`aaf4505e1a97c19b57ae9d088dc6b5e8d3de715db4951476f518d13ccbfea12d`；
- wheel SHA-256：`894ea024540135538f62f8130fdc272dc60c3bf7b33dd4e999813192597c394f`。

这些哈希只对应当前未提交工作区的本地候选，用于证明打包形状；正式 GitHub Release asset 必须从最终
release commit 或 CI 重新构建，不能把本地临时文件直接当权威产物。

## 仍未执行的外部动作

- 未调用真实 LLM，因本轮没有新的费用/数据外发授权；
- 未 push、未创建 tag、未创建 GitHub Release、未上传 PyPI；
- `.scratch` 中的 PRD/issues、临时构建目录和 Playwright artifacts 均不进入提交。

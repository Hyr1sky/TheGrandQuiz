# PRD：v0.1.0 可分发 RC

Status: in progress
Triage: ready-for-agent

## Goal

把已经通过功能 RC 的本地 `main` 收口为其他技术用户可以从 GitHub 获取、安装、理解并安全试用的
local-first CLI + Web 候选版。发布工程只修复分发、文档、隐私和干净环境复现问题，不增加学习功能。

## Product promise

v0.1.0 承诺本地材料 ingest、精确 Evidence、当前材料 Chat、逐题考核、Learning Memory、trace/replay/eval
与 Local Web 阅读/考核/观测。Web Acquisition/可恢复审批、资源管理、知识点管理和学习轨迹留到 v0.1.0 后。

## Required outcomes

1. wheel/sdist 包含公开 CLI 的全部运行依赖与 Eval 资产；仓库外 `grandquiz report` 离线 17/17。
2. README 提供从 clone 到首次 ingest/react/quiz/Web 的最短路径，并明确能力边界。
3. README/SECURITY 对外部 LLM、Web untrusted、learning.db/trace.db、备份与清除给出一致说明。
4. CONTRIBUTING 与 issue/PR 模板固定五道门、trace_id、cassette 和密钥纪律。
5. CI 构建产物并在仓库外安装 smoke；不依赖 `.env`、真实 API、Docker 或生产 DB。
6. LICENSE、package metadata 与来源说明在仓库所有者选择许可证后闭合。
7. 自动门与两轮人工 dogfood 通过后，才准备 RC tag 和 GitHub Pre-release。

## Non-goals

- 不实现 LW-S5 Acquisition/可恢复审批或 LW-S6 管理/统计。
- 不发布到 PyPI。
- 不创建 tag、GitHub Release 或推送远程，除非仓库所有者再次明确批准。
- 不把内部 Runtime 宣称为稳定 SDK 或框架 API。

## Release order

```text
scope freeze
→ package resources / installed-wheel smoke
→ Quickstart / privacy / contribution docs
→ artifact CI
→ owner dogfood
→ private RC
→ blocker-only fixes
→ v0.1.0
```

## Human gates

- 许可证由仓库所有者选择：MIT 或 Apache-2.0。
- 真实模型、真实 Web Fetch 与生产 DB dogfood 需仓库所有者显式执行或授权。
- RC/final tag 与 GitHub Release 需最终批准。

# EJ-S3 — 双 Tier HTML 与 CLI 可发现性

Status: done（2026-07-19；双 Tier HTML、质量详情/judge trace 与 CLI examples 完成）
Type: AFK

## Parent

[PRD：Tier-2 LLM Grader 与质量评测闭环](../PRD.md)

## What to build

把规则门和质量门投影到现有自包含 Eval HTML：索引页可区分 rule fail、quality fail、pass，详情展示 rubric/prompt、维度分数、理由、逐字依据与 judge trace/cost。同时让 `report --help` 和 `trace --help` 提供可复制的生成与浏览器打开示例。

覆盖 PRD User Stories：16–18、20、22–25、27。

## Acceptance criteria

- [x] HTML 首页分列 Rule、Quality、execution tokens、judge tokens、rubric，并支持状态筛选
- [x] 未启用 Tier-2 的用例明确显示 N/A，不被误判成 quality pass 或 fail
- [x] case15 详情显示每维分数、理由、candidate/reference 依据与 judge prompt version
- [x] subject trace 继续复用现有 renderer；judge trace/quality section 不复制一套事件渲染实现
- [x] 所有动态文本正确 HTML escape，报告继续自包含、无外部脚本/样式请求
- [x] `grandquiz report --help` 说明默认 index 路径、`--out` 和 `open` 示例
- [x] `grandquiz trace --help` 说明 trace id、默认 trace DB/output 和 `open` 示例
- [x] 默认 report 明确为 Replay/零网络，不从 `.env` 隐式调用真实 judge

## Blocked by

- [EJ-S2](02-grounded-answer-quality-gate.md)

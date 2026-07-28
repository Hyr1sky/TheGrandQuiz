# PRD：v0.1.0 功能 RC 收口

Status: done
Triage: ready-for-human

## Goal

不新增产品能力，消除 Local Web 的三个 P1 风险，恢复确定性浏览器验收链，并让
`origin/main...HEAD` 达到可标记 `v0.1.0-rc` 的功能质量。

## Required outcomes

1. 所有动态 Markdown 只经一个安全 renderer；不可信内容不能自动加载图片。
2. Assessment trace 可靠闭合为 completed / failed / cancelled，失败进入错误计数。
3. Chat 单 session 同时只允许一个 turn；被接受 turn 的 exact resource scope 不被后续请求覆盖。
4. 浏览器只消费有限、稳定的 trace event/span 类别；TraceStore 重启后仍可恢复历史观测。
5. 确定性测试矩阵覆盖安全 Markdown、两轮 cursor、exact scope、Chat → Assessment、SSE resume 与重复动作。
6. 已批准的三栏 Chat / Anthropic 字体方向写回视觉规范，实现只使用 token shadow 与图标库。

## Non-goals

- 不实现 Acquisition / 可恢复审批。
- 不增加管理、统计、知识图谱或新学习功能。
- 不引入 LLM 测试员；浏览器 Bot 使用 scripted provider + 临时 SQLite。
- 不改变核心考核 workflow、prompt、cassette 或数据库 schema。

## Quality gate

Python / Web / Eval / OpenAPI / build / Playwright 全绿，随后对
`origin/main...HEAD` 执行 Standards + Spec 双轴审查。

## Closeout evidence

- Python：894 passed；Eval：17/17。
- Web：37 passed；typecheck / build / OpenAPI generation 全绿。
- Playwright：桌面 + 移动端 8/8；失败自动保留 screenshot / trace / HTML。
- 临时 SQLite：10 条 trace 的 sequence 连续，Chat turn 与 Assessment run 全部成对。

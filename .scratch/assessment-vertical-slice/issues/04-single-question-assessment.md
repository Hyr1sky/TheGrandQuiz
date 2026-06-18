# M3.2 — 单题考核竖切：考我 → 出题 → 判卷

Status: ready-for-agent
Type: AFK

## Parent

[PRD: 考核竖切 MVP](../PRD.md)

## What to build

考核竖切的后半段——逐题交互的拷问。"考我"触发；代码从知识库选一个 KnowledgeItem（本阶段可简单选，尚不依赖薄弱记忆）；`generate_quiz` 工具产出 grounded 题（锚定该 item，pydantic schema），经**运行时校验门**（锚定的 KnowledgeItem 存在 + evidence 非空，不达标 ModelRetry / 拦截）才展示；用户答；`grade_answer` 工具产出判决（对 / 勉强 / 错 + 指认的 weak_item_id + 所引 evidence；选择题确定性比对，开放问答 LLM 判且必须引 evidence）；显示判决。空库时拒绝出题并引导喂资源。可随时中止，半成品落 trace。发 AnswerJudged 领域事件。

LLM 仅在"出题""判卷"两槽被调用（ADR-0004）。

## Acceptance criteria

- [ ] "考我"触发逐题交互循环（出题 → 答 → 判决 → 下一题）
- [ ] 空库时拒绝出题并引导先喂资源（eval case 2）
- [ ] 出的每道题锚定存在的 KnowledgeItem 且 evidence 非空——运行时校验门挡幽灵题（eval case 3）
- [ ] `grade_answer` 产出结构化判决（三值 + weak_item_id + cited_evidence）
- [ ] 发 AnswerJudged 领域事件，进 trace
- [ ] 可中止会话，半成品状态落 trace
- [ ] 出题 / 判卷走 Replay 验证（缝 1 + 3），不 unit-TDD
- [ ] eval case 2、3 在事件 / trace 流上可断言
- [ ] CI 全绿

## Blocked by

- [03 — Ingest 竖切](03-ingest-slice.md)

# EJ-S2 — 自然材料回答 Tier-2 质量门

Status: ready-for-agent
Type: AFK

## Parent

[PRD：Tier-2 LLM Grader 与质量评测闭环](../PRD.md)

## What to build

让 case15 在既有 Tier-1 exact grounding/cost 门之后，再用已校准的 `grounded_answer` rubric 评最终用户可见回答的 semantic support、question coverage 与 learning usefulness。质量投影只提供自然问题、最终回答和最小参考证据；subject trace 与 judge trace 分离，execution 与 judge 成本分列。

覆盖 PRD User Stories：1、4–7、16–21、27、29–31。

## Acceptance criteria

- [ ] Case DSL 可选声明 quality profile；未声明的既有 14 条用例零 judge 调用且 Tier-1 行为不变
- [ ] Solver 暴露最终用户可见回答，case15 quality 投影只含问题、candidate 与测试内置 reference
- [ ] case15 Tier-1 继续独立验证 selected scope、search→read→citation、逐字 span、调用/token/read 门
- [ ] grounded_answer rubric 评 semantic support、question coverage、learning usefulness，并由代码算 pass
- [ ] subject events/expected sequence 不包含 judge 事件；judge 使用独立事件流与 span
- [ ] CaseReport 分离 rule/quality verdict、execution/judge tokens、subject/judge prompt versions
- [ ] quality-enabled case 任一 Tier 失败都总失败；Replay miss、校准失败和 judge 结构错误均为质量硬失败
- [ ] 反证证明 judge 自报 pass、伪依据、缺调用或把 judge token 混入 execution token 均不能通过

## Blocked by

- [EJ-S1](01-calibrated-quality-judge.md)

# LW-S4 — 逐题考核与 Evidence reveal

Status: blocked
Type: AFK

## Parent

[PRD：Local-first Web 学习工作台](../PRD.md)

## What to build

把 `AssessmentSession` 的逐题 workflow 投影成 Web 交互：出题、作答、判决、追问/正解、下一题和会话总结。
与题目相关的材料证据默认以玻璃遮罩隐藏，用户主动或悬停揭示时记录可观察动作，以后可区分独立答对与
查看材料后答对。

## Acceptance criteria

- [ ] LLM 只在既有出题/判卷槽中，状态转移和记账仍由代码执行
- [ ] 一次只展示一道题，选择题与开放题均能提交
- [ ] Evidence reveal 不修改原文，不绕过 citation 校验，并产生可审计事件
- [ ] 判决展示对/勉强/错、证据和薄弱状态变化，不引入分数/掌握度
- [ ] 刷新或短暂断线不会静默重复记账
- [ ] fake/replay provider 下完成端到端测试

## Blocked by

LW-S3.

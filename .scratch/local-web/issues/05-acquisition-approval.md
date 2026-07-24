# LW-S5 — Web Acquisition 与可恢复审批

Status: blocked
Type: HITL

## Parent

[PRD：Local-first Web 学习工作台](../PRD.md)

## What to build

把已交付 WA-S1–S5 的 Search → 用户选择 → Fetch → Reader 投影到 Web，并补齐真正可挂起/恢复的审批
run。审批请求持久化候选摘要与 token；用户筛选 KnowledgeItem 后恢复同一 run 并原子提交，不用保持一个
FastAPI request 阻塞。

## Acceptance criteria

- [ ] Search 只返回候选，用户选择后才 Fetch/Reader
- [ ] 质量失败零 Reader/审批/KB 污染
- [ ] 审批 run 进入 needs_input，重启服务后仍可查询和恢复
- [ ] token 单次使用、过期/重复/篡改 fail closed
- [ ] 用户筛选结果与最终原子 snapshot 可由 trace 审计
- [ ] UI 不显示或保存 provider secret
- [ ] 用户用真实材料完成一次 dogfood 并提供 trace_id

## Blocked by

LW-S3。真实外部 LLM、Web Fetch 和生产 DB 写入需用户在验收阶段授权。

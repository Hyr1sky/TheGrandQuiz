# SH-S9 — 真实 KnowledgeItem 审批门

Status: implementation-done / HITL terminal pending
Type: HITL

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

让生产 CLI 在 Reader 候选入库前真正展示并筛选 KnowledgeItem，结束固定 keep-all。第一步交付可用的阻塞
CLI 审批；持久 suspend/resume 作为后续独立竖切，不再声称现有同步协议已经具备该能力。

## Acceptance criteria

- [x] CLI 展示候选概念、摘要、证据与置信度，并允许逐项保留 / 剔除
- [x] 未获批 item 不进入知识快照；全部拒绝有明确结果
- [x] 用户取消审批不覆盖已有快照、不留下 pending 半状态
- [x] 审批请求与最终决策进入事件脊柱，trace 不泄露额外敏感内容
- [x] scripted adapter 继续服务确定性测试，真实 CLI 不再 keep-all
- [x] 文档诚实区分已交付阻塞审批与未交付 suspend/resume
- [ ] 真机交互验收与五门通过

## Blocked by

- [SH-S1](02-stable-resource-snapshot.md)

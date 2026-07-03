# M3.1 — Ingest 竖切：喂 URL → 深读 → 审批 → 入库

Status: ready-for-agent
Type: AFK

## Parent

[PRD: 考核竖切 MVP](../PRD.md)

## What to build

考核竖切的前半段——把材料变成可考核的知识库。建 LearningTask（学习主题容器 / 考核范围）；手动喂一个 URL（mock 资源源；fetch 层做大小 / 超时 / 域名限制，抓回内容打"不可信"标记）；Reader subagent 深读产出 KnowledgeItem 候选（概念名 + 摘要 + 证据 + 置信度，pydantic schema 强制校验、失败自动 ModelRetry），**证据为 `{quote, locator|None}` 结构**（locator 携 section_path/锚点，MVP 可留 None，但字段与形状第一天就在）；LearningResource **持久化原始抓取内容（或 blob + content_hash）**，使日后回填出处定位符 / 构建资源内概念树无需重抓（真实 URL 会腐烂）；审批门展示候选清单预览，用户剔除垃圾 item 后才入库（审批 = 可挂起 / 可恢复的 turn：发 ApprovalRequested 事件 + 持久化待决状态，CLI 可用阻塞 prompt 实现）；入库发 ResourceApproved / ItemCreated 领域事件。深读 fetch 失败 → 资源标记失败、不产生幽灵 item。

遵循 ADR-0002（KnowledgeItem 资源内唯一，不跨资源归并）。Reader 是 MVP 唯一 subagent。

## Acceptance criteria

- [ ] 能创建 LearningTask 并喂入一个 URL（mock 资源源）
- [ ] Reader subagent 产出符合 pydantic schema 的 KnowledgeItem 候选；schema 不符自动重试
- [ ] KnowledgeItem 证据为 `{quote, locator|None}` 结构（locator MVP 可为 None，字段/形状先在）
- [ ] LearningResource 持久化原始抓取内容（或 blob + content_hash），出处可事后回填、不必重抓
- [ ] 审批门展示候选预览；未审批的 KnowledgeItem 不得入库（eval case 1）
- [ ] 用户剔除后剩余 item 入库，发 ItemCreated 事件
- [ ] 审批以 ApprovalRequested 事件 + 待决状态实现（接口形状按 suspend/resume）
- [ ] 深读 fetch 失败 → 资源标记失败、不产生幽灵 KnowledgeItem（eval case 7）
- [ ] fetch 层有大小 / 超时 / 域名限制，抓回内容打"不可信"标记
- [ ] eval case 1、7 在事件 / trace 流上可断言（缝 1）
- [ ] CI 全绿

## Blocked by

- [02 — TraceStore + Replay Provider](02-tracestore-and-replay.md)

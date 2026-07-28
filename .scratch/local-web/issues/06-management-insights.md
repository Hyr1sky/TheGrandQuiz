# LW-S6 — 资源、知识点、设置与学习轨迹

Status: post-v0.1 backlog
Type: AFK

## Parent

[PRD：Local-first Web 学习工作台](../PRD.md)

## What to build

补齐 v0.1.0 所需的管理与理解入口：文章/revision/KnowledgeItem 浏览，安全的资源操作，薄弱→观察中→
销账轨迹，provider 配置状态和数据备份说明。它是领域行为和 trace 的投影，不是数据库表 dashboard。

## Acceptance criteria

- [ ] 文章、current/history revision、KnowledgeItem 与 Evidence 关系可读
- [ ] 破坏性操作明确展示影响并要求确认，不静默删除历史 trace/revision
- [ ] 学习轨迹使用三态状态机和 ActivityEvent，不制造连续掌握度分数
- [ ] 设置只显示 secret 是否存在，不经 API 回传 secret value
- [ ] 页面明确 learning.db/trace.db 位置、备份、删除和外部 LLM 数据边界
- [ ] 查询数量有界，真实生产规模下无明显 N+1

## Blocked by

LW-S3 and LW-S4.

本项不阻塞首次 v0.1.0；管理与学习轨迹只在真实 RC 反馈证明优先级后进入下一轮。

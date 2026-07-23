# AD-S4 — 对齐当前态领域语言与生产文案

Status: ready-for-agent
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

清理权威文档、生产 docstring 与用户可见 CLI 文案中剩余的 LearningTask、内容寻址、过期 Web Acquisition
状态与其他已被 ADR-0005/0007/0008 取代的描述。历史 ADR、devrecords 和已完成 PRD 保留原始时间语境。

## Acceptance criteria

- [ ] 当前态文档统一使用 locator-addressed ResourceRevision 语义
- [ ] 生产 docstring 不再把 URL 称为内容身份或把已交付 Web Fetch 写成未交付
- [ ] 用户可见 ingest 文案不再把已消解的 LearningTask 称为“任务”
- [ ] 不机械改写历史 ADR、devrecords 或已完成 PRD
- [ ] 增加聚焦当前态文件的术语回归测试
- [ ] 无运行时 workflow、prompt、tool schema 或 cassette 变化

## Blocked by

None - can start immediately


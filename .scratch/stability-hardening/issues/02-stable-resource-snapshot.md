# SH-S1 — 稳定资源身份与原子知识快照

Status: done
Type: HITL

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

落实 ADR-0007：同名本地文件不碰撞，KnowledgeItem 身份不受 Reader 顺序影响；重 ingest 只在 Reader 与
审批成功后原子替换该资源的完整知识快照，并清理已移除 item 的关联学习状态。建立后续判决原子提交复用
的 transaction seam。

## Acceptance criteria

- [x] 不同目录同名文件得到不同 resource_id，同一路径重 ingest 保持同一 resource_id
- [x] Reader 候选重排不改变同一概念证据的 item_id，重复概念指纹触发重试
- [x] 重 ingest 候选减少后旧尾项与其薄弱账、已问题目、难度全部删除
- [x] 重 ingest fetch / Reader / 审批前失败时旧获批快照保持可用
- [x] resource、item、关联账外键与 upsert 不会因 delete-then-insert 误清保留状态
- [x] Dict / SQLite 对快照替换结果 parity；迁移幂等
- [x] 清理真实 learning DB 前完成带日期备份并验证可打开；3 份真实材料重建为 88 个 item
- [x] 五门全绿，受影响 cassette 清单明确

## Blocked by

- [SH-S0](01-authoritative-doc-baseline.md)

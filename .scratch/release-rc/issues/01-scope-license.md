# RC-S1 — 发布范围、许可证与来源审计

Status: done
Type: HITL

## Acceptance criteria

- [x] v0.1.0 能力与限制写入 PRD，LW-S5/LW-S6 不再阻塞首次发布
- [x] 仓库所有者选择 MIT 或 Apache-2.0
- [x] 审计旧仓库来源、依赖、cassette 与测试材料的再分发权
- [x] LICENSE、README 与 package metadata 一致

## Decision and evidence

- 2026-07-28 仓库所有者明确选择 MIT；
- 当前仓库 git 历史作者均为 Hyr1sky；ADR-0001 明确旧仓库为作者自己的冻结参考实现；
- 旧仓库公开 URL 当前不可访问，因此再分发权以仓库所有者声明与本仓库可审计迁移记录为依据；
- Python 直接依赖为 MIT / BSD / Apache-2.0，Web lock 中未发现强 copyleft 依赖；
- cassette 仅含模型响应、短合成材料和有界搜索摘要，不含完整第三方文章或凭证；
- wheel 保留 React license comments 与 d3-celestial 独立许可证文件。

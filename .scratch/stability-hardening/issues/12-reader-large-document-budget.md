# SH-S11 — 长文 Reader 预算内分块

Status: done
Type: bugfix

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

真实 DB 重建时，Agentic-RL 材料在 Reader 单次请求中被 S7 的 32k Provider 硬门以
`47,556 > 32,000` 拒绝。硬门行为正确；Reader 作为“隔离长上下文”的唯一 subagent 必须在门内做
确定性 map/reduce，而不是提高上限或绕过预算。

## Acceptance criteria

- [x] 先有回归测试复现长材料在审批 / 写库前被完整请求预算门拒绝
- [x] Reader 按注入式确定性 token 估算切块，不丢失、不重叠正文，优先在段落边界断开
- [x] 每个片段沿用既有结构化输出校验、有界重试与 `MODEL_STARTED/ENDED` span
- [x] 聚合由代码完成：多数片段主题作为资源主题；相同稳定 item ID 保留首次出现者
- [x] 短材料 messages 逐字不变，既有 Reader cassette 无意义重录为零
- [x] 32k Provider fail-closed 门保持原值，Reader 单片预算 16k，二者职责分离
- [x] Ruff、format、Pyright、import-linter、全量 pytest 全绿（`721 passed`）
- [x] 真实 Agentic-RL 深读产生 3 对成功 model span 和 30 个候选；取消审批后生产库仍为零行
- [x] keep/reject 后写入生产 DB，并完成三份真实材料重建（3 个资源 / 88 个 item）

## Residual risk

- 分块会放大相邻片段的同名概念和文末练习题误抽取；当前由真实审批门剔除。跨片段按概念语义合并、
  对“练习题/思考题”段落的抽取策略属于后续 Reader 质量迭代，不在本次预算 bugfix 中暗改身份规则。

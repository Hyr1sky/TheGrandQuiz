# GAS-S3 — 自然 ReAct 路由与成本门

Status: ready-for-agent
Type: AFK

## Parent

[PRD：自然材料问答与 Agentic Search 成本收口](../PRD.md)

## What to build

把 `GroundedDocumentAnswer` 以单个高层 learning 工具暴露给开放 ReAct，并调整路由契约，使普通“根据材料回答并给
出处”的自然问题无需工具名即可优先调用它。外层模型直接转述模块已验证的答案/citations 或结构化拒绝；既有
outline/search/expand/read/cite 原子工具继续服务复杂探索。以 GAS-S1 契约阻止模型调用和 prompt 历史重新膨胀。

覆盖 PRD User Stories：1–5、7–13、20–25、27。

## Acceptance criteria

- [ ] learning tool registry 注册一个参数有界的高层 grounded-answer 工具，并继续注册全部既有原子文档工具
- [ ] ReAct prompt 明确自然材料问答优先走高层工具，不要求用户知道或点名工具
- [ ] 高层工具与直接模块调用共享同一实现和 grounding 结果，不复制 workflow
- [ ] 高层工具成功输出包含可直接转述的答案、可读 section_path 和已验证 citations
- [ ] 高层工具失败输出保留 invalid scope、no evidence、budget exhausted 等状态，外层不得补写无依据答案
- [ ] 最终回答不能把 node id、search excerpt 或未读 KnowledgeItem quote 冒充 node citation
- [ ] 自然 eval 只使用 exact selected scope，并满足 search → read → citation 事件顺序
- [ ] 自然 eval 达到 model calls ≤4、累计 tokens ≤45,000、读取占比 ≤25%、exact citations ≥1
- [ ] prompt/tool result 投影不会随内部搜索步骤形成不断增长的外层工具历史
- [ ] case14 等受 tool schema/prompt 指纹影响的 Replay 明确进入真实重录清单
- [ ] 若组合 workflow 已达成本门，不实现通用工具历史压缩器

## Blocked by

- [GAS-S2](02-grounded-document-answer.md)

# GAS-S2 — GroundedDocumentAnswer 有界 workflow

Status: ready-for-agent
Type: AFK

## Parent

[PRD：自然材料问答与 Agentic Search 成本收口](../PRD.md)

## What to build

交付 `GroundedDocumentAnswer` 深模块：以 query、exact resource scope 和显式预算为输入，在既有 Document Structure
能力上确定性完成候选搜索与有界读取，只用一个结构化 LLM 槽选择已读证据并组织答案，再由代码验证逐字 quote/span
和渲染 citation。模块返回答案、已验证 citations、搜索/读取节点、usage 与稳定状态，可由应用代码直接调用，也可
由后续 ReAct 高层工具复用。

覆盖 PRD User Stories：2–6、8–10、12–22、26–28。

## Acceptance criteria

- [ ] 公共输入契约包含 query、非空 exact resource ids、候选上限、读取预算和模型预算
- [ ] 公共输出契约包含 answer、verified citations、searched/read nodes、scope、usage/read metrics 和稳定状态
- [ ] workflow 固定执行 exact scope → FTS candidates → bounded reads → structured answer → code validation
- [ ] 模型只接收本次已读 evidence windows，不决定 resource/revision/node 身份，不运行自由工具循环
- [ ] 成功路径只需一次回答模型调用；结构化输出失败最多按既有 recovery 契约进行一次有界重试
- [ ] 每条 citation 均满足 current revision、read-before-cite、窗口内唯一逐字 quote 和合法 span
- [ ] invalid scope 在搜索前拒绝且零读取；no evidence、budget exhausted、ambiguous quote 均 fail closed
- [ ] 候选数、单节点读取量、总读取量和模型 tokens 受代码预算控制，正文保持 untrusted 标记
- [ ] 复用既有 FTS、DocumentNode read 和 citation resolver，不复制索引或建立新 SQLite schema
- [ ] 搜索、读取、内部模型调用、citation 成功/拒绝与 workflow 汇总事件进入同一事件脊柱
- [ ] fake provider + SQLite fixture 覆盖成功、无证据、无效 scope、预算、注入文本、重复 quote、越界 span 和重试
- [ ] kernel 不 import learning domain，quiz workflow、Reader ingest 和既有原子工具行为无回归

## Blocked by

- [GAS-S1](01-natural-answer-baseline.md)

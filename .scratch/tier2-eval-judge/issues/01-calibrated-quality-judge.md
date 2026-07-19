# EJ-S1 — 校准优先的 QualityJudge

Status: ready-for-agent
Type: AFK

## Parent

[PRD：Tier-2 LLM Grader 与质量评测闭环](../PRD.md)

## What to build

交付一个可独立运行的 Eval QualityJudge tracer bullet：受信任 rubric registry、结构化四档维度判定、逐字审计依据、独立事件 span、有限重试，以及一组人工标注的合成 calibration samples。judge 只有在所有阻断性维度落入人工区间后才被视为可用于质量门。

覆盖 PRD User Stories：2–3、8–15、20–21、26–29、32。

## Acceptance criteria

- [ ] rubric 只能按预注册 id 选择，criteria、评分锚点和门限不来自任意 YAML prompt
- [ ] QualityJudge 返回每个 criterion 恰好一次的 1..4 分、理由和 candidate/reference 逐字依据
- [ ] 缺失、重复、未知 criterion、越界分数、空理由或伪造依据均有限重试并 fail closed
- [ ] judge started/ended、模型 span、prompt version、usage 和失败 fingerprint 进入独立 AgentEvent 流
- [ ] calibration set 覆盖 fully supported、partially supported、unsupported embellishment、justified refusal
- [ ] calibration runner 以人工分数区间判一致，任何阻断性分歧使 gate 失败
- [ ] scripted fake 只测契约；真实语义能力必须由后续真实 Replay + calibration 证明
- [ ] kernel/domain 生产 workflow 零改动，现有干扰项 judge 行为不变

## Blocked by

None - can start immediately.

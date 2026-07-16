# SH-S2 — 考核 scope 三态与未解析拒答

Status: ready-for-agent
Type: AFK

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

把考核范围从含混的可空 ID 列表收敛为必填的 all / selected / unresolved 三态。用户点名材料但无法从目录
解析时必须在选题前诚实拒答，不能静默扩大为全库。

## Acceptance criteria

- [ ] all、selected、unresolved 三态及字段组合由结构化校验保证
- [ ] all 可考全库，selected 只考指定资源
- [ ] unresolved 即使 KB 非空也零出题、零判卷、零记忆写入
- [ ] selected 指向不存在资源仍保留 empty_scope 语义
- [ ] scope mode、请求标签、ID 与命中数进入事件脊柱
- [ ] 工具遗漏必填 scope 或给出非法组合时走 ModelRetry，不进入 workflow
- [ ] ReAct 轨迹覆盖“不存在材料”与“随便考我”两种相反意图
- [ ] 五门全绿，旧工具 cassette 明确进入重录清单

## Blocked by

- [SH-S0](01-authoritative-doc-baseline.md)

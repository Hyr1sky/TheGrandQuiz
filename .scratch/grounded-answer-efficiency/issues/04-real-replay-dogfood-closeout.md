# GAS-S4 — 真实 Replay、生产 dogfood 与收口

Status: done（2026-07-19；真实 Replay、生产 dogfood、联合审计、五门与 git 收口全部通过）
Type: HITL

## Parent

[PRD：自然材料问答与 Agentic Search 成本收口](../PRD.md)

## What to build

用授权的外部 LLM 服务真实重录 tool schema/prompt 变化影响的 cassette，并录制新的自然 grounded-answer case；随后
对生产 current revision 执行一次不点名工具的材料问答，通过 learning.db + trace.db 联合审计 exact scope、受限读取、
精确引用和成本门。最后完成全量质量门、开发记录、issue/PRD 回填与 conventional git 历史。

覆盖 PRD User Stories：1–13、21–28。

## Acceptance criteria

- [x] case14 及其他执行指纹受影响的 cassette 由真实模型重录，不手工修改 provider 输出或 fingerprint
- [x] 新自然问答 cassette 的用户消息不包含工具名，并使用测试内置合成 KB/上下文
- [x] Replay 稳定通过 exact selected scope、search → read → citation、read-before-cite 和逐字证据断言
- [x] Replay 达到 model calls ≤4、累计 tokens ≤45,000、读取占比 ≤25%、exact citations ≥1
- [x] 生产 dogfood 使用普通自然问题，成功返回至少一条 current revision exact node citation
- [x] 只读联合审计从 learning.db 与 trace.db 复算 scope、事件顺序、quote/span、预算、调用数和 token 指标
- [x] 真实失败若暴露新边界，先新增最小回归再修复，不放宽 grounding 或预算门
- [x] Ruff check、Ruff format check、Pyright、import-linter、全量 pytest 与 Tier-1 eval 全绿
- [x] PRD 与 GAS-S1–S4 回填完成证据和状态，DS-S5 继续关闭
- [x] 开发日志落盘到 docs/devrecords，说明基线、实现、测试、真实 trace、成本变化和剩余限制
- [x] 改动按可验收 slice 形成 conventional commits，main 工作树干净并推送到 origin

## Current evidence

- 真实 case15：3 model calls / 10,282 tokens / max prompt 4,962 / 1 bounded leaf read / 1 exact citation。
- 静态四门与全量 pytest `784 passed`；规划 `e44ca6c`、实现 `103ab1c`、边界测试 `cfc4118`。
- 生产 trace `01c64e58ba9949368a06a4693bc5ec26`：自然询问 Agent Memory 中的 Mem0 流程，1 个高层工具、
  3 model calls / 12,125 tokens / 1 search / 3 reads / 1 exact citation。
- 与 ingest trace `2515ec1af79a4a0a9860993b4a35beb9` 的只读联合审计 `passed=true`：exact selected scope，
  current revision `a37bdcb799210246`，read-before-cite，读取 2,044 / 20,721 字（9.86%）。
- 先前 trace `aafd099e305846fda9a484176156141a` 的“事件信封 / Agent Event”问题在该材料中没有逐字依据，
  `no_evidence` 属于正确 fail-closed，不作为成功路径证据；它也暴露出失败后外层原子工具回退仍可能增加成本，
  但未出现常识补写或伪 citation，不改变本 slice 的成功路径验收结论。

## Blocked by

- [GAS-S3](03-react-routing-cost-gate.md)

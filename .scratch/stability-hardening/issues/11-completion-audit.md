# SH-S10 — 稳定性加固完成审计

Status: HITL closing
Type: HITL

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

按 PRD 的每条显式要求逐项收集当前代码、测试、trace、cassette、数据库和真机行为证据；完成备份后清库
重建与真实 dogfood，并把所有权威文档收口到相同事实。

## Acceptance criteria

- [x] S1-S9 与真机暴露的 S11 每条 acceptance criterion 有直接证据，不以“未发现问题”代替证明
- [ ] learning DB 备份可打开，新库从真实材料重建并完成考核闭环
- [x] 全部受影响 cassette 已重录或明确废弃，无旧工具契约假绿
- [x] Ruff、format、Pyright、import-linter、全量 pytest 全绿
- [ ] 全部 eval 与关键真机 trace 通过，成本 / token /错误信息完整
- [x] README、CONTEXT、architecture、ADR、PRD、issue、skeleton ledger 状态一致
- [x] 残余风险和明确 Out of Scope 形成最终报告

## Blocked by

- [SH-S1](02-stable-resource-snapshot.md)
- [SH-S2](03-fail-closed-quiz-scope.md)
- [SH-S3](04-streaming-web-fetch.md)
- [SH-S4](05-replay-execution-fingerprint.md)
- [SH-S5](06-atomic-learning-state.md)
- [SH-S6](07-durable-trace-processor.md)
- [SH-S7](08-provider-request-budget.md)
- [SH-S8](09-direct-correct-difficulty.md)
- [SH-S9](10-real-approval-gate.md)
- [SH-S11](12-reader-large-document-budget.md)

## Audit snapshot (2026-07-17)

### Code and deterministic evidence

| Slice | Evidence | State |
| --- | --- | --- |
| S1 | stable local locator / item fingerprint tests; Dict/SQLite snapshot parity; FK cascade; migration 0007; real backup/rebuild | done |
| S2 | discriminated `QuizScope`; unresolved/empty scope and tool validation tests; real case14 replay | done |
| S3 | async stream tests prove decompressed byte cutoff stops later reads; SSRF/redirect/error taxonomy tests | done |
| S4 | v2 tool contract fingerprint tests + real assessment/case14 cassette re-recording | done |
| S5 | `LearningStateWriter` rollback injection tests for Dict/SQLite; state events emitted only after commit | done |
| S6 | durable processor failure propagation + best-effort observer isolation; CLI false-success regression test | done |
| S7 | full messages/tools request budget tests; tool-loop growth rejection; asked-history cap/context priority tests | done |
| S8 | unified evolution tests + real three-round 3→4 difficulty activation replay | done |
| S9 | `CliApprovalGate` tests + real cancel and 20/27, 21/21, 47/49 keep/reject traces | done |
| S11 | Reader deterministic 16k map/reduce under unchanged 32k Provider gate; 3 real long-resource writes | done |

Static gates are green:

```text
ruff check: pass
ruff format --check: pass (137 files)
pyright: pass (0 errors)
import-linter: pass (71 files, 255 dependencies, 1 contract kept)
```

Pytest currently collects 721 tests: `721 passed`. No cassette was forged or manually re-keyed. Real recording evidence:

1. `assess.cassette.json`: enrich 模型针对 pass@k 出题并逐字锚定证据；用闭包答案作答后 basic 判为“错”。
2. `eval_case14_bulk_quiz.cassette.json`: ReAct 只调用一次
   `start_quiz(scope=all, count=3, focus=mixed, question_type=选择题)`，三题均走受控 workflow。
3. `difficulty_activation.cassette.json`: 同一闭包 KnowledgeItem 连续三轮真实判“对”，第二轮唯一触发
   3→4 档，第三轮以高档提示继续出题；离线回放护住完整路径。

### Real database evidence

- Pre-migration backup: `~/.grandquiz/learning.db.backup-20260717-130422-pre-migration`, schema v4,
  `quick_check=ok`, 4 resources / 31 items / 1 memory / 0 preferences；SHA256 与迁移前生产库相同：
  `6596cd1d74c6957758f7710a686c75ca478490158299645597f182a4aa8637ee`。
- 三份可重建原文已按原 `content_hash` 提取到 `/private/tmp/grandquiz-rebuild-20260717-130422/`；失败且无
  raw content 的旧资源不伪造重建。
- 用户明确允许迁移后，生产 `~/.grandquiz/learning.db` 已执行 0005-0008，当前 schema v8，
  `quick_check=ok`、`foreign_key_check` 为空；ADR-0007 要求的身份不稳定旧知识表已清空。
- Agentic-RL 首次真机重建暴露 Reader 单请求 `47,556 > 32,000`；SH-S11 修复后真实 3 分块均成功，
  trace `58c017af44f241778c86545069ef4d0f` 在取消审批后以 `ingest.ended(ok=false)` 闭合，资源、item、
  memory、asked、difficulty 仍全部为 0。
- 用户批准具体筛选方案后，生产重建 trace 全部 `ingest.ended(ok=true)`：
  - Agentic-RL `1a93870dfed045089ab74988841c5393`：3 个 model span / 40,546 tokens，保留 20/27；
  - Agent Communication Protocols `0d1cc92618d8490d808aa17f146681ef`：2 个 span / 32,875 tokens，
    保留 21/21；
  - Hook As Reference `6e6a91e9342a4086a2df1686be9c3824`：4 个 span / 63,000 tokens，剔除两条
    跨片段同名重复，保留 47/49。
- 生产库最终为 schema v8、`quick_check=ok`、`foreign_key_check` 为空，3 resources / 88 items；三份
  `content_hash` 与迁移前原文一致，资源内无同名 concept、无空 evidence、无孤儿外键。

### Remaining HITL

仅剩从新库完成一次真实 quiz：该步骤会把获批 KnowledgeItem 的摘要 / 证据发给 `.env` 模型用于出题与
判卷，需用户单独授权并回答一道题；完成后才勾选“新库重建并完成考核闭环”与“关键真机 trace”。

### Residual scope

- Durable approval/answer suspend-resume with persisted pending state remains a skeleton item, not part of the delivered
  blocking CLI adapter.
- Article extraction quality, `web_search`, browser fallback and MCP adapters remain in the separate Web Acquisition PRD.
- Reader 分块可能让同名概念或文末练习题跨片重复；当前由真实审批门剔除，不在本次暗改 ADR-0007 身份。
- The stability PRD must not be marked done until the real quiz trace passes.

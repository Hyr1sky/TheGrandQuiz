# SH-S10 — 稳定性加固完成审计

Status: HITL closing
Type: HITL

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

按 PRD 的每条显式要求逐项收集当前代码、测试、trace、cassette、数据库和真机行为证据；完成备份后清库
重建与真实 dogfood，并把所有权威文档收口到相同事实。

## Acceptance criteria

- [ ] S1-S9 每条 acceptance criterion 有直接证据，不以“未发现问题”代替证明
- [ ] learning DB 备份可打开，新库从真实材料重建并完成考核闭环
- [x] 全部受影响 cassette 已重录或明确废弃，无旧工具契约假绿
- [x] Ruff、format、Pyright、import-linter、全量 pytest 全绿
- [ ] 全部 eval 与关键真机 trace 通过，成本 / token /错误信息完整
- [ ] README、CONTEXT、architecture、ADR、PRD、issue、skeleton ledger 状态一致
- [ ] 残余风险和明确 Out of Scope 形成最终报告

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

## Audit snapshot (2026-07-17)

### Code and deterministic evidence

| Slice | Evidence | State |
| --- | --- | --- |
| S1 | stable local locator / item fingerprint tests; Dict/SQLite snapshot parity; FK cascade; migration 0007 | implementation done |
| S2 | discriminated `QuizScope`; unresolved/empty scope and tool validation tests; real case14 replay | done |
| S3 | async stream tests prove decompressed byte cutoff stops later reads; SSRF/redirect/error taxonomy tests | done |
| S4 | v2 tool contract fingerprint tests + real assessment/case14 cassette re-recording | done |
| S5 | `LearningStateWriter` rollback injection tests for Dict/SQLite; state events emitted only after commit | done |
| S6 | durable processor failure propagation + best-effort observer isolation; CLI false-success regression test | done |
| S7 | full messages/tools request budget tests; tool-loop growth rejection; asked-history cap/context priority tests | done |
| S8 | unified evolution tests + real three-round 3→4 difficulty activation replay | done |
| S9 | `CliApprovalGate` full preview/select/reject/cancel tests; requested/decided events; production composition no keep-all | implementation done; terminal HITL pending |

Static gates are green:

```text
ruff check: pass
ruff format --check: pass (135 files)
pyright: pass (0 errors)
import-linter: pass (71 files, 254 dependencies, 1 contract kept)
```

Pytest currently collects 719 tests: `719 passed`. No cassette was forged or manually re-keyed. Real recording evidence:

1. `assess.cassette.json`: enrich 模型针对 pass@k 出题并逐字锚定证据；用闭包答案作答后 basic 判为“错”。
2. `eval_case14_bulk_quiz.cassette.json`: ReAct 只调用一次
   `start_quiz(scope=all, count=3, focus=mixed, question_type=选择题)`，三题均走受控 workflow。
3. `difficulty_activation.cassette.json`: 同一闭包 KnowledgeItem 连续三轮真实判“对”，第二轮唯一触发
   3→4 档，第三轮以高档提示继续出题；离线回放护住完整路径。

### Real database evidence

- Source: `~/.grandquiz/learning.db`, 532480 bytes, schema v4, `quick_check=ok`.
- Backup: `~/.grandquiz/learning.db.backup-20260717-pre-adr0007`, same size, schema v4, `quick_check=ok`.
- Both currently contain 4 resources, 31 knowledge items and 0 preferences.
- Production DB has not been migrated or cleared. Migration/rebuild remains an explicit HITL step.

### Remaining HITL

Run a real `grandquiz ingest` or `grandquiz react` ingest turn and exercise keep, reject and cancel once each. Only after
backing up again should the real learning DB be opened by the new code to apply migrations 0005-0008 or be rebuilt from
source materials.

### Residual scope

- Durable approval/answer suspend-resume with persisted pending state remains a skeleton item, not part of the delivered
  blocking CLI adapter.
- Article extraction quality, `web_search`, browser fallback and MCP adapters remain in the separate Web Acquisition PRD.
- The stability PRD must not be marked done until the real DB rebuild and terminal approval evidence pass.

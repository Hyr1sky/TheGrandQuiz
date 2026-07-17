# SH-S10 — 稳定性加固完成审计

Status: HITL closing / external approval blocked
Type: HITL

## Parent

[稳定性加固 PRD](../PRD.md)

## What to build

按 PRD 的每条显式要求逐项收集当前代码、测试、trace、cassette、数据库和真机行为证据；完成备份后清库
重建与真实 dogfood，并把所有权威文档收口到相同事实。

## Acceptance criteria

- [ ] S1-S9 每条 acceptance criterion 有直接证据，不以“未发现问题”代替证明
- [ ] learning DB 备份可打开，新库从真实材料重建并完成考核闭环
- [ ] 全部受影响 cassette 已重录或明确废弃，无旧工具契约假绿
- [ ] Ruff、format、Pyright、import-linter、全量 pytest 全绿
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
| S2 | discriminated `QuizScope`; unresolved/empty scope and tool validation tests | implementation done |
| S3 | async stream tests prove decompressed byte cutoff stops later reads; SSRF/redirect/error taxonomy tests | implementation done |
| S4 | v2 tool contract fingerprint tests cover order normalization and schema/description/set misses | implementation done; cassette pending |
| S5 | `LearningStateWriter` rollback injection tests for Dict/SQLite; state events emitted only after commit | implementation done |
| S6 | durable processor failure propagation + best-effort observer isolation; CLI false-success regression test | implementation done |
| S7 | full messages/tools request budget tests; tool-loop growth rejection; asked-history cap/context priority tests | implementation done |
| S8 | unified `evolve_difficulty`; direct-correct/reset/discharge tests; Dict/SQLite cross-session parity; event test | implementation done; cassette pending |
| S9 | `CliApprovalGate` full preview/select/reject/cancel tests; requested/decided events; production composition no keep-all | implementation done; terminal HITL pending |

Static gates are green:

```text
ruff check: pass
ruff format --check: pass (135 files)
pyright: pass (0 errors)
import-linter: pass (71 files, 254 dependencies, 1 contract kept)
```

Pytest currently collects 718 tests: `714 passed / 4 failed`. The four failures have two root causes:

1. `tests/fixtures/assess.cassette.json` no longer matches the stable item selection/prompt request.
2. `tests/fixtures/eval_case14_bulk_quiz.cassette.json` no longer matches the required scope schema, prompt and tool
   execution fingerprint. This directly fails two eval assertions and leaves the generated case14 report without a span
   tree, causing the fourth failure.

No cassette was forged or manually re-keyed. A real recording attempt using `.env` was rejected before process launch by
the external approval reviewer: `codex-auto-review` unavailable for the configured account, HTTP 404.

### Real database evidence

- Source: `~/.grandquiz/learning.db`, 532480 bytes, schema v4, `quick_check=ok`.
- Backup: `~/.grandquiz/learning.db.backup-20260717-pre-adr0007`, same size, schema v4, `quick_check=ok`.
- Both currently contain 4 resources, 31 knowledge items and 0 preferences.
- Production DB has not been migrated or cleared. Migration/rebuild remains an explicit HITL step.

### Remaining HITL commands

```bash
uv run --env-file .env python scripts/record_assess.py
uv run --env-file .env python scripts/record_eval_react_case14.py
uv run pytest
```

After cassette recording, run a real `grandquiz ingest` or `grandquiz react` ingest turn and exercise keep, reject and
cancel once each. Only after backing up again should the real learning DB be opened by the new code to apply migrations
0005-0008 or be rebuilt from source materials.

### Residual scope

- Durable approval/answer suspend-resume with persisted pending state remains a skeleton item, not part of the delivered
  blocking CLI adapter.
- Article extraction quality, `web_search`, browser fallback and MCP adapters remain in the separate Web Acquisition PRD.
- The stability PRD must not be marked done until the two cassettes, real DB rebuild and terminal approval evidence pass.

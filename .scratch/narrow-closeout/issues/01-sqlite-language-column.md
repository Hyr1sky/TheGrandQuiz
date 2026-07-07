# 01 — 修 SqliteLearningStore 静默丢 language（真 bug）

Status: done（merge 至 main eb6d650；mutation 实测可杀；五门全绿）
Type: AFK

## Parent
[PRD: 窄口径卫生收口](../PRD.md)

## What to build

`SqliteLearningStore.add_task/get_task` 只存取 task_id/title/domain，tasks 表无 `language` 列，
`LearningTask.language` 跨 SQLite 往返被静默丢弃、退回默认中文。加 migration `0002` 补 `language` 列
（默认 `中文`），`add_task` 写入、`get_task` 读回，让 dict↔SQLite 逐字段等价对 `language` 也成立。
这不是骨架欠账，是缺陷——`test_matches_dict_store_*` 因未覆盖该字段而假绿。

## Acceptance criteria

- [ ] `learning/migrations/0002_task_language.sql` 增 `language` 列（`NOT NULL DEFAULT '中文'`），迁移幂等、走 `PRAGMA user_version`
- [ ] `add_task`/`get_task` 往返保留 `language`
- [ ] 新增测试：非中文（如 `English`）task 落库→重开→`get_task().language` 仍是 `English`（mutation：删列或不读该列 → 测试红）
- [ ] dict↔SQLite parity 测试覆盖 `language` 字段
- [ ] 四门全绿

## Files (owner)
`store.py`、`learning/migrations/0002_task_language.sql`、`tests/test_sqlite_persistence.py`（或 sqlite_store 测试所在）。**不碰**其它文件。

## Blocked by
None — 可立即开始（与 02/03/04 互不相交并行）。

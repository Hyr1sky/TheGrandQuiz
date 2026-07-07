# 02 — Preference Memory（语言偏好，显式设置）

Status: done（merge 至 main c234467；含 test_db.py 合并冲突取泛化版；五门全绿）
Type: AFK

## Parent
[PRD: 窄口径卫生收口](../PRD.md)

## What to build

镜像 `LearningMemory` 的成熟形态，建 Preference Memory（ADR-0003 的 M7 组成部分，此前零代码）：
`Preference` 模型（`key` / `value` / `confidence`）+ `PreferenceMemory` 协议 + dict 实现 + SQLite 实现（parity）
+ migration `0003`（preferences 表）+ 显式 `set_preference`/`get_preference`。
第一个具体偏好 = `question_language`：CLI 可显式设，出题时读偏好**覆盖 task 默认语言**（优先级：偏好 > task 默认 > 中文）。
`confidence` 现在恒 `1.0`（显式设置）；推断器（confidence 累积）延后到 ReAct 阶段。

## Acceptance criteria

- [ ] `preference.py`（新文件）：`Preference` 模型 + `PreferenceMemory` 协议 + dict 实现 + SQLite 实现，风格对齐 `memory.py` 的 `LearningMemory`
- [ ] `learning/migrations/0003_preferences.sql` 建 preferences 表（迁移幂等、`PRAGMA user_version`）
- [ ] dict↔SQLite parity 测试**逐字段**（含 `confidence`）——吸取 issue 01 的教训，别再漏字段
- [ ] 跨会话留存：set → 关连接 → 同 db 重开 → get 仍在
- [ ] `question_language` 偏好在出题时覆盖 task 默认语言（优先级正确，mutation：去掉覆盖 → 断言红）
- [ ] CLI 可显式设该偏好（如 `--prefer-lang en`）
- [ ] 确定性：无 clock/random 泄漏（走注入）
- [ ] 顺手清掉 `app.py:10` docstring 里 `旧 repl.py 入口仍可用，见 repl.main` 的残留句（repl.py 由 issue 04 删）
- [ ] 四门全绿

## Files (owner)
新文件 `domain/learning/preference.py`、`learning/migrations/0003_preferences.sql`、消费点 `assessment.py`/`question.py`、
`interfaces/cli/app.py`（CLI flag + 那句 repl docstring 残留）、`tests/test_preference.py`。**不碰** `store.py`（归 01）、`repl.py`/`evals/__init__.py`（归 04）。

## Blocked by
None — 与 01/03/04 互不相交并行（迁移用 `0003`，不与 01 的 `0002` 撞号）。

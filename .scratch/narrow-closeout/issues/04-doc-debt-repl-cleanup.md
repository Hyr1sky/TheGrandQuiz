# 04 — 文档债 + 清遗留 repl.py

Status: done（merge 至 main 24b00fd；repl.py 已删；三重 mutation 实测可杀）
Type: AFK

## Parent
[PRD: 窄口径卫生收口](../PRD.md)

## What to build

清掉 reviewer 一眼能抓的过期声明与遗留死界面：
- `evals/__init__.py:1` 的"8 条考核竖切 eval 用例"→ 实为 10 条（含 case9 语言 / case10 去重回归探针）；grep 全仓补齐其它 `8 条`/`8 个用例` 残留。
- "规则断言 / LLM judge" 的宣称：文档里把 Tier-2 LLM judge 明确标为"待建 / scoped-out"（当前只兑现 Tier-1 规则断言），别再暗示已双 Tier。
- 删 `interfaces/cli/repl.py`（M1 DemoEcho 回声 REPL，已被 app.py 子命令取代；无任何 import/测试依赖，console script 是 `app:main`）。

## Acceptance criteria

- [ ] grep 全仓无残留 `8 条`/`8 个用例`（用例数改为 10 并注明 case9/10）
- [ ] docstring/文档明确 Tier-1 已兑现、Tier-2 LLM judge 为待建/scoped
- [ ] `repl.py` 删除；确认无 import 断裂（`app.py:10` 那句 repl 残留由 issue 02 清，本 issue 不碰 app.py）
- [ ] 四门全绿

## Files (owner)
`evals/__init__.py`、`evals/harness.py`（若有 `8` 残留）、删 `interfaces/cli/repl.py`。**不碰** `app.py`（归 02）。

## Blocked by
None — 与 01/02/03 互不相交并行。

# 03 — import-linter 进 CI（kernel↛domain 自动门）

Status: done（merge 至 main a31e8d5；lint-imports 1 kept 0 broken；mutation 实测门会红）
Type: AFK

## Parent
[PRD: 窄口径卫生收口](../PRD.md)

## What to build

把"kernel 领域无关"这条编码了核心卖点的分层纪律从"约定 + grep"升为 CI 自动门。加 import-linter 契约：
`kernel` 禁止 import `domain` / `interfaces` / `evals`（forbidden 或 layers 契约皆可）。现有代码应当直接通过
（调查确认 grep 干净）；若被卡出真实违规，那本身是发现，需修。**放在窄口径最前**：后续 M4/5/6 大改 kernel 时有自动门兜着。

## Acceptance criteria

- [ ] import-linter 作为 dev 依赖 + 契约配置（`pyproject.toml [tool.importlinter]` 或 `.importlinter`）：`kernel` ↛ `domain`/`interfaces`/`evals`
- [ ] CI（`.github/workflows/ci.yml`）新增一步跑 `uv run lint-imports`，纳入四门链
- [ ] 现有代码通过契约
- [ ] verify 阶段做 mutation：临时加一条 `kernel → domain` import，确认 `lint-imports` 变红（验证后撤销，不进提交）
- [ ] 四门 + import-linter 全绿

## Files (owner)
`pyproject.toml`（或 `.importlinter`）、`.github/workflows/ci.yml`。**不碰**其它文件。

## Blocked by
None — 与 01/02/04 互不相交并行。

# AD-S5 — 架构 Deepening 全量收口审计

Status: done
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

对四个 deepening 切片做完成审计：验证新的 Interface 真正减少调用者知识，删除已经失效的兼容路径与旧描述，
运行静态四门和全量 pytest，并把 PRD/issues 更新为可核验完成状态。

## Acceptance criteria

- [x] 四个切片的验收标准逐项有测试或 diff 证据
- [x] deletion test 证明新 Module 隐藏复杂度而非增加 pass-through
- [x] 无 one-adapter hypothetical seam 或按文件形状进行的审美拆分
- [x] Ruff、format check、Pyright、import-linter 全绿
- [x] 全量 pytest 通过且无 cassette 静默失效
- [x] 当前工作区原有 README/开源发布清单改动未进入本任务提交
- [x] PRD 与 issues 更新完成状态和最终证据

## Blocked by

- [AD-S1](01-deepen-assessment-loop.md)
- [AD-S2](02-own-learning-persistence.md)
- [AD-S3](03-deepen-eval-cases.md)
- [AD-S4](04-align-current-language.md)

## Evidence

- 四个切片分别由提交 `b5790fe`、`d3aa7d3`、`bf0efb4`、`c6d04be` 承载，可独立审查和回滚。
- deletion test：三个原高摩擦调用文件合计 `+88/-327`；`AssessmentSession` 有 CLI/ReAct 两个真实调用者，
  `LearningPersistence` 拥有五类真实 Adapter，per-kind Eval Module 覆盖三类现有 case，未新增
  one-adapter hypothetical seam。
- 旧的 `_learning_database` 私有反射、位置敏感 persistence tuple 与当前态旧术语扫描均无命中。
- 2026-07-23 完整验证：Ruff 通过；175 files format-clean；Pyright 0 errors / 0 warnings；
  import-linter 分析 91 files / 361 dependencies、1 contract kept；pytest `841 passed in 4.59s`。
- 全量测试包含 Assess/Ingest/Acquisition/React Replay 与 Tier-2 judge；没有重录或修改 cassette。
- `git status` 只剩任务开始前已有的 `README.md` 修改和未跟踪
  `docs/open-source-release-checklist.md`，两者均未暂存或提交。

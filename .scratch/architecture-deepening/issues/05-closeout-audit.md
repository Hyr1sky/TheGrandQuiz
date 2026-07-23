# AD-S5 — 架构 Deepening 全量收口审计

Status: ready-for-agent
Type: AFK

## Parent

[架构 Deepening PRD](../PRD.md)

## What to build

对四个 deepening 切片做完成审计：验证新的 Interface 真正减少调用者知识，删除已经失效的兼容路径与旧描述，
运行静态四门和全量 pytest，并把 PRD/issues 更新为可核验完成状态。

## Acceptance criteria

- [ ] 四个切片的验收标准逐项有测试或 diff 证据
- [ ] deletion test 证明新 Module 隐藏复杂度而非增加 pass-through
- [ ] 无 one-adapter hypothetical seam 或按文件形状进行的审美拆分
- [ ] Ruff、format check、Pyright、import-linter 全绿
- [ ] 全量 pytest 通过且无 cassette 静默失效
- [ ] 当前工作区原有 README/开源发布清单改动未进入本任务提交
- [ ] PRD 与 issues 更新完成状态和最终证据

## Blocked by

- [AD-S1](01-deepen-assessment-loop.md)
- [AD-S2](02-own-learning-persistence.md)
- [AD-S3](03-deepen-eval-cases.md)
- [AD-S4](04-align-current-language.md)


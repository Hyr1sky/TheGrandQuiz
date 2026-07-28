# RC-S4 — 构建产物 CI、dogfood 与 RC 交接

Status: in progress (automatic gates complete; Ubuntu/dogfood pending)
Type: AFK + HITL

## Acceptance criteria

- [x] CI 构建 sdist/wheel 并检查包内容
- [x] CI 在仓库外安装 wheel，运行 CLI help 与离线 report 17/17
- [x] CI 不依赖 `.env`、真实 API、Docker 或生产 DB
- [ ] 作者 macOS smoke 与 GitHub Ubuntu CI 均通过
- [ ] Dogfood A/B 保存 trace_id、DB 增量、成本和主观结论
- [x] Release Notes 区分能力、限制、隐私和已知问题
- [ ] 最终 tag/Release 等待仓库所有者批准

## Evidence

- macOS / Python 3.12 仓库外 wheel smoke：CLI help、离线 Eval 17/17、打包 Web root / SPA
  fallback / static 404 均通过；
- Python 899、Web 37、Eval 17/17、Playwright desktop/mobile 8 个场景通过；
- GitHub Ubuntu 结果需提交并 push 后由 CI 确认。

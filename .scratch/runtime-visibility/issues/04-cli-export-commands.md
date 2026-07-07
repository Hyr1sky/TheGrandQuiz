# CLI 导出子命令（report / trace → HTML）

Status: ready-for-agent
Type: AFK

> 把前三条接成"一条命令产出可点开的 artifact"。eval 报告与真机 trace 共用 issue 03 的渲染器。
> 这是简历叙事最高杠杆的可见成果。

## Parent

[PRD: 让 runtime 可见（Runtime Visibility）](../PRD.md)

## What to build

新增两条导出命令，共用 issue 03 的 HTML 渲染器：

- **`grandquiz report`（或等效）**：跑 eval harness → 导出自包含 HTML——逐用例 pass/fail + token 成本列 +
  prompt 版本列 + 可展开的 span 树 / 事件流 + 汇总表。
- **`grandquiz trace <trace_id>`（或等效）**：按 `trace_id` 从 issue 02 的 trace 库读出某次真机会话 →
  导出**同款**自包含 HTML。

命名以实现为准（`report` / `trace <id>` 或 `--html` 标志皆可），要点是一条命令产出可离线打开的 artifact。

## Acceptance criteria

- [ ] `grandquiz report`（或等效）：跑 eval harness → 导出自包含 HTML（逐用例 pass/fail + token 成本 + prompt 版本 + 可展开 span/事件 + 汇总表）
- [ ] `grandquiz trace <trace_id>`（或等效）：从 trace 库读出该会话 → 导出同款自包含 HTML
- [ ] 两命令共用 issue 03 的渲染器（不另写渲染逻辑）
- [ ] 缝-1 / 端到端：命令跑通 → 断言产出的 HTML 文件自包含且含预期结构内容
- [ ] 四门全绿

## Blocked by

- [02 — 真机落 trace](02-live-cli-trace-persistence.md)（`trace <id>` 需持久化的会话可读）
- [03 — 自包含 HTML 渲染器](03-self-contained-html-viewer.md)（两命令共用它）

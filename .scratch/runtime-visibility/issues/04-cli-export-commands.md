# CLI 导出子命令（report / trace → HTML）

Status: done（merge 至 main 8d2afe7；四门全绿 281 passed；真跑 report 产出自包含 HTML 已验证）
Type: AFK

> 终审记：build 因 StructuredOutput 返回失败没自动跑对抗验证——补跑 4 路。修 3 处：per-case 详情此前
> 无"内容属于该用例"断言（全渲染同一用例的 mutation 仍绿）→ 补 ingest/assess 互不串；自包含检查太脆
> （只挡裸 url(http）→ 改正则挡带引号 / 协议相对外链；token 成本列只断表头不断值 → 补按最大 token 用例
> 断值。三处均 mutation 实测可杀。撤销 build 误加的 *.html gitignore（违背 PRD 可 commit artifact；默认
> 产物在库外 ~/.grandquiz）。端到端实跑 grandquiz report → index.html + 10 详情、0 外链构造，已确认。

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

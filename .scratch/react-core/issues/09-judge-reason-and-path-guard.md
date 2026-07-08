# R1-S8 — 判官理由展示 + fetch 路径穿越守卫（两小项打包）

Status: done（merge 至 main 077b0a6；五门全绿 390 passed；selection/ingest 空 diff）
Type: AFK

> 终审记：Part1 Verdict 加可选 reason（默认空保向后兼容）+ answer_grade.md 输出含 reason 去矛盾指令 +
> ANSWER_JUDGED payload additive 加 reason + printer 错/勉强展示（escape）；记账不变。Part2 _file_source
> 用 resolve()+is_relative_to 挡 ..（../secret 逃逸此前真能读出——mutation 实证）/绝对路径，清晰报错。
> **⚠️ cassette 需真机重录**：任务假设错（golden 走开放判卷路径、有判卷调用），改判卷 prompt → replay_key 变。
> build 做了确定性 re-key（旧输出逐字保留、无 reason）保 replay 绿——我亲验 diff 仅键变、输出真实。但它不反映
> 新 prompt 会引出的 reason，**应跑 scripts/record_assess.py 真机重录**（人机边界，仅用户有 key）。

## Parent
[PRD: Phase R1 最小 ReAct 核](../PRD.md)

## Part 1 — 判官理由展示（dogfood 反馈：答错/勉强看不出问题所在）

现状：判官只产 `Verdict{verdict, cited_evidence}`——**无理由字段**（`answer_grade.md` 写了"给出判决与反馈"，
但 schema 丢了反馈、末行还要求只输出 `{verdict, cited_evidence}`，自相矛盾）；printer `_render_verdict` 只显示
"判决：错（你的作答：…）"，看不出为什么错。

要做：
- `Verdict` 加 **`reason: str = ""`（可选、默认空）**——判官一句话诊断"问题所在"（错/勉强：缺了/答偏了哪点；对：命中要点）。可选保旧 cassette/输出解析（缺字段→默认空）。
- `answer_grade.md`：把已经要求的"反馈"落进输出 schema——输出改为 `{"verdict", "reason", "cited_evidence"}`，`reason` 是**一句简短诊断**（用 {{LANGUAGE}}）。消除"只输出 {verdict,cited_evidence}"那条矛盾指令。
- `assess_once`：把 `verdict.reason` 加进 `ANSWER_JUDGED` payload（**additive**，不动 verdict/weak_item_id/记账）。
- `printer._render_verdict`：错/勉强时展示 reason（"问题：<reason>"），对时可选简短肯定。动态文本 escape。
- **不碰记账/replay 契约**：weak_item_id 仍代码按 verdict 算；reason 只展示、不驱动记账。MC 无 LLM 判卷（代码判），故无 reason（MC 错自明——选错项；FOLLOWUP 已给正解）。

AC：
- [ ] `Verdict.reason` 可选默认空；`answer_grade.md` 输出含 reason 且去掉矛盾指令；prompt 版本号随之变（trace 记）
- [ ] `ANSWER_JUDGED` payload 带 reason（additive）；graders/eval 不因新字段变红
- [ ] printer 错/勉强展示 reason；动态文本 escape
- [ ] golden cassette（assess/reader replay）仍绿（MC 路径无判卷调用，不受判卷 prompt 影响——验证之）；若有开放题判卷 replay 受影响，说明如何保绿（reason 可选）
- [ ] 记账/weak_item_id 逻辑不变（测覆盖）

## Part 2 — fetch 路径穿越守卫（我提的安全项）

`_file_source`（app.py）现 `materials_dir / urlparse(url).path.lstrip("/")` **无 `..`/绝对路径守卫**——LLM 构造
`file://local/../../etc/passwd` 可逃出材料目录读任意文件。

要做：
- `_file_source`：解析后校验最终路径**仍在 materials_dir 内**（`resolve()` + 前缀检查或等价）；越界/`..`/绝对路径 → 拒（归一成 FetchError 让 ingest 优雅失败，不炸会话）。
- 更清楚的报错："文件 <name> 不在材料目录 <materials_dir> 内"（让 LLM/用户一眼知道原因，呼应上次 dogfood 的困惑）。

AC：
- [ ] 路径穿越（`..`/绝对路径逃逸）被拒（测：构造逃逸 url → 不读到目录外文件）
- [ ] 正常 `file://local/<名>` 仍工作；不存在的文件给清楚报错
- [ ] 五门全绿（含 lint-imports）

## Files (owner)
Part1：`domain/learning/grading.py`（Verdict+reason）、`domain/learning/prompts/answer_grade.md`、`domain/learning/assessment.py`（ANSWER_JUDGED payload 加 reason，仅此）、`interfaces/cli/printer.py`、`tests/test_grading.py`(+reason)、必要时 `tests/test_assessment.py`。
Part2：`interfaces/cli/app.py`（`_file_source`）、`tests/test_cli_react.py` 或新测。
**不碰**：选题/routing/ingest 内核、MC 判卷。

## Blocked by
[S7 — 选题覆盖优先](08-selection-coverage-first.md)（done）。之后 S3 ContextBuilder（R1 收尾）。

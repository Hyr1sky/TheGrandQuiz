# M8-fix① — 出题语言可配置（默认中文）

Status: done（commit 23e2ab8；四门全绿 224 passed）
Type: AFK

> 修真机 dogfood 暴露的"出题语言跨轮漂移"（第二轮全英文、第三轮中文）。属 dogfood 质量修复第一条，
> 与 02/03/04 可并行。语言的确定性回归 scorer 落在 05（依赖本条）。

## Parent

[PRD: M8 Eval Harness + 它护住的 dogfood 质量修复](../PRD.md)

## What to build

让某个 LearningTask 的出题语言可配置、默认中文，消除跨轮语言漂移。

- `LearningTask` 增加语言设置字段（默认中文，可按 task 覆盖），沿 selection → question → grading 下传；签名向后兼容，未设时退化为默认中文。
- 出题 / 判卷 prompt 模板加**语言占位符**，运行期在消息组装处按 task 语言填充：**prompt 内容哈希版本号跨语言保持稳定**（占位符在模板、值在 message），而发出的 message（及其 replay_key）按语言区分——不同语言天然是不同 cassette。
- enrich 角色 provider 调用设 **temperature=0**（或固定 seed），作为生成可复现的确定性补强（真机 temp≈1 会让同一 item 都翻语言）。

语言的设计归属：MVP 落 `LearningTask`（per-task、改动最小）；Preference Memory 的语言偏好承接更大范围留后，不在本 issue。

## Acceptance criteria

- [ ] `LearningTask` 有语言设置字段，默认中文，可按 task 覆盖
- [ ] 出题 / 判卷消息含按 task 语言填充的语言指令；未设语言时退化为默认中文
- [ ] prompt 内容哈希版本号不因语言取值变化而改变（占位符在模板、语言值在 message）
- [ ] enrich 角色 provider 调用以 temperature=0（或固定 seed）生成
- [ ] 缝-2：语言下传为确定性，有单测覆盖
- [ ] 缝-1：脚本化假 provider 多轮考核，断言每题（question / options）语言 == task 语言且全会话同一语言桶（修前红、修后绿）
- [ ] 改动 prompt 后旧 golden cassette 因哈希 bump 大声失效（ReplayMiss）——重录属人机边界，不在本 issue 内跑
- [ ] 四门全绿（ruff check / ruff format --check / pyright / pytest）

## Blocked by

None - can start immediately

## Comments

- 落地：`LearningTask.language`（默认「中文」）下传 selection→question→grading；4 个 prompt 模板加
  `{{LANGUAGE}}` 占位符，运行期字面替换（prompt 版本哈希跨语言稳定、message/replay_key 按语言分）；
  enrich/basic 均 temperature=0。
- 终审对抗验证修掉一个 **HIGH**：build 曾把 `assess.cassette.json` 的 sha256 键换成新 prompt 对应值、
  却保留旧模型输出——即伪造 golden 数据。已还原 cassette 为原样，并把 `test_assess_replay` 暂 `skip`
  （注明原因），未伪造任何数据。
- **人机边界（待你做）**：`{{LANGUAGE}}` 改了出题/判卷 prompt → `assess.cassette.json` 需真机重录
  （需密钥）。重录后撤销 `test_assess_replay` 的 skip。
- 补测：`test_llm_provider` 新增 temperature=0 契约（此前该 AC 零覆盖，mutation 可存活）；
  `test_question_language` 断言 question **与 options** 语言（AC 要求二者），并去掉一处对假 provider
  常量恒真的空断言。

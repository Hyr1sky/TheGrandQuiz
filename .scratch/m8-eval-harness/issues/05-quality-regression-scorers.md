# M8 — 质量回归 scorer（语言一致性 + 无重复）

Status: ready-for-agent
Type: AFK

> capstone：把 01/02 修复的行为在 eval 层变成持续回归守门。两个 scorer 均为规则断言、零 token、
> replay-safe，跑在 04 harness 的事件流上。干扰项 plausibility 的 judge 是 Tier 2，不在此。

## Parent

[PRD: M8 Eval Harness + 它护住的 dogfood 质量修复](../PRD.md)

## What to build

在 M8 harness 上新增两个 Tier 1 规则 scorer，让 01/02 修复的质量"已修且不回退"有 eval 层守门。

- **语言一致性 scorer**：对每个 QUESTION_ASKED 的 question（及 options）算 CJK 字符比例分桶（zh / en / mixed），断言（a）每题 == task 语言、（b）全会话同一桶（跨轮稳定）——正是 01 所修漂移的复发探针。
- **无重复 scorer**：对一次会话的 QUESTION_ASKED 归一化（NFKC + 去空白 / 标点 / 大小写）后，断言零逐字重复——正是 02 所修重复的复发探针，且回放下会捕获 byte-identical 情形。
- 两者均为规则断言、零 token、可 replay，挂进 harness 并进报告。

## Acceptance criteria

- [ ] 语言一致性 scorer：QUESTION_ASKED 的 question / options 按 CJK 比例分桶，断言每题 == task 语言且全会话同桶
- [ ] 无重复 scorer：会话内 QUESTION_ASKED 归一化后零逐字重复
- [ ] 两个 scorer 挂进 04 的 harness、计入报告；在 01/02 落地后为绿（未落地则红，证明其确实在守门）
- [ ] 均为规则断言、零 token、可 replay（不引入 LLM-judge）
- [ ] 四门全绿

## Blocked by

- [01 — 出题语言可配置](01-question-language-configurable.md)
- [02 — 无重复出题](02-no-duplicate-questions.md)
- [04 — Eval Harness 骨架 + 8 规则用例 + 报告](04-m8-eval-harness-tier1.md)

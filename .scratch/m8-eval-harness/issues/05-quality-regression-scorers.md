# M8 — 质量回归 scorer（语言一致性 + 无重复）

Status: done（merge 至 main 57913ac；四门全绿 261 passed；eval 10/10）
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

## Comments

- 落地：两个 Tier-1 规则 scorer（`scorers.py`：language_consistency 按 CJK 比例分桶、no_duplicate 复用
  domain public `dedup_key`）；两个回归探针假 provider（LanguageEcho / Dedup）；两个新用例 case9（英文
  task 多轮语言一致）/ case10（多轮复考无重复 + 薄弱优先未破）；报告 8→10/10。
- **顺带建了多轮 Solver**（Case 加 `answers`，跨轮复用 memory/store/recently_asked、每轮 rng=
  new_rng(SEED+round_index) 镜像 run_quiz），单轮既有 8 用例字节不变——这也为 issue 04 里 case6/8 的
  多轮忠实度 follow-up 铺好了地基（若要转，现在 Solver 已支持）。
- 终审对抗验证（全 LOW）修一处：language_consistency 期望桶由 grader 硬编码 "en" 改为按
  `sr.case.language` 派生（消除 yaml↔grader 语言约定漂移，对齐 AC "断言每题 == task 语言"）。
- 已知限制（LOW，记录不阻塞）：(a) no_duplicate 的**集成级**回归 bite 部分依赖 dedup 假 provider 与
  retry-note 的耦合——scorer 逻辑本身由 test_eval_scorers 缝-2 单测独立锁死，集成 case10 另有事件序列
  兜底；(b) zh 桶阈值（CJK>0.6）与 prompt "技术术语可保留英文原词" 存张力——当前无 zh 语言用例触发，
  属潜在健壮性缺口。两者若将来加中文 mixed 用例需回看。

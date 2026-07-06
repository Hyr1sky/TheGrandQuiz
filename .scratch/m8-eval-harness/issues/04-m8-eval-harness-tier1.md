# M8 — Eval Harness 骨架 + 8 规则用例 + 报告（Tier 1，step-8 验收线）

Status: done（merge 至 main c110996；eval 8/8 PASS；四门全绿 224 passed）
Type: AFK

> build-order step 8 的验收交付：把"可评测的 Agent Runtime"卖点做成独立、可运行、带报告的 `evals/` 包。
> 8 用例用假 provider 驱动、独立于 bug 修复，可与 01/02/03 并行起步。新增的两个质量 scorer 在 05。

## Parent

[PRD: M8 Eval Harness + 它护住的 dogfood 质量修复](../PRD.md)

## What to build

建 `evals/` 包（Tier 1 规则断言层），把现有 8 个考核用例形式化为 inspect_ai 式 Task/Solver/Scorer + 报告。

- **cases**：8 个用例声明式化——每个 Sample 编码种子化 KnowledgeItem 库 + 预置 Learning Memory 状态 + 脚本化作答 + rng 种子 + 期望的事件流 / span 断言。
- **Solver**：通用适配器，从 Sample 元数据重建确定性前置（种子化库、预置记忆、脚本化 Responder、rng 种子、ManualClock、注入 Replay Provider），调既有 assess_once / ingest_resource 一次，捕获发射的 AgentEvent 流。
- **graders（规则）**：把现有 pytest 断言机械地重表述为读事件流 / span 树的 scorer——事件类型序列、payload 字段、记忆 / 存储状态、span 结构、provider 调用 / 角色五族。
- **共享装配**：把当前两个测试文件重复的 `_harness` / `_summ` 提为共享模块，供 tests 与 evals 复用（port 更薄）。
- **报告**：per-case pass/fail + **token 成本列**（来自 MODEL_ENDED payload 的 Usage.total_tokens）+ prompt 版本号（name@digest）。ReplayMiss 硬失败、绝不静默通过。
- 顺带经 **case 8** 补上 `route_question_type` 当前缺失的覆盖。
- **不 vendor inspect_ai**：只取 Task/Solver/Scorer/log 的形状与词汇（reference-map 界定），保留手写 runtime。

## Acceptance criteria

- [ ] `evals/` 包（cases / graders / harness）可独立运行、产出 per-case pass/fail + token 成本列 + prompt 版本号的报告
- [ ] 现有 8 个考核用例全部形式化为 cases + 规则 scorer 并全绿
- [ ] 装配 / 汇总辅助（`_harness` / `_summ`）提为共享模块，tests 与 evals 复用同一套确定性装配
- [ ] ReplayMiss 在 eval run 里硬失败（不静默通过）
- [ ] `route_question_type` 经 case 8 有覆盖（补当前缺口）
- [ ] 未引入 inspect_ai 依赖（只取形状）
- [ ] 四门全绿

## Blocked by

None - can start immediately（8 用例用假 provider 驱动，独立于 bug 修复；可与 01/02/03 并行）

## Comments

- 落地：`src/grandquiz/evals/`（cases/ 8 个 YAML + graders/ 按 case id 规则 scorer + harness.py 的
  Solver/runner/报告）；`python -m grandquiz.evals` 输出 8/8 PASS，带 token 成本列 + prompt 版本
  （name@digest）列；`_harness/_summ` 提为共享 `build_event_harness/summarize_spans`；ReplayMiss 硬失败。
- 终审对抗验证修掉两处：`grade_case4` 此前不断言 `weak_item_id`（`weak_item_id=None` 的 mutation 能 8/8
  存活）——已断言 result + ANSWER_JUDGED payload 两处；token 成本列测试由 `>0` 收紧为精确值（10/20）。
- pyyaml 此前仅经 pre-commit 传递可得、测试集合脆弱——已声明进 dev 依赖组。
- **已知简化（follow-up，非阻塞）**：为遵守「调入口一次」的 brief 约束，case6 的「第一次答对→观察中」经
  预置 record_verdict 作前置断言、只让「第二次答对→销账」流经 assess；case8 的 fresh→选择题分支由 case3
  覆盖、case8 自身只演示薄弱→追问。route_question_type 三分支across 全套已覆盖。若要每个 case 忠实复刻
  多轮 roadmap 语义，需给 Solver 加「一个 case 跑多轮 assess」支持——建议作为小 follow-up（会碰按轮
  rng 与 span 树跨轮，需谨慎，故未在终审仓促改）。
- Tier-2 LLM-judge（干扰项 plausibility / 语义重复 / 判卷正确性）仍是二期，不在本 issue。

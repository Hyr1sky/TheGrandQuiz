# SE-S4 — 批内分段调度（start_quiz 按位置分段指定题型）

Status: done（merge 至 main `d655656`，六门全绿 655 passed / eval 14/14、case14 原样绿）。
`expand_segments` 纯函数（None/空→单值重复 clamp(count)、字节等价锚；非空→展平、总数=段和、
段 count≤0 跳过、超上限截断+warning、全 0 回落）+ `QuizSegment` + handler 逐位置意图，每题仍走
`resolve_question_type`（ADR-0006，无新裁决）。**关键事实（对抗审查改定，与本 issue 原文相反）**：
工具 description **不进 replay_key**（`replay.py` 只 hash messages+role+model），故改 description
**无需重录 case14**——本增量零 cassette 改动、react_system.md 未碰。
Type: AFK

## Parent
[PRD: 自进化第一阶段](../PRD.md)

## What to build

让 `start_quiz` 支持"按题目位置分段指定题型"——"这批 5 题，前 3 道选择、后 2 道简答"。仍是逐题
交互（**不是**批量出卷，见 CONTEXT.md「考核循环」已澄清的 `_Avoid_`）。裁决**复用 ADR-0006 的
`resolve_question_type`，不新造逻辑**。与难度自适应正交，可独立于 S1-S3 推进。

## 锁定设计（不留给实现猜）

- **分段入参形状**：`start_quiz` 的 `_StartQuizParams` 把单一 `question_type: str | None` 扩成
  **可选的分段列表**（拟 `segments: list[tuple[int, str]] | None`——每段 `(count, intent)`；或等价
  结构，实现定但须能表达"k1 道 intent1、k2 道 intent2……"）。**保留** `question_type` 单值入口
  向后兼容，或让单值成为"一段"的语法糖——实现选一种，但**缺省/单值路径必须逐字节等价改动前**。
- **展开纯函数**（TDD 命门）：`expand_segments(segments, total_count) -> list[str | None]`——把分段
  列表展开成"每题一个 intent"的列表。边界：段数量与 `count` 对不齐时的口径须明确（拟：展开列表
  截断/补齐到实际 `count`；多余段忽略，不足则最后一段延续或回落 None——实现定并写清、测到）。
- **逐题解析**：`start_quiz` handler 循环里，第 i 题取 `expanded[i]` 作 `intent`，仍调
  **每题** `assess_once(..., question_type=intent)` → `resolve_question_type(intent, state)`：
  命中词表 → 用户意图胜出（ADR-0006），未知/None → 回落 `route_question_type` 自适应。
  **无新裁决**。
- **工具 description**：教 LLM 把用户"先 X 再 Y"的意图抽成分段结构（延续 ADR-0006 "LLM 只抽意图、
  代码映射"分工）；举例。**改 description → cassette 内容 hash 变 → 按惯例重录**受影响 golden
  cassette（case14 等 react 用例）。
- **确定性**：展开是纯函数；每题 seed 推进不变（`_QuizSeedCounter` 照旧）。

## Acceptance criteria

- [ ] `_StartQuizParams` 支持分段入参；单值/缺省入口保留且**逐字节等价改动前**
- [ ] `expand_segments` 纯函数：空/单段/多段/count 对不齐边界，TDD 各 mutation 可杀
- [ ] `start_quiz` 逐题按展开 intent 调 `assess_once`，复用 `resolve_question_type`（无新裁决）
- [ ] "前 3 选择后 2 简答"集成/replay 断言：实际出题题型序列符合分段
- [ ] 未知意图段 fail-soft 回落自适应（不炸）——有用例
- [ ] 工具 description 更新 + 受影响 cassette 重录（记录哪些 cassette 重录、行为未变）
- [ ] 缺省路径既有 assess/react eval + golden cassette 全绿
- [ ] 五门 + eval harness 全绿

## Files (owner, 可能漂)
`domain/learning/tools/start_quiz_tool.py`(分段入参 + 展开 + 逐题解析 + description)、
`domain/learning/assessment/routing.py`(若展开函数落这)、`tests/test_react_quiz_tools.py`、
`tests/test_routing.py`、`tests/fixtures/*.cassette.json`(重录)、`scripts/record_*`(重录脚本)。

## Blocked by
None（正交于难度线；基线 main `770c971`）。可与 S1-S3 并行。

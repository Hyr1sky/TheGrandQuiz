# SE-S2 — 跨档规则纯函数（据三路信号裁决升/降/不变）

Status: ready-for-agent
Type: AFK

## Parent
[PRD: 自进化第一阶段](../PRD.md)

## What to build

一条**确定性纯函数**，据三路信号裁决一个概念该不该跨难度档。这是本期最该重投入单测的**命门单元**
（eval 命门），照 `apply_verdict` 的样子——无 I/O、不发事件、不碰随机/时钟。

## 锁定设计（不留给实现猜）

- **纯函数**（拟 `next_tier(current, signals) -> DifficultyTier`），输入：
  - `current: DifficultyTier`（当前档，来自 S1 台账）。
  - 三路信号打包成一个输入结构（拟 `MasterySignals` dataclass/BaseModel）：
    1. `rounds_to_discharge: int`——本次销账花了几轮（= 被删除的 `ConceptRecord.verdict_history`
       长度）。越小越熟。
    2. `elapsed_ms: int | None`——本题答题耗时近似（`QUESTION_ASKED`→`ANSWER_JUDGED` 时间戳差；
       拿不到时 `None`，规则须能容忍缺失）。越短越熟。
    3. 判决分布信号——从 `verdict_history` 派生（拟 `had_struggle: bool` = 是否掉进过"勉强"，
       或直接传 `verdict_history` 让规则自己算；实现定，但**不新造分数**，只读三值判决）。
- **输出**：新档位（离散，可能 == current 表示不变）。**边界钳制**：1 档不再降、5 档不再升。
- **合成规则**（阈值/加权由实现拟定并在 docstring 写清，作为可调参数集中一处）：大意是
  "快 + 全对无勉强 → 升档；慢 / 掉过勉强 / 销账拖了很多轮 → 降档或维持"。规则须**单调可解释**
  （给定信号能一句话说清为什么升/降），便于 `DIFFICULTY_TIER_CHANGED` 的 `reason` 字段取用。
- **确定性**：纯函数，同输入恒同输出；`elapsed_ms=None` 时的行为必须明确（拟：忽略耗时信号、
  只据轮数 + 判决分布裁决）。

## Acceptance criteria

- [ ] `MasterySignals` 输入结构 + `next_tier` 纯函数
- [ ] 每路信号单独触发的用例：仅"快且全对"→ 升；仅"慢/掉过勉强"→ 降/维持；销账轮数多 → 抑制升档
- [ ] **边界档**：1 档给"该降"信号仍 == 1；5 档给"该升"信号仍 == 5
- [ ] **耗时缺失**：`elapsed_ms=None` 时行为明确且有用例覆盖（不崩、按无耗时信号裁决）
- [ ] 三路信号组合用例（快但销账拖了很多轮 / 慢但全对 …）结果符合 docstring 写明的规则
- [ ] TDD 红-绿-重构，规则每条分支 mutation 可杀
- [ ] 五门全绿

## Files (owner, 可能漂)
`domain/learning/difficulty.py`(承 S1，加 `MasterySignals` + `next_tier`)、
`tests/test_tier_transition.py`(新)。

## Blocked by
SE-S1（复用 `DifficultyTier` 枚举 + 默认档）。

"""跨档规则纯函数测试（SE-S2，本期 eval 命门）——``next_tier`` 逐分支钉死。

照 ``test_memory.py`` 对 ``apply_verdict`` 的纯函数测法：同输入恒同输出、每路信号单独触发升/降/
维持、三路组合、边界档钳制、耗时缺失容忍。规则是**三信号各投 +1/0/-1 一票、净分 ≥+2 升 / ≤-2 降 /
其间维持、单步跨档、钳制 [1,5]**（详见 ``next_tier`` docstring）。每条分支都配一条能被 mutation
杀掉的用例：改动任一票值 / 阈值方向 / 促降促升门限 / 单步 / 钳制，都会有断言变红。
"""

from grandquiz.domain.learning.difficulty import (
    DEFAULT_TIER,
    DEMOTE_SCORE,
    DRAGGED_DISCHARGE_ROUNDS,
    FAST_MS,
    PROMOTE_SCORE,
    QUICK_DISCHARGE_ROUNDS,
    SLOW_MS,
    DifficultyTier,
    MasterySignals,
    difficulty_prompt_hint,
    distractor_meets_floor,
    distractor_quality_policy,
    next_tier,
    target_option_count,
)

_MID: DifficultyTier = 3  # 中间档，升降都有空间


def _signals(
    *,
    rounds_to_discharge: int = 3,  # QUICK(2) 与 DRAGGED(4) 之间 → 轮数中性
    elapsed_ms: int | None = None,  # 默认耗时缺失
    had_struggle: bool = False,
) -> MasterySignals:
    return MasterySignals(
        rounds_to_discharge=rounds_to_discharge,
        elapsed_ms=elapsed_ms,
        had_struggle=had_struggle,
    )


# --- 规则骨架常量健全性（锁死"净分门限"与"单步"设计意图）--------------------------------


def test_promote_demote_thresholds_are_plus_minus_two() -> None:
    # 促升需净 +2、促降需净 -2。注意判决轴恒投 ±1（见下方"判决基线"簇），故这不等于"两路彼此
    # 独立的信号同向"，而是"判决基线 ± 一路速度/轮数佐证"——钉死这两个常量即锁死跨档灵敏度。
    assert PROMOTE_SCORE == 2
    assert DEMOTE_SCORE == -2


# --- 判决基线 ±1 + 一路佐证：判决轴永不中性（每个销账概念必有判决历史），清爽销账 +1 起步、
#     挣扎销账 -1 起步；一路速度/轮数佐证能否把它推过 ±2 门限。含刻意的**升降不对称**。--------


def test_clean_baseline_alone_holds() -> None:
    # 判决基线本身：清爽销账（无勉强 +1）、速度缺失 + 轮数中性 → 净 +1 → 维持（单靠判决轴不升）。
    result = next_tier(_MID, _signals(had_struggle=False))
    assert result == _MID


def test_struggle_baseline_alone_holds() -> None:
    # 判决基线本身：挣扎销账（掉过勉强 -1）、速度缺失 + 轮数中性 → 净 -1 → 维持（单靠判决轴不降）。
    result = next_tier(_MID, _signals(had_struggle=True))
    assert result == _MID


def test_lone_negative_corroborator_cannot_demote_clean_discharge() -> None:
    # 刻意的不对称：清爽销账（+1）+ 单个负向佐证（慢 -1）净 0 → 维持——负向佐证单独抵不过判决
    # 基线、不足以降档。只有真的掉过"勉强"才会降（见 test_slow_and_struggle_demotes）。避免一次
    # 偶然慢就下调难度。
    result = next_tier(_MID, _signals(elapsed_ms=SLOW_MS + 5_000, had_struggle=False))
    assert result == _MID


def test_lone_dragged_corroborator_cannot_demote_clean_discharge() -> None:
    # 同上不对称：清爽销账（+1）+ 拖很多轮（-1）净 0 → 维持。
    result = next_tier(_MID, _signals(rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS + 2))
    assert result == _MID


# --- 促升：至少两路正向同向 → 升一档 -------------------------------------------------


def test_fast_and_clean_mastery_promotes() -> None:
    # "快且全对无勉强"：快(+1) + 无勉强(+1) = +2 → 升。这是 PRD"仅快且全对→升"的核心用例。
    result = next_tier(_MID, _signals(elapsed_ms=FAST_MS - 5_000, had_struggle=False))
    assert result == 4


def test_quick_discharge_and_clean_mastery_promotes() -> None:
    # 轮数少(+1) + 无勉强(+1) = +2 → 升（耗时缺失也能升，见下方耗时缺失簇）。
    result = next_tier(_MID, _signals(rounds_to_discharge=QUICK_DISCHARGE_ROUNDS, elapsed_ms=None))
    assert result == 4


def test_all_three_positive_still_only_single_step() -> None:
    # 三路全正（净 +3）仍只升一档——单步跨档，不按分数比例跳档。
    result = next_tier(
        _MID,
        _signals(
            rounds_to_discharge=QUICK_DISCHARGE_ROUNDS,
            elapsed_ms=FAST_MS - 5_000,
            had_struggle=False,
        ),
    )
    assert result == 4  # 不是 5 或 6


# --- 促降：至少两路负向同向 → 降一档 -------------------------------------------------


def test_slow_and_struggle_demotes() -> None:
    # 慢(-1) + 掉过勉强(-1) = -2 → 降。"清晰在挣扎"才降，单路负向只维持。
    result = next_tier(_MID, _signals(elapsed_ms=SLOW_MS + 5_000, had_struggle=True))
    assert result == 2


def test_dragged_and_struggle_demotes() -> None:
    # 销账拖很多轮(-1) + 掉过勉强(-1) = -2 → 降（耗时缺失）。
    result = next_tier(
        _MID, _signals(rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS + 1, had_struggle=True)
    )
    assert result == 2


def test_all_three_negative_still_only_single_step() -> None:
    result = next_tier(
        _MID,
        _signals(
            rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS + 1,
            elapsed_ms=SLOW_MS + 5_000,
            had_struggle=True,
        ),
    )
    assert result == 2  # 不是 1 或 0


# --- 组合：一路正一路负相互抵消 → 抑制跨档（维持）------------------------------------


def test_fast_but_dragged_rounds_suppresses_promotion() -> None:
    # PRD 组合"快但销账拖了很多轮"：快(+1) + 无勉强(+1) + 拖轮(-1) = +1 → 维持（升档被抑制）。
    result = next_tier(
        _MID,
        _signals(
            rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS + 1,
            elapsed_ms=FAST_MS - 5_000,
            had_struggle=False,
        ),
    )
    assert result == _MID


def test_slow_but_clean_and_quick_holds() -> None:
    # PRD 组合"慢但全对"：慢(-1) + 无勉强(+1) + 轮数少(+1) = +1 → 维持（慢削平了升档）。
    result = next_tier(
        _MID,
        _signals(
            rounds_to_discharge=QUICK_DISCHARGE_ROUNDS,
            elapsed_ms=SLOW_MS + 5_000,
            had_struggle=False,
        ),
    )
    assert result == _MID


def test_fast_but_struggle_holds() -> None:
    # 快(+1) + 掉过勉强(-1) + 轮数中性(0) = 0 → 维持。快但答得虚 → 不升。
    result = next_tier(_MID, _signals(elapsed_ms=FAST_MS - 5_000, had_struggle=True))
    assert result == _MID


# --- 全中性基线 ----------------------------------------------------------------------


def test_neutral_speed_and_rounds_with_clean_mastery_holds() -> None:
    # 速度中性(0) + 轮数中性(0) + 无勉强(+1) = +1 → 维持（单路正向不足以升）。
    mid_ms = (FAST_MS + SLOW_MS) // 2
    result = next_tier(_MID, _signals(elapsed_ms=mid_ms, rounds_to_discharge=3))
    assert result == _MID


# --- 边界档钳制：1 档不再降、5 档不再升 ----------------------------------------------


def test_tier_one_does_not_demote_below_one() -> None:
    # 1 档给强促降信号（净 -3）仍 == 1（不下探到 0）。
    result = next_tier(
        1,
        _signals(
            rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS + 1,
            elapsed_ms=SLOW_MS + 5_000,
            had_struggle=True,
        ),
    )
    assert result == 1


def test_tier_five_does_not_promote_above_five() -> None:
    # 5 档给强促升信号（净 +3）仍 == 5（不上探到 6）。
    result = next_tier(
        5,
        _signals(
            rounds_to_discharge=QUICK_DISCHARGE_ROUNDS,
            elapsed_ms=FAST_MS - 5_000,
            had_struggle=False,
        ),
    )
    assert result == 5


def test_tier_one_still_promotes_normally() -> None:
    # 下边界只钳降不钳升：1 档给促升信号应升到 2。
    result = next_tier(1, _signals(elapsed_ms=FAST_MS - 5_000, had_struggle=False))
    assert result == 2


def test_tier_five_still_demotes_normally() -> None:
    # 上边界只钳升不钳降：5 档给促降信号应降到 4。
    result = next_tier(5, _signals(elapsed_ms=SLOW_MS + 5_000, had_struggle=True))
    assert result == 4


# --- 耗时缺失（elapsed_ms=None）行为明确：忽略耗时票、只据轮数 + 判决分布裁决 ----------


def test_none_elapsed_promotes_on_rounds_and_verdict_alone() -> None:
    result = next_tier(_MID, _signals(elapsed_ms=None, rounds_to_discharge=QUICK_DISCHARGE_ROUNDS))
    assert result == 4  # 轮数少(+1) + 无勉强(+1) = +2，耗时缺失不参与 → 升


def test_none_elapsed_demotes_on_rounds_and_verdict_alone() -> None:
    result = next_tier(
        _MID,
        _signals(
            elapsed_ms=None, rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS + 1, had_struggle=True
        ),
    )
    assert result == 2  # 拖轮(-1) + 勉强(-1) = -2，耗时缺失不参与 → 降


def test_none_elapsed_holds_when_rounds_and_verdict_split() -> None:
    result = next_tier(
        _MID,
        _signals(elapsed_ms=None, rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS + 1),
    )
    assert result == _MID  # 拖轮(-1) + 无勉强(+1) = 0 → 维持


# --- 阈值边界的包含性（钉死 <= / >= 而非 < / >）--------------------------------------


def test_fast_boundary_is_inclusive() -> None:
    # 恰好 == FAST_MS 算"快"（+1）。配无勉强(+1) = +2 → 升；若边界改成严格 < 则不算快 → 维持，红。
    result = next_tier(_MID, _signals(elapsed_ms=FAST_MS, had_struggle=False))
    assert result == 4


def test_slow_boundary_is_inclusive() -> None:
    # 恰好 == SLOW_MS 算"慢"（-1）。配勉强(-1) = -2 → 降；若改成严格 > 则不算慢 → 维持，红。
    result = next_tier(_MID, _signals(elapsed_ms=SLOW_MS, had_struggle=True))
    assert result == 2


def test_quick_rounds_boundary_is_inclusive() -> None:
    # 恰好 == QUICK_DISCHARGE_ROUNDS 算"少"（+1）。配无勉强(+1) = +2 → 升。
    result = next_tier(
        _MID, _signals(rounds_to_discharge=QUICK_DISCHARGE_ROUNDS, had_struggle=False)
    )
    assert result == 4


def test_dragged_rounds_boundary_is_inclusive() -> None:
    # 恰好 == DRAGGED_DISCHARGE_ROUNDS 算"拖"（-1）。配勉强(-1) = -2 → 降。
    result = next_tier(
        _MID, _signals(rounds_to_discharge=DRAGGED_DISCHARGE_ROUNDS, had_struggle=True)
    )
    assert result == 2


# --- 纯函数性质：同输入恒同输出 ------------------------------------------------------


def test_deterministic_same_input_same_output() -> None:
    sig = _signals(rounds_to_discharge=QUICK_DISCHARGE_ROUNDS, elapsed_ms=FAST_MS - 1)
    assert next_tier(_MID, sig) == next_tier(_MID, sig)


# --- SE-S5a：档位 → 选择题目标选项数（``target_option_count`` 纯函数，逐档 + 单调 + 边界）------


def test_target_option_count_each_tier() -> None:
    # 逐档钉死映射值（mutation：改任一档的选项数应被这条杀掉）。默认档（3）给 4 项。
    assert target_option_count(1) == 3
    assert target_option_count(2) == 3
    assert target_option_count(3) == 4
    assert target_option_count(4) == 4
    assert target_option_count(5) == 4


def test_target_option_count_default_tier_is_four() -> None:
    # 默认档（从没考过的概念起点）= 4 项——锁死"标准档给 4 项"的口径（与 DEFAULT_TIER 联动）。
    assert target_option_count(DEFAULT_TIER) == 4


def test_target_option_count_is_monotonic_non_decreasing() -> None:
    # 单调不减：难度只加不减选项，绝不出现"更难反而选项更少"（钉死设计意图）。
    counts = [target_option_count(t) for t in (1, 2, 3, 4, 5)]
    assert counts == sorted(counts)
    assert all(counts[i] <= counts[i + 1] for i in range(len(counts) - 1))


def test_target_option_count_boundaries_are_min_three_max_four() -> None:
    # 边界：最低档不低于 3 项；高档不靠堆选项制造表面难度，最多 4 项。
    assert target_option_count(1) == 3  # 下界
    assert target_option_count(5) == 4  # 上界


def test_target_option_count_deterministic() -> None:
    # 纯函数：同输入恒同输出。
    assert target_option_count(4) == target_option_count(4)


def test_high_tier_quality_policy_is_compositional() -> None:
    tier4 = distractor_quality_policy(4)
    tier5 = distractor_quality_policy(5)
    assert tier4 is not None
    assert tier5 is not None
    assert (tier4.minimum_label, tier4.minimum_reasonable) == ("较弱干扰", 1)
    assert (tier5.minimum_label, tier5.minimum_reasonable) == ("较弱干扰", 2)


def test_quality_policy_only_gates_above_default_tier_and_is_deterministic() -> None:
    assert distractor_quality_policy(1) is None
    assert distractor_quality_policy(2) is None
    assert distractor_quality_policy(DEFAULT_TIER) is None
    assert distractor_quality_policy(5) == distractor_quality_policy(5)


def test_meets_floor_合理干扰_floor_accepts_only_合理() -> None:
    # floor=合理干扰（最严，tier5）：只有"合理干扰"达标，"较弱/无效"拒。
    assert distractor_meets_floor("合理干扰", "合理干扰")
    assert not distractor_meets_floor("较弱干扰", "合理干扰")
    assert not distractor_meets_floor("无效干扰", "合理干扰")


def test_meets_floor_较弱干扰_floor_accepts_合理_and_较弱() -> None:
    # floor=较弱干扰（tier4）：合理、较弱都达标，只拒"无效干扰"。
    assert distractor_meets_floor("合理干扰", "较弱干扰")
    assert distractor_meets_floor("较弱干扰", "较弱干扰")
    assert not distractor_meets_floor("无效干扰", "较弱干扰")


def test_meets_floor_is_reflexive_at_each_label() -> None:
    # 每一档都达自身门槛（≥ 比较的自反性，钉死"恰好达标即通过"边界）。
    for label in ("合理干扰", "较弱干扰", "无效干扰"):
        assert distractor_meets_floor(label, label)


# --- SE-S6：档位 → 开放 / 追问难度提示（``difficulty_prompt_hint`` 纯函数，软杠杆）------------
# 软性如实标注：本函数是难度落到题面的**软腿**——只断言"不同档给不同提示文本 / 默认档给 None"，
# **不断言"高档提示真的让题更难"**（深度主观、外部不可验证，见 difficulty_prompt_hint docstring）。


def test_prompt_hint_high_tiers_are_non_empty_deep_hint() -> None:
    # 高档 4/5 → 非空逼深提示（含"边界"字样，钉死"问边界 / 反例"的口径，mutation：改成放缓应变红）。
    for tier in (4, 5):
        hint = difficulty_prompt_hint(tier)
        assert hint is not None
        assert hint != ""
        assert "边界" in hint  # 逼深提示的判别特征（问边界条件 / 反例 / 跨概念）


def test_prompt_hint_low_tiers_are_non_empty_easy_hint() -> None:
    # 低档 1/2 → 非空放缓提示（含"核心"字样，钉死"只问核心定义 / 基本理解"的口径）。
    for tier in (1, 2):
        hint = difficulty_prompt_hint(tier)
        assert hint is not None
        assert hint != ""
        assert "核心" in hint  # 放缓提示的判别特征（问最核心的定义 / 基本理解）


def test_prompt_hint_default_tier_is_none() -> None:
    # 默认档（3）→ None：不加提示、保持出题官自然深度（与 SE-S5 "只对非默认档加杠杆"取向一致）。
    assert difficulty_prompt_hint(3) is None
    assert difficulty_prompt_hint(DEFAULT_TIER) is None


def test_prompt_hint_high_and_low_bands_differ() -> None:
    # "各档文本不同"：逼深提示（高档）与放缓提示（低档）是**不同**的文本（钉死三挡：逼深 / 放缓 /
    # None 两两可区分）。4/5 刻意共用一句、1/2 刻意共用一句——软腿不细分成假精度的多句（见
    # difficulty_prompt_hint docstring），故断言的是**band 间**不同，而非每个 tier 都不同。
    hard = difficulty_prompt_hint(5)
    easy = difficulty_prompt_hint(1)
    assert hard is not None and easy is not None
    assert hard != easy
    # 三挡两两可区分（None 与两种非空提示都不同——None 天然与非空不等，此处显式记录设计意图）。
    assert difficulty_prompt_hint(3) is None
    assert hard != difficulty_prompt_hint(3)
    assert easy != difficulty_prompt_hint(3)


def test_prompt_hint_bands_share_within_band() -> None:
    # 设计意图钉死：4/5 共用同一逼深提示、1/2 共用同一放缓提示（软腿三挡而非五档细分）。
    assert difficulty_prompt_hint(4) == difficulty_prompt_hint(5)
    assert difficulty_prompt_hint(1) == difficulty_prompt_hint(2)


def test_prompt_hint_deterministic() -> None:
    # 纯函数：同输入恒同输出。
    assert difficulty_prompt_hint(5) == difficulty_prompt_hint(5)
    assert difficulty_prompt_hint(3) == difficulty_prompt_hint(3)

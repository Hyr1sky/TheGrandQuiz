"""难度引擎的确定性地基（SE-S1 台账 + SE-S2 跨档规则）。

自进化第一阶段给每个 KnowledgeItem 一个**离散 5 档难度**（PRD 决策 1 硬约束：不做连续分数——
沿 ``CONTEXT.md``「薄弱概念」`_Avoid_: 掌握度分数` 与「判决」`_Avoid_: 评分、分数`，全仓库用
三态状态机 / 三值判决而非连续分，难度不能成为第一个例外）。本模块只含**可独立单测的确定性单元**，
此刻没有生产消费者：

- **SE-S1 难度台账**：``DifficultyLedger`` 协议 + Dict / Sqlite 两实现，锚定 ``item_id`` 存档位。
  独立于 Learning Memory 薄弱台账（决策 2）——薄弱台账"销账即删行"，难度生命周期却是"只要考过
  就一直在"（连从没薄弱过、一路顺畅的概念也要标难度并升档，User Story 12），故另立一张表、不随
  销账丢失。协议形状照 ``asked_questions.py`` 的 Protocol + Dict + Sqlite 三段式。
- **SE-S2 跨档规则**：``next_tier`` 纯函数据三路信号（销账轮数 / 答题耗时 / 判决分布）裁决该概念
  升 / 降 / 维持一档。照 ``memory.py`` 的 ``apply_verdict``——无 I/O、不发事件、不碰随机 / 时钟。

determinism 纪律：难度表无时间戳列（``seq`` 排序）；``next_tier`` 无 clock / random；domain 层不
import time / datetime / uuid。
"""

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from grandquiz.kernel.db import connect, migrate

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 离散 5 档难度（1 最易、5 最难）。PRD 决策 1：必须离散，不做连续分数。
DifficultyTier = Literal[1, 2, 3, 4, 5]

_MIN_TIER: DifficultyTier = 1
_MAX_TIER: DifficultyTier = 5
# 5 档正中的标准档：从没考过、无任何信号的概念的确定起点（tier_of 未记录时的兜底值）。
DEFAULT_TIER: DifficultyTier = 3

# 档梯，供 next_tier 的钳制与 DB 读回的 int→Literal 收敛（索引 tuple[DifficultyTier, ...]
# 返回的元素类型即 DifficultyTier，免 cast）。
_TIER_LADDER: tuple[DifficultyTier, ...] = (1, 2, 3, 4, 5)


# ============================================================================
# SE-S1：难度台账（Protocol + Dict + Sqlite 三段式，照 asked_questions.py）
# ============================================================================


class DifficultyLedger(Protocol):
    """难度台账的结构化契约（后续 S3 写、S5/S6 读的形参类型）。

    ``tier_of``：读某 item 当前难度档；**从没记录过 → 返回默认档 ``DEFAULT_TIER``（不抛、不
    None）**，因为难度需要一个确定的起点档，缺省不是错误状态。
    ``set_tier``：幂等写覆盖某 item 的档位（每概念至多一档，后写胜出，不累积历史——难度是"当前
    状态"而非"证据历史"，与 ``asked_questions`` 的只增语义相反）。
    """

    def tier_of(self, item_id: str) -> DifficultyTier: ...
    def set_tier(self, item_id: str, tier: DifficultyTier) -> None: ...


class DictDifficultyLedger:
    """进程内难度台账（dict[item_id -> tier]），测试 / 快速用的内存实现、无 I/O。"""

    def __init__(self) -> None:
        self._tiers: dict[str, DifficultyTier] = {}

    def tier_of(self, item_id: str) -> DifficultyTier:
        """读某 item 当前难度档；未记录过 → 默认档兜底。"""
        return self._tiers.get(item_id, DEFAULT_TIER)

    def set_tier(self, item_id: str, tier: DifficultyTier) -> None:
        """幂等写覆盖某 item 的难度档（后写胜出）。"""
        self._tiers[item_id] = tier


class SqliteDifficultyLedger:
    """难度台账的 SQLite 持久化实现（满足 ``DifficultyLedger`` 协议），跨会话留存档位。

    ``db_path`` 是 learning 数据的 db 文件（与 store / memory / asked_questions 共用同一 db）；
    ``__init__`` 打开连接并跑 ``migrate``（幂等，迁移 0006 建 ``difficulty`` 表）。``set_tier`` 用
    ``INSERT OR REPLACE``（item_id 唯一约束 → 冲突即替换整行，实现幂等覆盖，照 ``memory.py`` 的
    写覆盖路数）。``seq`` 自增主键给插入序、非时间戳（决策 2：本表无任何时间戳列，读取按 item_id
    定位、不依赖时序，保证 replay 逐字节一致）。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn = connect(db_path)
        migrate(self._conn, _LEARNING_MIGRATIONS_DIR)

    def tier_of(self, item_id: str) -> DifficultyTier:
        row = self._conn.execute(
            "SELECT tier FROM difficulty WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return DEFAULT_TIER
        return _coerce_tier(int(row[0]))

    def set_tier(self, item_id: str, tier: DifficultyTier) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO difficulty (item_id, tier) VALUES (?, ?)",
            (item_id, tier),
        )
        self._conn.commit()

    def close(self) -> None:
        """关闭底层连接（跨会话验收：关闭后用同一 db_path 重开，档位仍在、不重置回默认）。"""
        self._conn.close()


def _coerce_tier(value: int) -> DifficultyTier:
    """把 DB 读回的 int 收敛为 ``DifficultyTier``；越界（脏数据）大声失败。

    ``set_tier`` 只接受 ``DifficultyTier``，故正常情况下表里恒为 1..5；越界只可能是外部篡改 /
    迁移错误，照 ``memory.py`` 的不变量哲学在读取点即 ``ValueError`` 大声失败，而非静默返回
    非法档位。索引 ``_TIER_LADDER`` 免 cast 拿到 ``DifficultyTier`` 静态类型。
    """
    if value < _MIN_TIER or value > _MAX_TIER:
        raise ValueError(f"难度档越界：{value}（合法 {_MIN_TIER}..{_MAX_TIER}）")
    return _TIER_LADDER[value - _MIN_TIER]


# ============================================================================
# SE-S2：跨档规则纯函数（据三路信号裁决升 / 降 / 维持，照 memory.py 的 apply_verdict）
# ============================================================================

# --- 可调参数集中一处（S3 接线后如需重调，只动这里；DIFFICULTY_TIER_CHANGED 的 reason
#     字段取用这些阈值解释"为什么升/降"）------------------------------------------------

# 销账轮数（= 被删除的 ConceptRecord.verdict_history 长度）：≤ QUICK 视作"很快掌握"投正票，
# ≥ DRAGGED 视作"来回拖了很多轮"投负票，其间中性。（最快销账 = 薄弱→观察中→掌握 ≈ 2 轮。）
QUICK_DISCHARGE_ROUNDS = 2
DRAGGED_DISCHARGE_ROUNDS = 4

# 答题耗时近似（QUESTION_ASKED→ANSWER_JUDGED 时间戳差，ms）：≤ FAST 投正票、≥ SLOW 投负票、
# 其间中性；None（拿不到）投 0 票（忽略耗时、只据轮数 + 判决分布裁决）。阈值是 v1 粗估、可调。
FAST_MS = 30_000
SLOW_MS = 120_000

# 三票求和后的促升 / 促降门限：净分 ≥ +2 升一档、≤ -2 降一档，其间维持。含义="至少两路信号
# 同向"才跨档——单路信号只维持，避免单一噪声（如一次偶然慢）就抖动难度。
PROMOTE_SCORE = 2
DEMOTE_SCORE = -2


class MasterySignals(BaseModel):
    """跨档裁决的三路输入信号（不可变；由 S3 从既有 trace 事件 + verdict_history 派生）。

    ``rounds_to_discharge``：本次销账花了几轮（越小越熟）。= 被删除的 ``ConceptRecord``
    ``verdict_history`` 长度，只在 ``CONCEPT_STATE_CHANGED`` 发"销账"转移那一刻可捕获（销账后
    记录连同 history 被整条删除，事后无法回读）。
    ``elapsed_ms``：本题答题耗时近似（越短越熟）；拿不到 → ``None``，规则忽略此路（见 docstring）。
    ``had_struggle``：本概念生命周期内是否掉进过"勉强"判决（三值判决之一，非分数）。为 True 表示
    答得虚 / 不稳。**刻意只读三值判决、不造"答案质量分数"**（CONTEXT.md「判决」`_Avoid_: 评分`）。
    """

    model_config = ConfigDict(frozen=True)

    rounds_to_discharge: int
    elapsed_ms: int | None
    had_struggle: bool


def next_tier(current: DifficultyTier, signals: MasterySignals) -> DifficultyTier:
    """据三路信号裁决概念的新难度档（纯函数，无 I/O、不发事件、不碰随机 / 时钟）——本期命门单元。

    **合成规则（单调可解释）**：三路信号各投一票 ∈ {+1（更熟，该更难）, 0（中性 / 缺失）,
    -1（更虚，该更易）}，票值求和为净分：

    - 销账轮数：≤ ``QUICK_DISCHARGE_ROUNDS`` → +1；≥ ``DRAGGED_DISCHARGE_ROUNDS`` → -1；其间 0。
    - 判决分布：无"勉强"（``had_struggle=False``）→ +1；掉过"勉强" → -1。（二值信号，无中性——
      每个销账概念要么全程未虚、要么虚过，都是有效信息。）
    - 答题耗时：``≤ FAST_MS`` → +1；``≥ SLOW_MS`` → -1；其间 0；``None`` → 0（**耗时缺失时明确
      忽略此路，只据轮数 + 判决分布裁决**，不崩不猜）。

    净分 ≥ ``PROMOTE_SCORE`` → **升一档**；≤ ``DEMOTE_SCORE`` → **降一档**；其间 → **维持**。
    门限 ±2 意为"至少两路信号同向"才跨档——单路信号只维持，避免单一噪声（偶然一次慢 / 一次勉强）
    抖动难度。**每次至多跨一档**（单步，不按分数比例跳档，稳），且**边界钳制**：1 档不再降、
    5 档不再升。同输入恒同输出。

    一句话解释（供 ``DIFFICULTY_TIER_CHANGED`` 的 reason 取用）：升 = "两路以上信号显示掌握得好
    （快 / 少轮 / 无勉强）"；降 = "两路以上信号显示还在挣扎（慢 / 拖轮 / 掉过勉强）"。
    """
    score = _rounds_vote(signals.rounds_to_discharge) + _verdict_vote(signals.had_struggle)
    score += _speed_vote(signals.elapsed_ms)

    if score >= PROMOTE_SCORE:
        direction = 1
    elif score <= DEMOTE_SCORE:
        direction = -1
    else:
        direction = 0

    # 索引 [0, 4] 钳制 = 边界档不越界（1 不再降、5 不再升）；索引 _TIER_LADDER 免 cast。
    index = max(0, min(len(_TIER_LADDER) - 1, current - _MIN_TIER + direction))
    return _TIER_LADDER[index]


def _rounds_vote(rounds_to_discharge: int) -> int:
    """销账轮数投票：很快掌握 +1、拖很多轮 -1、其间中性。"""
    if rounds_to_discharge <= QUICK_DISCHARGE_ROUNDS:
        return 1
    if rounds_to_discharge >= DRAGGED_DISCHARGE_ROUNDS:
        return -1
    return 0


def _verdict_vote(had_struggle: bool) -> int:
    """判决分布投票：掉过"勉强" -1、全程未虚 +1（二值，无中性）。"""
    return -1 if had_struggle else 1


def _speed_vote(elapsed_ms: int | None) -> int:
    """答题耗时投票：快 +1、慢 -1、其间中性；缺失（None）0 票（明确忽略耗时路）。"""
    if elapsed_ms is None:
        return 0
    if elapsed_ms <= FAST_MS:
        return 1
    if elapsed_ms >= SLOW_MS:
        return -1
    return 0

"""难度引擎的确定性地基（SE-S1 台账 + SE-S2 跨档规则）。

自进化第一阶段给每个 KnowledgeItem 一个**离散 5 档难度**（PRD 决策 1 硬约束：不做连续分数——
沿 ``CONTEXT.md``「薄弱概念」`_Avoid_: 掌握度分数` 与「判决」`_Avoid_: 评分、分数`，全仓库用
三态状态机 / 三值判决而非连续分，难度不能成为第一个例外）。本模块集中放置可独立单测的
确定性单元：

- **SE-S1 难度台账**：``DifficultyLedger`` 协议 + Dict / Sqlite 两实现，锚定 ``item_id`` 存档位。
  独立于 Learning Memory 薄弱台账（决策 2）——薄弱台账"销账即删行"，难度生命周期却是"只要考过
  就一直在"（连从没薄弱过、一路顺畅的概念也要标难度并升档，User Story 12），故另立一张表、不随
  销账丢失。协议形状照 ``asked_questions.py`` 的 Protocol + Dict + Sqlite 三段式。
- **SE-S2 跨档规则**：``next_tier`` 纯函数据三路信号（销账轮数 / 答题耗时 / 判决分布）裁决该概念
  升 / 降 / 维持一档。照 ``memory.py`` 的 ``apply_verdict``——无 I/O、不发事件、不碰随机 / 时钟。
- **SH-S8 统一演化**：``evolve_difficulty`` 同时消费直答、重置与销账证据；未追踪概念连续答对两次
  升一档，错 / 勉强清空直答 streak，销账继续复用三路信号。
- **SE-S5a 选择题选项数杠杆**：``target_option_count`` 纯函数把难度档映射到选择题目标选项数
  （档越高、干扰项越多、越难靠排除法蒙对），是"让难度落到题面"的第一条腿。同样确定性、无 I/O
  （被 ``assess_once`` 的选择题分支读、下传出题请求）。

determinism 纪律：难度表无时间戳列（``seq`` 排序）；``next_tier`` 无 clock / random；domain 层不
import time / datetime / uuid。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from grandquiz.domain.learning.judge import DistractorLabel
from grandquiz.domain.learning.persistence import DatabaseSource, LearningDatabase, database_from

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 离散 5 档难度（1 最易、5 最难）。PRD 决策 1：必须离散，不做连续分数。
DifficultyTier = Literal[1, 2, 3, 4, 5]
DifficultyMode = Literal["foundation", "adaptive", "challenge"]

_MIN_TIER: DifficultyTier = 1
_MAX_TIER: DifficultyTier = 5
# 5 档正中的标准档：从没考过、无任何信号的概念的确定起点（tier_of 未记录时的兜底值）。
DEFAULT_TIER: DifficultyTier = 3
DIRECT_CORRECTS_TO_PROMOTE = 2

# 档梯，供 next_tier 的钳制与 DB 读回的 int→Literal 收敛（索引 tuple[DifficultyTier, ...]
# 返回的元素类型即 DifficultyTier，免 cast）。
_TIER_LADDER: tuple[DifficultyTier, ...] = (1, 2, 3, 4, 5)


def effective_difficulty_tier(tier: DifficultyTier, mode: DifficultyMode) -> DifficultyTier:
    """Apply a bounded question-time bias without mutating the learned item tier."""
    offset = -1 if mode == "foundation" else 1 if mode == "challenge" else 0
    index = max(0, min(len(_TIER_LADDER) - 1, tier - _MIN_TIER + offset))
    return _TIER_LADDER[index]


# ============================================================================
# SE-S1：难度台账（Protocol + Dict + Sqlite 三段式，照 asked_questions.py）
# ============================================================================


class DifficultyLedger(Protocol):
    """难度台账的结构化契约（考核提交写，出题难度杠杆读）。

    ``tier_of``：读某 item 当前难度档；**从没记录过 → 返回默认档 ``DEFAULT_TIER``（不抛、不
    None）**，因为难度需要一个确定的起点档，缺省不是错误状态。
    ``set_tier``：幂等写覆盖某 item 的档位（每概念至多一档，后写胜出，不累积历史——难度是"当前
    状态"而非"证据历史"，与 ``asked_questions`` 的只增语义相反）。
    """

    def tier_of(self, item_id: str) -> DifficultyTier: ...
    def set_tier(self, item_id: str, tier: DifficultyTier) -> None: ...
    def progress_of(self, item_id: str) -> "DifficultyProgress": ...
    def set_progress(self, item_id: str, progress: "DifficultyProgress") -> None: ...


@dataclass(frozen=True)
class DifficultyProgress:
    tier: DifficultyTier = DEFAULT_TIER
    correct_streak: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.correct_streak < DIRECT_CORRECTS_TO_PROMOTE:
            raise ValueError(
                f"correct_streak 必须在 0..{DIRECT_CORRECTS_TO_PROMOTE - 1}，"
                f"实为 {self.correct_streak}"
            )


@dataclass(frozen=True)
class DirectCorrectEvidence:
    pass


@dataclass(frozen=True)
class ResetEvidence:
    pass


@dataclass(frozen=True)
class DischargeEvidence:
    signals: "MasterySignals"


DifficultyEvidence = DirectCorrectEvidence | ResetEvidence | DischargeEvidence


def evolve_difficulty(
    current: DifficultyProgress, evidence: DifficultyEvidence
) -> DifficultyProgress:
    """据一次已判定证据演化难度进度；无 I/O、每次至多跨一档。"""
    if isinstance(evidence, DirectCorrectEvidence):
        streak = current.correct_streak + 1
        if streak < DIRECT_CORRECTS_TO_PROMOTE:
            return DifficultyProgress(tier=current.tier, correct_streak=streak)
        promoted = _TIER_LADDER[min(len(_TIER_LADDER) - 1, current.tier - _MIN_TIER + 1)]
        return DifficultyProgress(tier=promoted, correct_streak=0)
    if isinstance(evidence, ResetEvidence):
        return DifficultyProgress(tier=current.tier, correct_streak=0)
    return DifficultyProgress(
        tier=next_tier(current.tier, evidence.signals),
        correct_streak=0,
    )


def difficulty_evolution_reason(
    before: DifficultyProgress,
    after: DifficultyProgress,
    evidence: DifficultyEvidence,
) -> str:
    """解释一次真跨档；与 ``evolve_difficulty`` 消费同一证据。"""
    if isinstance(evidence, DirectCorrectEvidence):
        return f"连续答对 {DIRECT_CORRECTS_TO_PROMOTE} 次——上调难度"
    if isinstance(evidence, DischargeEvidence):
        return tier_change_reason(before.tier, after.tier, evidence.signals)
    raise ValueError("重置证据不会产生难度跨档")


class DictDifficultyLedger:
    """进程内难度台账（dict[item_id -> tier]），测试 / 快速用的内存实现、无 I/O。"""

    def __init__(self) -> None:
        self._progress: dict[str, DifficultyProgress] = {}

    def tier_of(self, item_id: str) -> DifficultyTier:
        """读某 item 当前难度档；未记录过 → 默认档兜底。"""
        return self.progress_of(item_id).tier

    def set_tier(self, item_id: str, tier: DifficultyTier) -> None:
        """幂等写覆盖某 item 的难度档（后写胜出）。"""
        current = self.progress_of(item_id)
        self._progress[item_id] = DifficultyProgress(
            tier=tier, correct_streak=current.correct_streak
        )

    def progress_of(self, item_id: str) -> DifficultyProgress:
        return self._progress.get(item_id, DifficultyProgress())

    def set_progress(self, item_id: str, progress: DifficultyProgress) -> None:
        self._progress[item_id] = progress

    def replace_progress(self, item_id: str, progress: DifficultyProgress) -> None:
        self._progress[item_id] = progress

    def _snapshot_state(self) -> object:
        return dict(self._progress)

    def _restore_state(self, snapshot: object) -> None:
        if not isinstance(snapshot, dict):
            raise TypeError("Difficulty snapshot 必须是 dict")
        self._progress = snapshot  # type: ignore[assignment]


class SqliteDifficultyLedger:
    """难度台账的 SQLite 持久化实现（满足 ``DifficultyLedger`` 协议），跨会话留存档位。

    ``db_path`` 是 learning 数据的 db 文件（与 store / memory / asked_questions 共用同一 db）；
    ``__init__`` 打开连接并跑 ``migrate``（幂等，迁移 0006 建 ``difficulty`` 表）。``set_tier`` 用
    ``INSERT OR REPLACE``（item_id 唯一约束 → 冲突即替换整行，实现幂等覆盖，照 ``memory.py`` 的
    写覆盖路数）。``seq`` 自增主键给插入序、非时间戳（决策 2：本表无任何时间戳列，读取按 item_id
    定位、不依赖时序，保证 replay 逐字节一致）。
    """

    def __init__(self, db_path: DatabaseSource) -> None:
        self._db = database_from(db_path)
        self._conn = self._db.connection

    @property
    def transaction_owner(self) -> LearningDatabase:
        """显式暴露跨账本判决写入使用的 transaction owner。"""
        return self._db

    def tier_of(self, item_id: str) -> DifficultyTier:
        return self.progress_of(item_id).tier

    def progress_of(self, item_id: str) -> DifficultyProgress:
        row = self._conn.execute(
            "SELECT tier, correct_streak FROM difficulty WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return DifficultyProgress()
        return DifficultyProgress(tier=_coerce_tier(int(row[0])), correct_streak=int(row[1]))

    def set_tier(self, item_id: str, tier: DifficultyTier) -> None:
        current = self.progress_of(item_id)
        self.set_progress(
            item_id, DifficultyProgress(tier=tier, correct_streak=current.correct_streak)
        )

    def set_progress(self, item_id: str, progress: DifficultyProgress) -> None:
        self._conn.execute(
            "INSERT INTO difficulty (item_id, tier, correct_streak) VALUES (?, ?, ?) "
            "ON CONFLICT(item_id) DO UPDATE SET tier=excluded.tier, "
            "correct_streak=excluded.correct_streak",
            (item_id, progress.tier, progress.correct_streak),
        )
        self._db.commit()

    def replace_progress(self, item_id: str, progress: DifficultyProgress) -> None:
        """Replace one derived current state inside the caller's transaction."""

        self.set_progress(item_id, progress)

    def close(self) -> None:
        """关闭底层连接（跨会话验收：关闭后用同一 db_path 重开，档位仍在、不重置回默认）。"""
        self._db.close()


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

# 三票求和后的促升 / 促降门限：净分 ≥ +2 升一档、≤ -2 降一档，其间维持。
# 注意判决轴恒投 ±1（无勉强 +1 / 掉过勉强 -1，见 _verdict_vote——每个销账概念必有判决历史、
# 这一路永不中性），故实际跨档条件是**"判决基线 ±1 + 一路速度/轮数佐证推过门限"**，而非
# "两路彼此独立的信号同向"：
#   - 清爽销账（无勉强 +1）+ 任一正向佐证（快 / 少轮）→ +2 → 升；
#   - 挣扎销账（掉过勉强 -1）+ 任一负向佐证（慢 / 拖轮）→ -2 → 降；
#   - 但清爽销账 + 单个负向佐证只 = 0 → 维持（负向佐证单独抵不过判决基线、不足以降档——
#     只有真的掉过"勉强"才降；这个不对称是刻意的，避免一次偶然慢就下调难度）。
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
    **判决轴恒投 ±1**（无勉强 +1 / 掉过勉强 -1——每个销账概念必有判决历史，这一路永不中性），
    故门限 ±2 的真实含义是 **"判决基线 ± 一路速度/轮数佐证"** 而非"两路彼此独立的信号同向"：
    清爽销账（+1）+ 任一正向佐证（快 / 少轮）即升；挣扎销账（-1）+ 任一负向佐证（慢 / 拖轮）即降。
    有个**刻意的不对称**：清爽销账 + 单个负向佐证只 = 0 → 维持（负向佐证单独抵不过判决基线，
    不足以降档；只有真的掉过"勉强"才会降），避免一次偶然慢就下调难度。**每次至多跨一档**（单步，
    不按分数比例跳档，稳），且**边界钳制**：1 档不再降、5 档不再升。同输入恒同输出。

    一句话解释（供 ``DIFFICULTY_TIER_CHANGED`` 的 reason 取用）：升 = "答得又快又干脆（无勉强 +
    快 / 少轮佐证）"；降 = "答得又虚又费劲（掉过勉强 + 慢 / 拖轮佐证）"。
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


def tier_change_reason(
    from_tier: DifficultyTier, to_tier: DifficultyTier, signals: MasterySignals
) -> str:
    """据跨档方向 + 哪些信号促成，产一句中文说明（供 ``DIFFICULTY_TIER_CHANGED`` 的 reason 字段）。

    纯函数、无 I/O。**与 ``next_tier`` 同源读同一批阈值常量**（``QUICK_DISCHARGE_ROUNDS`` /
    ``DRAGGED_DISCHARGE_ROUNDS`` / ``FAST_MS`` / ``SLOW_MS``）——单独拆成 helper 便于单测，且让
    reason 措辞与跨档规则同源（改阈值只动一处、说明不与规则脱节）。它只解释"为什么跨了这一档"，
    **不重算该不该跨**（那是 ``next_tier`` 的活；本函数假定调用方已确认真跨档
    ``to_tier != from_tier``）。

    方向由 ``from/to`` 定，措辞取 ``next_tier`` docstring 末尾的口径：
    - **升档**（``to_tier > from_tier``）："答得又快又干脆"——升档由判决基线（无勉强 +1）+ 至少一路
      正向佐证（快 / 少轮）推过门限，故"没掉过勉强"必成立、必有至少一条速度 / 轮数佐证。
    - **降档**（``to_tier < from_tier``）："答得又虚又费劲"——降档必由掉过"勉强"（-1）+ 至少一路负向
      佐证（慢 / 拖轮）推过门限，故"掉过勉强"必成立、必有至少一条佐证。
    """
    if to_tier > from_tier:
        reasons: list[str] = ["全程没掉过'勉强'"]
        if signals.rounds_to_discharge <= QUICK_DISCHARGE_ROUNDS:
            reasons.append(f"{signals.rounds_to_discharge} 轮就掌握")
        if signals.elapsed_ms is not None and signals.elapsed_ms <= FAST_MS:
            reasons.append("答得快")
        return f"答得又快又干脆（{'、'.join(reasons)}）——上调难度"
    # 降档（to_tier < from_tier）
    reasons = []
    if signals.had_struggle:
        reasons.append("掉过'勉强'")
    if signals.rounds_to_discharge >= DRAGGED_DISCHARGE_ROUNDS:
        reasons.append(f"来回考了 {signals.rounds_to_discharge} 轮")
    if signals.elapsed_ms is not None and signals.elapsed_ms >= SLOW_MS:
        reasons.append("答得慢")
    return f"答得又虚又费劲（{'、'.join(reasons)}）——下调难度"


# ============================================================================
# SE-S5a：档位 → 选择题目标选项数（选择题硬杠杆①，确定性映射）
# ============================================================================

# 难度档 → 选择题目标选项数（1 正确项 + N-1 干扰项）的确定性映射。设计意图：档 ≤ 默认档给
# 最简 3 项（最易靠排除法蒙对）、默认档（3）给 4 项、高档递增到 6 项——档位越高、干扰项越多、
# 越难排除，把"难度"落到可断言的题面结构上（PRD 决策 4 杠杆①）。**单调不减**（难度只加不减
# 选项，不出现"更难反而选项更少"）。具体值是 v1 校准、可调：改映射只动这张表一处，
# ``target_option_count`` 随之变，不散落各处。5 档全覆盖（``DifficultyTier`` = 1..5）。
_TIER_OPTION_COUNTS: dict[DifficultyTier, int] = {1: 3, 2: 3, 3: 4, 4: 5, 5: 6}


def target_option_count(tier: DifficultyTier) -> int:
    """据难度档返回选择题的目标选项数（1 正确项 + N-1 干扰项）——纯函数、无 I/O、确定性。

    映射见 ``_TIER_OPTION_COUNTS``：1/2 档 3 项、3 档（默认档）4 项、4 档 5 项、5 档 6 项——
    档位越高、干扰项越多、越难靠排除法蒙对（PRD 决策 4 杠杆①）。映射**单调不减**（难度只加不
    减选项）。具体值 v1 校准、可调：只动 ``_TIER_OPTION_COUNTS`` 一处。``tier`` 由
    ``DifficultyTier`` 收敛为 1..5、5 档全覆盖，故直接索引不会 KeyError（越界档在 ``_coerce_tier``
    读取点已大声失败）。
    """
    return _TIER_OPTION_COUNTS[tier]


# ============================================================================
# SE-S5b：档位 → 选择题干扰项质量闸门（选择题硬杠杆②，确定性映射 + 达标比较纯函数）
# ============================================================================

# 干扰项档位排序（``DistractorLabel`` 的偏序，值越大越硬）：无效 < 较弱 < 合理。judge
# （``judge.py``）判每个干扰项一档，本表把三档收敛成可比较的序数，供 ``distractor_meets_floor``
# 判"够不够硬"。
# 三档全覆盖（``DistractorLabel`` = 三值），故直接索引不会 KeyError。
_DISTRACTOR_LABEL_RANK: dict[DistractorLabel, int] = {
    "无效干扰": 0,
    "较弱干扰": 1,
    "合理干扰": 2,
}

# 难度档 → 该档要求的**最低可接受干扰项档**（None = 不设 judge 闸门）的确定性映射。设计意图：干扰项
# 质量闸门是"变难"方向的杠杆，故**只对高于默认档（3）的 tier 设门**——降档 / 默认档 / 新概念不该
# 反过来要求更硬的干扰项（那会与 SE-S5a"默认档不加杠杆"的取向自相矛盾、且徒增默认路径重试耗尽
# 风险）。tier 5 → "合理干扰"（最严：拒 较弱 / 无效）；tier 4 → "较弱干扰"（拒 无效）；tier 1/2/3
# → None（不设门）。具体门槛是 v1 校准、可调：改映射只动这张表一处。5 档全覆盖。
_TIER_QUALITY_FLOOR: dict[DifficultyTier, DistractorLabel | None] = {
    1: None,
    2: None,
    3: None,
    4: "较弱干扰",
    5: "合理干扰",
}


def distractor_quality_floor(tier: DifficultyTier) -> DistractorLabel | None:
    """据难度档返回选择题干扰项的**最低可接受质量档**——纯函数、无 I/O、确定性（SE-S5b 杠杆②）。

    映射见 ``_TIER_QUALITY_FLOOR``：**只对高于默认档（3）的 tier 设 judge 闸门**——tier 5 要求全部
    干扰项达"合理干扰"（拒 较弱 / 无效）、tier 4 要求达"较弱干扰"（拒 无效）、tier 1/2/3 返回
    ``None``（不设门，档位不比默认更难时不该反要求更硬干扰项）。返回 ``None`` 时调用方
    （``assess_once`` → ``generate_multiple_choice``）**完全不调 judge**——本闸门只在升过默认档的
    概念上生效，把"难度"落到"干扰项够不够迷惑"这条可 judge 断言的题面维度上（PRD 决策 4 杠杆②）。
    具体门槛 v1 校准、可调：只动 ``_TIER_QUALITY_FLOOR`` 一处。``tier`` 由 ``DifficultyTier`` 收敛
    为 1..5、5 档全覆盖，故直接索引不会 KeyError。
    """
    return _TIER_QUALITY_FLOOR[tier]


def distractor_meets_floor(label: DistractorLabel, floor: DistractorLabel) -> bool:
    """判 judge 给某干扰项的 ``label`` 是否达到（≥）要求的最低档 ``floor``——纯函数、确定性。

    按 ``_DISTRACTOR_LABEL_RANK`` 的偏序比较（无效 < 较弱 < 合理）：``rank(label) >= rank(floor)``
    即达标。用在 ``generate_multiple_choice`` 的 judge 验收闸门里——任一干扰项不达标即 ``ModelRetry``
    重生成。两参都由 ``DistractorLabel`` 收敛为三值、全覆盖，故直接索引不会 KeyError。
    """
    return _DISTRACTOR_LABEL_RANK[label] >= _DISTRACTOR_LABEL_RANK[floor]


# ============================================================================
# SE-S6：档位 → 开放题 / 追问的难度提示（**软杠杆**，确定性映射；如实承认比 MC 硬杠杆软）
# ============================================================================

# 高档（4/5）逼深提示：让出题官问边界 / 反例 / 跨概念联系 / 易忽略细节，而非最基础的核心定义。
# 低档（1/2）放缓提示：让出题官只问核心定义 / 基本理解，别问偏门细节。文本集中一处、可调
# （改措辞只动这两个常量）。**刻意用完整自然语言句子而非结构化指令**——它作为一条追加的 user
# message 直接进出题上下文（append-pattern，见 ``question._append_difficulty_hint``），越像人给
# 出题官的口头难度要求越自然。
_HARD_PROMPT_HINT = (
    "这是高难度考核：请就该知识点问边界条件 / 反例 / 与其他概念的联系 / "
    "易被忽略的细节，不要问最基础的核心定义。"
)
_EASY_PROMPT_HINT = "这是入门考核：请就该知识点问最核心的定义 / 基本理解，不要问偏门细节。"

# 难度档 → 追加给开放 / 追问出题的难度提示文本（None = 不加提示、保持出题官自然深度）的确定性映射。
# 设计（沿 SE-S5 "只对非默认档加杠杆"的取向）：**只对非默认档（3）生效**——高档 4/5 共用逼深提示、
# 低档 1/2 共用放缓提示、默认档 3 → None（新概念 / 从没考过的确定起点保持自然深度）。
# **为何 4/5 共用而不细分（1/2 同理）**：本条是 PRD 决策 4 明确承认的**软腿**——不像 MC 的"选项数 /
# 干扰项质量闸门"能被 judge 确定性断言，开放题"深度"是主观的、外部不可验证（见 ``difficulty_prompt_
# hint`` docstring 与 issue 06 的软性如实标注）。既然连"高档题真的更难"都无法断言，把逼深提示细分成
# 4/5 两条措辞略异的句子只是**假精度**（false precision）：真正有意义、可外部区分的粒度是"逼深 vs
# 放缓 vs 不干预"三挡，故按此三挡设计，与 issue 06 "4/5 共用一句"的口径一致。具体措辞 v1 校准、
# 可调：只动本表与上面两个常量。5 档全覆盖（``DifficultyTier`` = 1..5）。
_TIER_PROMPT_HINTS: dict[DifficultyTier, str | None] = {
    1: _EASY_PROMPT_HINT,
    2: _EASY_PROMPT_HINT,
    3: None,
    4: _HARD_PROMPT_HINT,
    5: _HARD_PROMPT_HINT,
}


def difficulty_prompt_hint(tier: DifficultyTier) -> str | None:
    """据难度档返回**追加给开放 / 追问出题**的难度提示文本（None = 不加提示）——纯函数、无 I/O。

    映射见 ``_TIER_PROMPT_HINTS``：**只对非默认档（3）生效**——高档 4/5 → 逼深提示（问边界 / 反例 /
    跨概念 / 易忽略细节）、低档 1/2 → 放缓提示（只问核心定义 / 基本理解）、默认档 3 → ``None``
    （保持出题官自然深度，新概念的确定起点）。返回 ``None`` 时调用方（``generate_question`` 经
    ``assess_once``）**不追加任何 message**——发出的 message / replay_key / prompt 版本号逐字节等价
    改动前（cassette 不破的命根）。

    **软性如实标注（PRD 决策 4 授权，issue 06）**：这是难度落到题面的最后一条腿，比 MC 硬杠杆软。
    MC 的选项数 / 干扰项质量闸门是可被确定性代码 / judge 断言的结构性杠杆；开放题的"深度"是主观的、
    外部不可验证——本函数**只保证不同档追加不同提示文本**（逼深 / 放缓 / 不干预三挡），**不保证也无法
    断言"高档题真的更难"**。故 4/5（及 1/2）刻意共用一句而不细分成措辞略异的多句：既然连"更难"都
    断言不了，细分只是假精度；真正可外部区分的粒度就是这三挡。具体措辞 v1 校准、可调：只动
    ``_TIER_PROMPT_HINTS`` 与 ``_HARD_PROMPT_HINT`` / ``_EASY_PROMPT_HINT`` 常量。``tier`` 由
    ``DifficultyTier`` 收敛为 1..5、5 档全覆盖，故直接索引不会 KeyError（越界档在 ``_coerce_tier``
    读取点已大声失败）。

    **未做（留后续）**：issue 06 可选辅助杠杆② "证据条选择"（高档优先引更冷门证据条）本增量刻意
    跳过，保持聚焦在 prompt-hint 这条主软杠杆上。
    """
    return _TIER_PROMPT_HINTS[tier]

"""Learning Memory——薄弱概念的确定性台账（三态状态机 + 连对销账）。

``Memory`` 协议是记忆的结构化契约（选题 / 判卷编排依赖它）；两种实现满足它：

- ``LearningMemory``：**进程内 dict**、无 I/O——测试 / 快速用的内存实现（不再是骨架欠账）。
- ``SqliteLearningMemory``：**SQLite 持久化**——跨会话薄弱点留存、重启后仍薄弱优先出题（M7）。

两者共用**纯函数状态机** ``apply_verdict``（状态转移不重写），故行为逐字段一致；因都满足
``Memory`` 协议，调用方（``assess_once`` / ``select_target``）签名一字不改即可替换实现。

ADR-0003："Learning Memory = 薄弱概念 × 表现历史，考核循环的持久层、选题优先级的唯一数据源"；
ADR-0004："LLM 判卷，代码记账"——状态转移是**确定性纯代码**，不由 LLM 碰。记忆锚定
``KnowledgeItem.item_id``（ADR-0002 的概念同一性边界）。

**状态只有两个在记忆里**——薄弱 / 观察中；"销账"不是第三个枚举值，而是**从台账移除**
（掌握了就不再追踪）。四条转移（``apply_verdict`` 逐条实现，是缝 2 的命门单元）：

- 任一情形，verdict 为 错 / 勉强 → 概念进入 / 回到 **薄弱**，consecutive_correct 归 0
  （含"观察中"复发被打回薄弱）。
- 概念当前 **薄弱** 且 verdict 为 对 → 转 **观察中**，consecutive_correct = 1。
- 概念当前 **观察中** 且 verdict 为 对 → **销账**（连对两次才算掌握，防蒙对 / 假掌握）。
- 概念**不在记忆** 且 verdict 为 对 → **不追踪**（答对非薄弱概念不入记忆）。

竖切先穿透：进程内 dict、无任何 I/O，让考核循环后半段（选题 / 判卷 / 销账）早点在事件脊柱上
亮起来；M7 换 SQLite 支持的 Memory 抽象时调用方（``assess_once`` / ``select_target``）签名不变，
跨会话持久与"重启后仍薄弱优先出题"的不变量留给 M7 验收。
"""

import json
from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.kernel.db import connect, migrate

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 记忆里只存这两个状态；销账 = 从 dict 移除（不是第三个枚举值）。
ConceptState = Literal["薄弱", "观察中"]
# 转移后的对外表达：追踪态（薄弱 / 观察中）或"销账"（已移除）；None 表示未追踪（不进记忆）。
ToState = Literal["薄弱", "观察中", "销账"]

# verdict 属"勉强 / 错"→ 概念（回到）薄弱。与 grading 的判决三值一致。
_WEAK_VERDICTS: frozenset[VerdictLabel] = frozenset({"勉强", "错"})
# 连对达此次数即销账（"连对两次才算掌握"）。
_DISCHARGE_THRESHOLD = 2


class ConceptRecord(BaseModel):
    """一个被追踪的薄弱概念在记忆里的记录（不可变快照，锚定 ``item_id``）。

    ``consecutive_correct``：连续答对次数——薄弱恒 0，观察中恒 1（到 2 即销账、记录消失）。
    ``verdict_history``：该记录生命周期内每次判决的追加历史（销账 = 记录移除，历史随之清空；
    再次入薄弱是全新记录）。
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    state: ConceptState
    consecutive_correct: int
    verdict_history: list[VerdictLabel]

    @model_validator(mode="after")
    def _check_state_count_invariant(self) -> Self:
        # 不变量：薄弱 ↔ 连对 0、观察中 ↔ 连对 1。
        # apply_verdict 的"对"路径只看 consecutive_correct 判销账、不读 state；
        # 若 M7 持久层反序列化出 state 与 count 不一致（薄弱却 count=1），单次答对会误销账。
        # 故构造点即拒非法记录，令脏数据大声失败而非静默错误销账。
        expected = 0 if self.state == "薄弱" else 1
        if self.consecutive_correct != expected:
            raise ValueError(
                f"ConceptRecord 不变量破坏：state={self.state} 应有 cc=={expected}、"
                f"实为 {self.consecutive_correct}"
            )
        return self


class Transition(BaseModel):
    """一次 ``record_verdict`` 的转移信息，供 ``assess_once`` 发 CONCEPT_STATE_CHANGED 事件。

    ``from_state`` None = 记录此前不在记忆（未追踪）；``to_state`` None = 记录此后仍不在记忆
    （未追踪 / 答对非薄弱概念），``"销账"`` = 此前追踪、现已移除（掌握）。
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    from_state: ConceptState | None
    to_state: ToState | None
    consecutive_correct: int


def apply_verdict(
    record: ConceptRecord | None, verdict: VerdictLabel, *, item_id: str
) -> ConceptRecord | None:
    """按四条转移算出概念的新记录（纯函数，无 I/O、不发事件）——缝 2 的命门单元。

    返回 None 表示该概念**不该在记忆里**：或是销账（掌握），或是答对了一个未追踪的非薄弱概念。
    ``item_id`` 是必填锚点：``record`` 为 None（未追踪）而 verdict 为错 / 勉强时，需据它铸出一条
    全新薄弱记录——故显式传入而非仅从 ``record`` 取（这是本函数相对 spec 2 参签名的必要补充）。
    """
    if verdict in _WEAK_VERDICTS:
        # 任一情形（含未追踪 / 观察中复发）：进入 / 回到薄弱，连对计数归 0。
        history: list[VerdictLabel] = (
            [*record.verdict_history, verdict] if record is not None else [verdict]
        )
        return ConceptRecord(
            item_id=item_id, state="薄弱", consecutive_correct=0, verdict_history=history
        )
    # verdict == "对"
    if record is None:
        return None  # 不在记忆 + 对 → 不追踪
    new_count = record.consecutive_correct + 1
    if new_count >= _DISCHARGE_THRESHOLD:
        return None  # 观察中 + 对 → 连对两次 → 销账（移除）
    # 薄弱 + 对 → 观察中（连对计数 1）
    return ConceptRecord(
        item_id=item_id,
        state="观察中",
        consecutive_correct=new_count,
        verdict_history=[*record.verdict_history, verdict],
    )


class Memory(Protocol):
    """薄弱概念台账的结构化契约（``assess_once`` / ``select_target`` 的形参类型）。

    dict 版（``LearningMemory``）与 SQLite 版（``SqliteLearningMemory``）都结构上满足它，
    故调用方按此协议编程、可无改动地替换实现。唯一写入口是 ``record_verdict``（代码记账）；
    其余三个是只读投影（供选题的薄弱优先候选集与断言）。销账 = 从台账移除。
    """

    def record_verdict(self, item_id: str, verdict: VerdictLabel) -> Transition: ...
    def weak_item_ids(self) -> set[str]: ...
    def state_of(self, item_id: str) -> ConceptState | None: ...
    def record_of(self, item_id: str) -> ConceptRecord | None: ...


class LearningMemory:
    """薄弱概念的进程内台账（dict[item_id -> ConceptRecord]），测试 / 快速用的内存实现、无 I/O。

    唯一写入口是 ``record_verdict``（代码记账）；``weak_item_ids`` / ``state_of`` / ``record_of``
    是只读投影，供选题（薄弱优先候选集）与断言使用。销账 = 从 dict 移除。
    """

    def __init__(self) -> None:
        self._records: dict[str, ConceptRecord] = {}

    def record_verdict(self, item_id: str, verdict: VerdictLabel) -> Transition:
        """按 ``verdict`` 更新 ``item_id`` 的记录并返回转移信息（销账 = 移除）。"""
        before = self._records.get(item_id)
        from_state = before.state if before is not None else None
        after = apply_verdict(before, verdict, item_id=item_id)
        if after is None:
            self._records.pop(item_id, None)
            if before is not None:
                # 此前追踪、现已移除 → 销账（唯一路径：观察中 + 对）。透出触发销账的连对数（=2）。
                return Transition(
                    item_id=item_id,
                    from_state=from_state,
                    to_state="销账",
                    consecutive_correct=before.consecutive_correct + 1,
                )
            # 此前未追踪、现仍未追踪 → 答对非薄弱概念，不入记忆。
            return Transition(
                item_id=item_id, from_state=None, to_state=None, consecutive_correct=0
            )
        self._records[item_id] = after
        return Transition(
            item_id=item_id,
            from_state=from_state,
            to_state=after.state,
            consecutive_correct=after.consecutive_correct,
        )

    def weak_item_ids(self) -> set[str]:
        """当前被追踪的概念 item_id 集合（薄弱 ∪ 观察中；不含已销账）——薄弱优先选题的数据源。"""
        return set(self._records.keys())

    def state_of(self, item_id: str) -> ConceptState | None:
        """某 item 当前状态（薄弱 / 观察中）；不在记忆（未追踪 / 已销账）→ None。"""
        record = self._records.get(item_id)
        return record.state if record is not None else None

    def record_of(self, item_id: str) -> ConceptRecord | None:
        """某 item 的完整记录（含 verdict_history）；不在记忆 → None。只读投影。"""
        return self._records.get(item_id)


class SqliteLearningMemory:
    """薄弱概念的 SQLite 持久化台账（M7 正式实现，满足 ``Memory`` 协议）。

    ``db_path`` 是 learning 数据的**独立 db 文件**（与 trace.db 分开）；``__init__`` 打开连接并跑
    ``migrate``（幂等）。``record_verdict`` **复用纯函数 ``apply_verdict``**（状态机不重写）：读当前
    行 → ``apply_verdict`` → 写回（``INSERT OR REPLACE``）/ 删除（销账 = ``DELETE`` 行）→ 返回
    ``Transition``，逐字段与 dict 版一致。反序列化经 ``ConceptRecord.model_validate``——脏行
    （如薄弱却 cc=1）在**构造点即被不变量 validator 拒**，令脏数据大声失败而非静默错误销账。
    SQLite 是 I/O 但确定，schema 无时间戳列，不破坏 replay。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn = connect(db_path)
        migrate(self._conn, _LEARNING_MIGRATIONS_DIR)

    def record_verdict(self, item_id: str, verdict: VerdictLabel) -> Transition:
        """按 ``verdict`` 更新 ``item_id`` 的记录并返回转移信息（销账 = ``DELETE`` 行）。"""
        before = self._read_record(item_id)
        from_state = before.state if before is not None else None
        after = apply_verdict(before, verdict, item_id=item_id)
        if after is None:
            self._conn.execute("DELETE FROM learning_memory WHERE item_id = ?", (item_id,))
            self._conn.commit()
            if before is not None:
                # 此前追踪、现已移除 → 销账（唯一路径：观察中 + 对）。透出触发销账的连对数（=2）。
                return Transition(
                    item_id=item_id,
                    from_state=from_state,
                    to_state="销账",
                    consecutive_correct=before.consecutive_correct + 1,
                )
            # 此前未追踪、现仍未追踪 → 答对非薄弱概念，不入记忆。
            return Transition(
                item_id=item_id, from_state=None, to_state=None, consecutive_correct=0
            )
        self._write_record(after)
        return Transition(
            item_id=item_id,
            from_state=from_state,
            to_state=after.state,
            consecutive_correct=after.consecutive_correct,
        )

    def weak_item_ids(self) -> set[str]:
        """当前被追踪的概念 item_id 集合（薄弱 ∪ 观察中；不含已销账）——薄弱优先选题的数据源。"""
        cursor = self._conn.execute("SELECT item_id FROM learning_memory")
        return {str(row[0]) for row in cursor.fetchall()}

    def state_of(self, item_id: str) -> ConceptState | None:
        """某 item 当前状态（薄弱 / 观察中）；不在记忆（未追踪 / 已销账）→ None。"""
        record = self._read_record(item_id)
        return record.state if record is not None else None

    def record_of(self, item_id: str) -> ConceptRecord | None:
        """某 item 的完整记录（含 verdict_history）；不在记忆 → None。只读投影。"""
        return self._read_record(item_id)

    def close(self) -> None:
        """关闭底层连接（跨会话验收：关闭后用同一 db_path 重开，薄弱点仍在、状态 / 连对不变）。"""
        self._conn.close()

    def _read_record(self, item_id: str) -> ConceptRecord | None:
        row = self._conn.execute(
            "SELECT item_id, state, consecutive_correct, verdict_history "
            "FROM learning_memory WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        # model_validate 触发 M3.3 的不变量 model_validator：脏行（薄弱↔cc0 / 观察中↔cc1 破坏）
        # 在此构造点即失败（ValidationError），而非被 apply_verdict 静默错误销账。
        return ConceptRecord.model_validate(
            {
                "item_id": str(row[0]),
                "state": row[1],
                "consecutive_correct": int(row[2]),
                "verdict_history": json.loads(row[3]),
            }
        )

    def _write_record(self, record: ConceptRecord) -> None:
        history_json = json.dumps(record.verdict_history, sort_keys=True, ensure_ascii=False)
        self._conn.execute(
            "INSERT OR REPLACE INTO learning_memory "
            "(item_id, state, consecutive_correct, verdict_history) VALUES (?, ?, ?, ?)",
            (record.item_id, record.state, record.consecutive_correct, history_json),
        )
        self._conn.commit()

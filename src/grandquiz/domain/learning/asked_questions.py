"""已问过去重台账——跨会话持久化"item_id → 已问过的题目文本"（skeleton-ledger.md #8 修复）。

修的真实 bug：此前唯一的"已问过"记忆是 ``assess_once`` 的 ``recently_asked``
参数——一个会话内进程内 ``dict[str, list[str]]``，随 CLI 进程退出而丢失。复考同一薄弱概念若
跨会话（关掉 CLI 重开），系统对"上次问过什么"完全没有记忆，可能逐字重问旧题——"无重复出题"
这条防线（M8 修的那个 dogfood bug）此前只在**单次会话内**成立，跨会话就不成立。

**刻意跟 ``recently_asked`` 分开、不是替换它**：``recently_asked`` 同时承担两件事——
① 已问过的题目文本（喂"换角度"提示 + 去重校验门）② 本会话已考过的 item 集合（喂"mixed"
覆盖优先选题，避免会话内重复覆盖同一概念）。②是**会话范围**的概念（"这次坐下来学，尽量覆盖
没考过的"），把它也做成跨会话持久会改变"mixed"的语义——一个用过一阵子的知识库里几乎每个概念
都被问过，"覆盖优先"就永远没有"没问过的"可选，退化成恒定薄弱/随机。故本模块只承接①，②仍由
调用方的 ``recently_asked`` 会话内 dict 负责，两条数据各司其职。

镜像 Learning Memory / Preference Memory 的成熟形态：``AskedQuestionsLedger`` 协议 + 两种
实现（``DictAskedQuestionsLedger`` 进程内、``SqliteAskedQuestionsLedger`` 持久化），调用方
按协议编程、可无改动替换实现。
"""

from copy import deepcopy
from pathlib import Path
from typing import Protocol

from grandquiz.domain.learning.persistence import DatabaseSource, LearningDatabase, database_from

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class AskedQuestionsLedger(Protocol):
    """已问过台账的结构化契约（``assess_once`` 的可选形参类型）。

    ``asked_before``：读某 item 历史上问过的全部题目文本（跨会话累积、按问过的顺序）。
    ``record_asked``：追加一条新问过的题目文本。无删除 / 修改接口——同 Learning Memory 的
    ``verdict_history``，这份记忆只增不减（决策一致：历史证据不该被事后篡改）。
    """

    def asked_before(self, item_id: str, *, limit: int | None = None) -> list[str]: ...
    def record_asked(self, item_id: str, question: str) -> None: ...


class DictAskedQuestionsLedger:
    """进程内台账（dict[item_id -> list[question]]），测试 / 快速用的内存实现，无 I/O。"""

    def __init__(self) -> None:
        self._asked: dict[str, list[str]] = {}

    def asked_before(self, item_id: str, *, limit: int | None = None) -> list[str]:
        """读某 item 已问过的题目文本；未问过 → 空列表。只读投影。"""
        questions = self._asked.get(item_id, [])
        if limit is None:
            return list(questions)
        return list(questions[-limit:]) if limit > 0 else []

    def record_asked(self, item_id: str, question: str) -> None:
        """追加一条新问过的题目文本（后写追加，不覆盖）。"""
        self._asked.setdefault(item_id, []).append(question)

    def _snapshot_state(self) -> object:
        return deepcopy(self._asked)

    def _restore_state(self, snapshot: object) -> None:
        if not isinstance(snapshot, dict):
            raise TypeError("AskedQuestions snapshot 必须是 dict")
        self._asked = snapshot  # type: ignore[assignment]


class SqliteAskedQuestionsLedger:
    """已问过台账的 SQLite 持久化实现（M7 同款 SQLite 化路数），满足 ``AskedQuestionsLedger`` 协议。

    ``db_path`` 是 learning 数据的 db 文件（与 store / memory / preferences 共用同一 db）；
    ``__init__`` 打开连接并跑 ``migrate``（幂等，迁移 0005 建 ``asked_questions`` 表）。``seq``
    自增主键给出确定性的插入序（而非时间戳——决策 2：本表无任何时间戳列，题目的先后来自插入序，
    不来自墙上时间，保证 replay 逐字节一致）。
    """

    def __init__(self, db_path: DatabaseSource) -> None:
        self._db = database_from(db_path)
        self._conn = self._db.connection

    @property
    def transaction_owner(self) -> LearningDatabase:
        """显式暴露跨账本判决写入使用的 transaction owner。"""
        return self._db

    def asked_before(self, item_id: str, *, limit: int | None = None) -> list[str]:
        if limit is not None and limit <= 0:
            return []
        if limit is None:
            rows = self._conn.execute(
                "SELECT question FROM asked_questions WHERE item_id = ? ORDER BY seq",
                (item_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT question FROM ("
                "SELECT seq, question FROM asked_questions WHERE item_id = ? "
                "ORDER BY seq DESC LIMIT ?"
                ") ORDER BY seq",
                (item_id, limit),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def record_asked(self, item_id: str, question: str) -> None:
        self._conn.execute(
            "INSERT INTO asked_questions (item_id, question) VALUES (?, ?)",
            (item_id, question),
        )
        self._db.commit()

    def close(self) -> None:
        """关闭底层连接（跨会话验收：关闭后用同一 db_path 重开，已问过的题仍在）。"""
        self._db.close()

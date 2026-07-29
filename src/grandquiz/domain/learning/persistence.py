"""learning.db 的共享连接与事务所有者。"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, Self, runtime_checkable

from grandquiz.kernel.db import connect, migrate, transaction

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class LearningDatabase:
    """让 Store 与各领域台账共享一个 SQLite 连接和事务边界。"""

    def __init__(self, db_path: str | Path) -> None:
        self.connection = connect(db_path)
        migrate(self.connection, _LEARNING_MIGRATIONS_DIR)
        self._transaction_depth = 0

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        with transaction(self.connection) as conn:
            self._transaction_depth += 1
            try:
                yield conn
            finally:
                self._transaction_depth -= 1

    def commit(self) -> None:
        """独立写立即提交；外层 unit-of-work 内由最外层事务统一提交。"""
        if self._transaction_depth == 0:
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()


DatabaseSource = str | Path | LearningDatabase


def database_from(source: DatabaseSource) -> LearningDatabase:
    return source if isinstance(source, LearningDatabase) else LearningDatabase(source)


@runtime_checkable
class TransactionParticipant(Protocol):
    """公开声明某 SQLite Adapter 参与哪个 LearningDatabase 事务。"""

    @property
    def transaction_owner(self) -> LearningDatabase: ...


class LearningPersistence:
    """拥有一条 learning.db 连接及其全部持久 Adapter 的生命周期。

    各账本继续保持独立 Interface；本 Module 只集中连接 owner、具名装配和关闭动作，避免调用者依赖
    位置敏感元组或记住每新增一个账本就多关一次共享连接。
    """

    def __init__(self, db_path: str | Path) -> None:
        # 延迟导入避免 Adapter 模块导入 ``persistence`` 基础类型时形成循环。
        from grandquiz.domain.learning.acquisition import AcquisitionLedger
        from grandquiz.domain.learning.asked_questions import SqliteAskedQuestionsLedger
        from grandquiz.domain.learning.difficulty import SqliteDifficultyLedger
        from grandquiz.domain.learning.memory import SqliteLearningMemory
        from grandquiz.domain.learning.preference import SqlitePreferenceMemory
        from grandquiz.domain.learning.store import SqliteLearningStore

        self._database = LearningDatabase(db_path)
        self.store: SqliteLearningStore = SqliteLearningStore(self._database)
        self.memory: SqliteLearningMemory = SqliteLearningMemory(self._database)
        self.preferences: SqlitePreferenceMemory = SqlitePreferenceMemory(self._database)
        self.asked_questions: SqliteAskedQuestionsLedger = SqliteAskedQuestionsLedger(
            self._database
        )
        self.difficulty: SqliteDifficultyLedger = SqliteDifficultyLedger(self._database)
        self.acquisitions: AcquisitionLedger = AcquisitionLedger(self._database)
        self._closed = False

    @property
    def transaction_owner(self) -> LearningDatabase:
        """一次判决跨账本写入时使用的显式 SQLite transaction owner。"""
        return self._database

    def close(self) -> None:
        """只由 owner 关闭一次共享连接；重复收尾保持幂等。"""
        if self._closed:
            return
        self._database.close()
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

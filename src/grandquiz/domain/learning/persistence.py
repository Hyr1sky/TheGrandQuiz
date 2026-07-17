"""learning.db 的共享连接与事务所有者。"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

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

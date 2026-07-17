"""SQLite 连接 + 迁移执行器。

迁移用版本号（``PRAGMA user_version``）+ 顺序 SQL 文件，不上 alembic（见 CLAUDE.md）。
迁移文件里**禁止任何时间戳 / 非确定性内容**——replay 要求逐字节一致，时间戳会毁掉回放。
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开一个 SQLite 连接。``":memory:"`` 供测试 / 回放用。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection]:
    """在最外层调用处提交或回滚；同连接嵌套调用复用现有事务。"""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def migrate(conn: sqlite3.Connection, migrations_dir: str | Path = _MIGRATIONS_DIR) -> None:
    """按 ``PRAGMA user_version`` 增量应用 ``migrations_dir/*.sql``（通用迁移执行器）。

    发现 ``migrations_dir`` 下所有 ``.sql``，按文件名前导整数排序；对每个编号 > 当前
    ``user_version`` 的文件，把**其 SQL 与版本号推进放进同一事务**原子提交（DDL + ``PRAGMA
    user_version`` 一起 COMMIT）。故中途某文件失败时 ``user_version`` 停在**最后成功编号**，
    重跑从下一个未应用文件续上——不会"DDL 已提交但版本没跟上、重跑旧文件报错"。

    ``migrations_dir`` 参数化让 migrate 成为**领域无关**的通用 runner：kernel 自身的
    ``events`` 表走默认 ``kernel/migrations``（TraceStore 调 ``migrate(conn)`` 不变、向后兼容），
    domain 层传入自己的迁移目录（如 ``domain/learning/migrations``）复用同一 runner——kernel
    仍不认识任何领域表。``user_version`` 是 per-db 的，故 learning 数据须用独立 db 文件
    （与 trace.db 分开），各自维护 ``user_version`` 与迁移序列、互不串号。
    """
    current = _user_version(conn)
    for number, path in _discover_migrations(Path(migrations_dir)):
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        # DDL 与版本号推进同事务原子提交：executescript 先 COMMIT 挂起事务，再跑显式 BEGIN…COMMIT；
        # PRAGMA user_version 写入随本事务落盘；失败未达 COMMIT → rollback，版本停在上一个成功编号。
        # PRAGMA 不能参数化，number 是文件名解析出的 int，拼 f-string 安全。
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        except Exception:
            conn.rollback()
            raise


def _user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    migrations: list[tuple[int, Path]] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        number = int(path.name.split("_", 1)[0])
        migrations.append((number, path))
    migrations.sort(key=lambda item: item[0])
    return migrations

"""SQLite 连接 + 迁移执行器。

迁移用版本号（``PRAGMA user_version``）+ 顺序 SQL 文件，不上 alembic（见 CLAUDE.md）。
迁移文件里**禁止任何时间戳 / 非确定性内容**——replay 要求逐字节一致，时间戳会毁掉回放。
"""

import sqlite3
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开一个 SQLite 连接。``":memory:"`` 供测试 / 回放用。"""
    return sqlite3.connect(str(db_path))


def migrate(conn: sqlite3.Connection) -> None:
    """按 ``PRAGMA user_version`` 增量应用 ``migrations/*.sql``。

    发现 migrations 目录下所有 ``.sql``，按文件名前导整数排序；对每个编号 > 当前
    ``user_version`` 的文件，在一个事务里执行其 SQL，最后把 ``user_version`` 抬到
    已应用的最高编号。中途失败则整文件回滚。
    """
    current = _user_version(conn)
    highest = current
    for number, path in _discover_migrations():
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        # executescript 会先 COMMIT 再执行脚本；显式 BEGIN/COMMIT 把整个迁移文件包成
        # 一个事务，中途失败未达 COMMIT，异常后 rollback，保证原子应用。
        try:
            conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
        except Exception:
            conn.rollback()
            raise
        highest = number
    if highest != current:
        # PRAGMA 不能参数化；highest 是从文件名解析出来的 int，拼 f-string 安全。
        conn.execute(f"PRAGMA user_version = {highest}")
        conn.commit()


def _user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _discover_migrations() -> list[tuple[int, Path]]:
    migrations: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        number = int(path.name.split("_", 1)[0])
        migrations.append((number, path))
    migrations.sort(key=lambda item: item[0])
    return migrations

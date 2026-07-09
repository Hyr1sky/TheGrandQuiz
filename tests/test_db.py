"""通用迁移执行器测试——``migrate(conn, migrations_dir)`` 参数化（M7）。

钉死：默认走 kernel/migrations（建 events 表、TraceStore 行为不变）；指向 domain learning
migrations 时建出 learning 四表；重复 migrate 幂等（``user_version`` 挡住不重跑）；两套迁移各自
per-db 的 user_version（独立 db 文件不串号）。kernel 仍不认识任何领域表——它只按目录跑 SQL。
"""

import sqlite3
from pathlib import Path

import pytest

import grandquiz.domain.learning
from grandquiz.kernel.db import connect, migrate

_KERNEL_TABLES = {"events"}
# 全局 KB 终态（ADR-0005，迁移 0004 弃 tasks 表）：resources 内容寻址、无 tasks 表。
_LEARNING_TABLES = {"resources", "knowledge_items", "learning_memory", "preferences"}
_LEARNING_MIGRATIONS = Path(grandquiz.domain.learning.__file__).parent / "migrations"


def _tables(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {str(row[0]) for row in cursor.fetchall()}


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def test_migrate_default_creates_events_table() -> None:
    # 默认目录 = kernel/migrations（TraceStore 走的路径）：建出 events 表、user_version 抬到 1。
    conn = connect(":memory:")
    migrate(conn)
    assert _tables(conn) >= _KERNEL_TABLES
    assert _user_version(conn) == 1
    conn.close()


def test_migrate_learning_dir_creates_learning_tables() -> None:
    # 指向 domain learning migrations：建出 learning 四表，且不含 kernel 的 events 表（各自独立）。
    conn = connect(":memory:")
    migrate(conn, _LEARNING_MIGRATIONS)
    tables = _tables(conn)
    assert tables >= _LEARNING_TABLES
    assert "events" not in tables
    assert "tasks" not in tables  # ADR-0005：迁移 0004 弃 tasks 表（LearningTask 消解）
    # user_version 抬到已应用迁移的最高编号（新增迁移文件时自动跟随，不写死具体数字——
    # 与 migrate 内部"按文件名前导整数取最高"的推进逻辑一致）。
    highest = max(int(p.name.split("_", 1)[0]) for p in _LEARNING_MIGRATIONS.glob("*.sql"))
    assert _user_version(conn) == highest
    conn.close()


def test_migrate_is_idempotent_via_user_version() -> None:
    # 重复 migrate：user_version 已达最高编号，不重跑（重跑会因 CREATE TABLE 重名而报错）。
    conn = connect(":memory:")
    migrate(conn, _LEARNING_MIGRATIONS)
    first = _user_version(conn)
    migrate(conn, _LEARNING_MIGRATIONS)  # 幂等：不抛错、不重建
    migrate(conn, _LEARNING_MIGRATIONS)
    assert _user_version(conn) == first
    assert _tables(conn) >= _LEARNING_TABLES
    conn.close()


def test_migrate_stops_at_last_good_version_on_failure(tmp_path: Path) -> None:
    # 崩溃安全（M7 终审修复）：靠后的迁移文件失败 → user_version 停在上一个成功编号，
    # 前一个的 DDL + 版本号已原子提交，重跑从下一个未应用文件续上、不会重放旧文件报表已存在。
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_ok.sql").write_text("CREATE TABLE a (x INTEGER);", encoding="utf-8")
    (migrations / "0002_bad.sql").write_text("CREATE TABLE ;;; not valid sql", encoding="utf-8")
    conn = connect(":memory:")

    with pytest.raises(sqlite3.Error):
        migrate(conn, migrations)

    # 0001 已原子提交（表在、版本=1）；0002 失败未推进到 2。
    assert "a" in _tables(conn)
    assert _user_version(conn) == 1
    conn.close()

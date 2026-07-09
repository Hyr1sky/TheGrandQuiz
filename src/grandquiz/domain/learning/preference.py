"""Preference Memory——显式设置的个人偏好台账（ADR-0003 的 M7 组成部分）。

镜像 ``memory.py`` 的 ``LearningMemory`` 成熟形态：一个结构化契约（``PreferenceMemory`` 协议）+
两种实现，调用方按协议编程、可无改动替换实现：

- ``DictPreferenceMemory``：**进程内 dict**、无 I/O——测试 / 快速用的内存实现。
- ``SqlitePreferenceMemory``：**SQLite 持久化**——跨会话留存偏好（复用 ``kernel/db.py`` 的
  ``migrate``，迁移 0003 建 ``preferences`` 表）。

与 Learning Memory（薄弱概念台账，行为随判卷状态机演化）不同，Preference 是**显式设置**的键值：
唯一写入口是 ``set_preference``（代码记账，非 LLM 产），``confidence`` 现恒 ``1.0``（显式设置）。
``confidence`` 字段本身是为二期"从行为隐式推断偏好 + 置信度"预留的形状缝，MVP 不填别的值。

第一个被消费的偏好是 ``question_language``（键 ``QUESTION_LANGUAGE_KEY``）：``assess_once`` 出题前
读它决定出题 / 判卷语言，有效语言优先级 **偏好 > 硬兜底"中文"**（见 assessment）。语言是跨全库的
个人设置——ADR-0005 消解 ``LearningTask`` 后不再有 task 层语言，偏好即唯一显式来源。

确定性纪律（否则 replay 对不齐）：本模块**不 import time / random / datetime / uuid**——偏好是显式
键值、无时序含义，表 schema 亦无时间戳列（决策 2）。
"""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.kernel.db import connect, migrate

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 第一个被消费的偏好键：出题语言（偏好 > 硬兜底中文；ADR-0005 后无 task 层语言）。
QUESTION_LANGUAGE_KEY = "question_language"

# 显式设置的偏好置信度恒此值（MVP 无隐式推断，故不留时间 / 行为漂移入口）。
_EXPLICIT_CONFIDENCE = 1.0


class Preference(BaseModel):
    """一条被显式设置的偏好（不可变快照）：键 → 值 + 置信度。

    ``confidence`` 现恒 ``1.0``（显式设置）；``ge=0 / le=1`` 约束为二期隐式推断预留合法区间。
    无时间戳字段（决策 2）——偏好是显式键值、无时序含义。
    """

    model_config = ConfigDict(frozen=True)

    key: str
    value: str
    confidence: float = Field(default=_EXPLICIT_CONFIDENCE, ge=0.0, le=1.0)


class PreferenceMemory(Protocol):
    """偏好台账的结构化契约（``assess_once`` 的形参类型）。

    dict 版（``DictPreferenceMemory``）与 SQLite 版（``SqlitePreferenceMemory``）都结构上满足它，
    故调用方按此协议编程、可无改动替换实现。唯一写入口是 ``set_preference``（显式设置）；
    ``get_preference`` 是只读投影，供出题语言解析与断言。
    """

    def set_preference(self, key: str, value: str) -> None: ...
    def get_preference(self, key: str) -> Preference | None: ...


class DictPreferenceMemory:
    """偏好的进程内台账（dict[key -> Preference]），测试 / 快速用的内存实现、无 I/O。

    ``set_preference`` 以恒 ``1.0`` 置信度写入（后写覆盖前写）；``get_preference`` 只读投影。
    """

    def __init__(self) -> None:
        self._prefs: dict[str, Preference] = {}

    def set_preference(self, key: str, value: str) -> None:
        """显式设置 ``key`` 的偏好值（confidence 恒 1.0，后写覆盖前写）。"""
        self._prefs[key] = Preference(key=key, value=value, confidence=_EXPLICIT_CONFIDENCE)

    def get_preference(self, key: str) -> Preference | None:
        """读某键的偏好；未设置 → None。只读投影。"""
        return self._prefs.get(key)


class SqlitePreferenceMemory:
    """偏好的 SQLite 持久化台账（M7 正式实现，满足 ``PreferenceMemory`` 协议）。

    ``db_path`` 是 learning 数据的 db 文件（与 memory / store 共用同一 db，与 trace 库分开）；
    ``__init__`` 打开连接并跑 ``migrate``（幂等，迁移 0003 建 ``preferences`` 表）。写走
    ``set_preference``（``INSERT OR REPLACE``、后写覆盖前写），读走 ``get_preference``（单行经
    ``model_validate`` 反序列化）。schema 无时间戳列，不破坏 replay。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn = connect(db_path)
        migrate(self._conn, _LEARNING_MIGRATIONS_DIR)

    def set_preference(self, key: str, value: str) -> None:
        """显式设置 ``key`` 的偏好值（confidence 恒 1.0，``INSERT OR REPLACE`` 覆盖）。"""
        self._conn.execute(
            "INSERT OR REPLACE INTO preferences (key, value, confidence) VALUES (?, ?, ?)",
            (key, value, _EXPLICIT_CONFIDENCE),
        )
        self._conn.commit()

    def get_preference(self, key: str) -> Preference | None:
        """读某键的偏好；未设置 → None。只读投影。"""
        row = self._conn.execute(
            "SELECT key, value, confidence FROM preferences WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return Preference.model_validate(
            {"key": str(row[0]), "value": str(row[1]), "confidence": float(row[2])}
        )

    def close(self) -> None:
        """关闭底层连接（跨会话验收：关闭后用同一 db_path 重开，偏好仍在、confidence 不变）。"""
        self._conn.close()

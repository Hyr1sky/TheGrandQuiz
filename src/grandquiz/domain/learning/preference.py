"""Preference Memory——显式设置的个人偏好台账（ADR-0003 的 M7 组成部分）。

镜像 ``memory.py`` 的 ``LearningMemory`` 成熟形态：一个结构化契约（``PreferenceMemory`` 协议）+
两种实现，调用方按协议编程、可无改动替换实现：

- ``DictPreferenceMemory``：**进程内 dict**、无 I/O——测试 / 快速用的内存实现。
- ``SqlitePreferenceMemory``：**SQLite 持久化**——跨会话留存偏好（复用 ``kernel/db.py`` 的
  ``migrate``，迁移 0003 建 ``preferences`` 表）。

与 Learning Memory（薄弱概念台账，行为随判卷状态机演化）不同，Preference 原本是**显式设置**的
键值：``set_preference`` 默认仍以 ``confidence=1.0`` 写入（代码记账）。二期"从行为隐式推断偏好 +
置信度"这条预留的形状缝现已部分兑现：``set_preference`` 接受可选 ``confidence`` 关键字（不传时
向后兼容原行为），``record_inferred_preference`` 是推断侧的策略函数——显式设置（confidence==1.0）
永不被推断覆盖；推断值与已有推断一致则置信度递增（有上限，与显式的 1.0 区分开）；不一致则以新
观察重新起步。首个具体推断：``detect_language`` 从用户文本的字符集比例判断中文 / 英文（纯确定性
字符分类，不调 LLM——语言这个维度不需要判断力，用得上的地方留给更需要判断的维度）。

第一个被消费的偏好是 ``question_language``（键 ``QUESTION_LANGUAGE_KEY``）：``assess_once`` 出题前
读它决定出题 / 判卷语言，有效语言优先级 **偏好 > 硬兜底"中文"**（见 assessment）。语言是跨全库的
个人设置；没有材料或标题层的语言属性，偏好即唯一显式来源（ADR-0005）。

确定性纪律（否则 replay 对不齐）：本模块**不 import time / random / datetime / uuid**——偏好是显式
键值、无时序含义，表 schema 亦无时间戳列（决策 2）。
"""

import unicodedata
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.difficulty import DifficultyMode
from grandquiz.domain.learning.persistence import DatabaseSource, database_from

_LEARNING_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# 第一个被消费的偏好键：出题语言（偏好 > 硬兜底中文；ADR-0005 后无 task 层语言）。
QUESTION_LANGUAGE_KEY = "question_language"
DIFFICULTY_MODE_KEY = "difficulty_mode"
ASR_MATERIAL_HINTS_KEY = "asr_material_hints_enabled"

QuestionLanguage = Literal["中文", "英文"]


def resolve_question_language(memory: "PreferenceMemory") -> QuestionLanguage:
    preference = memory.get_preference(QUESTION_LANGUAGE_KEY)
    if preference is not None and preference.value in {"中文", "英文"}:
        return cast("QuestionLanguage", preference.value)
    return "中文"


def resolve_difficulty_mode(memory: "PreferenceMemory") -> DifficultyMode:
    preference = memory.get_preference(DIFFICULTY_MODE_KEY)
    if preference is not None and preference.value in {
        "foundation",
        "adaptive",
        "challenge",
    }:
        return cast("DifficultyMode", preference.value)
    return "adaptive"


def resolve_asr_material_hints(memory: "PreferenceMemory", *, default: bool) -> bool:
    preference = memory.get_preference(ASR_MATERIAL_HINTS_KEY)
    if preference is None:
        return default
    return preference.value == "true"


# 显式设置的偏好置信度恒此值——同时是"推断永不覆盖显式"判断的分界线（>= 此值即视为显式）。
_EXPLICIT_CONFIDENCE = 1.0

# 推断偏好的置信度区间：首次观察给下限，同值复现每次递增一档，封顶于上限——
# 上限刻意 < 1.0，让"推断出的"与"显式设置的"在数值上永远可区分。
_INFERRED_INITIAL_CONFIDENCE = 0.6
_INFERRED_CONFIDENCE_STEP = 0.1
_INFERRED_MAX_CONFIDENCE = 0.95

# detect_language 的最小信号字符数（wide + ascii 字母）——太短的文本（"ok"/"是"）语言信号太弱，
# 判了也可能是噪声，宁可不判（返回 None，调用方不写入偏好）。
_MIN_LANGUAGE_SIGNAL_CHARS = 4
_WIDE_EAW = frozenset({"W", "F"})


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
    故调用方按此协议编程、可无改动替换实现。写入口是 ``set_preference``（``confidence`` 不传即
    显式设置的 1.0，向后兼容；``record_inferred_preference`` 传更低的推断置信度）；
    ``get_preference`` 是只读投影，供出题语言解析与断言。
    """

    def set_preference(
        self, key: str, value: str, *, confidence: float = _EXPLICIT_CONFIDENCE
    ) -> None: ...
    def get_preference(self, key: str) -> Preference | None: ...


class DictPreferenceMemory:
    """偏好的进程内台账（dict[key -> Preference]），测试 / 快速用的内存实现、无 I/O。

    ``set_preference`` 以恒 ``1.0`` 置信度写入（后写覆盖前写）；``get_preference`` 只读投影。
    """

    def __init__(self) -> None:
        self._prefs: dict[str, Preference] = {}

    def set_preference(
        self, key: str, value: str, *, confidence: float = _EXPLICIT_CONFIDENCE
    ) -> None:
        """设置 ``key`` 的偏好值（``confidence`` 不传即显式设置的 1.0，后写覆盖前写）。"""
        self._prefs[key] = Preference(key=key, value=value, confidence=confidence)

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

    def __init__(self, db_path: DatabaseSource) -> None:
        self._db = database_from(db_path)
        self._conn = self._db.connection

    def set_preference(
        self, key: str, value: str, *, confidence: float = _EXPLICIT_CONFIDENCE
    ) -> None:
        """设置 ``key`` 的偏好值（``confidence`` 不传即显式的 1.0，``INSERT OR REPLACE`` 覆盖）。"""
        self._conn.execute(
            "INSERT INTO preferences (key, value, confidence) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, confidence=excluded.confidence",
            (key, value, confidence),
        )
        self._db.commit()

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
        self._db.close()


def detect_language(text: str) -> Literal["中文", "英文"] | None:
    """从文本的字符集比例判断主要语言——纯确定性字符分类，不调 LLM（这个维度不需要判断力）。

    East-Asian Wide/Fullwidth（CJK/全角）计中文信号，ASCII 字母计英文信号；两类信号总数低于
    ``_MIN_LANGUAGE_SIGNAL_CHARS`` 视为"信号太弱"（如"ok"/"是"这类短应答，判了也可能是噪声）
    → 返回 ``None``，调用方据此不写入偏好，而非把噪声记成一次观察。信号足够时取多数一方。
    """
    wide = sum(1 for ch in text if unicodedata.east_asian_width(ch) in _WIDE_EAW)
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if wide + ascii_letters < _MIN_LANGUAGE_SIGNAL_CHARS:
        return None
    return "中文" if wide >= ascii_letters else "英文"


def record_inferred_preference(memory: PreferenceMemory, key: str, value: str) -> None:
    """把一次行为观察折进偏好台账——推断侧的策略函数（LLM/确定性信号只产候选值，这里定写不写）。

    三条规则：① 已有偏好是**显式设置**（``confidence >= _EXPLICIT_CONFIDENCE``）→ 推断永不覆盖，
    直接跳过（同 ADR-0006"显式 > 自适应"的精神）。② 已有偏好是**此前的推断**且与本次观察值相同
    → 置信度递增一档（封顶 ``_INFERRED_MAX_CONFIDENCE``，与显式的 1.0 保持可区分）——多次一致
    观察让偏好更可信。③ 无偏好，或此前推断值与本次不同 → 以初始置信度重新起步（信号不一致，
    不该继续累积旧信号的置信度）。
    """
    existing = memory.get_preference(key)
    if existing is not None and existing.confidence >= _EXPLICIT_CONFIDENCE:
        return
    if existing is not None and existing.value == value:
        confidence = min(existing.confidence + _INFERRED_CONFIDENCE_STEP, _INFERRED_MAX_CONFIDENCE)
    else:
        confidence = _INFERRED_INITIAL_CONFIDENCE
    memory.set_preference(key, value, confidence=confidence)

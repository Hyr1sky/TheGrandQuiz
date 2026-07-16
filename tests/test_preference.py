"""Preference Memory 测试（M7，ADR-0003）——显式语言偏好的确定性台账 + 出题语言覆盖。

三簇断言：

- **dict ↔ SQLite parity（逐字段含 confidence）**：同一 set 序列喂 dict 版与 SQLite 版，
  ``get_preference`` 逐字段一致（key / value / confidence 全比，吸取 01 教训别漏字段）。
- **跨会话留存**：SQLite 版 set → 关闭连接、丢弃对象 → 同一 db_path 重开 → get 仍在、confidence 稳。
- **出题语言优先级（偏好 > 硬兜底"中文"）**：assess_once 读 ``question_language`` 偏好下传出题
  （``LearningTask`` 已消解，语言只来自偏好，ADR-0005）；去掉偏好覆盖（mutation）则"pref=英文 →
  应出英文"断言转红。
- **确定性无 clock / random 泄漏**：preference 模块不 import time / random / datetime / uuid，
  confidence 恒 1.0（显式设置、不随时间漂移）。
"""

import json
from collections.abc import Sequence
from pathlib import Path

from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
)
from grandquiz.domain.learning.preference import (
    QUESTION_LANGUAGE_KEY,
    DictPreferenceMemory,
    PreferenceMemory,
    SqlitePreferenceMemory,
    detect_language,
    record_inferred_preference,
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Completion, Message, Role, Usage

# --- Preference 模型 + 显式 set/get ------------------------------------------------


def test_set_then_get_returns_preference_with_confidence_one() -> None:
    mem = DictPreferenceMemory()
    mem.set_preference("question_language", "英文")
    pref = mem.get_preference("question_language")
    assert pref is not None
    assert pref.key == "question_language"
    assert pref.value == "英文"
    assert pref.confidence == 1.0  # 显式设置恒 1.0


def test_get_missing_preference_is_none() -> None:
    mem = DictPreferenceMemory()
    assert mem.get_preference("question_language") is None


def test_set_overwrites_previous_value() -> None:
    mem = DictPreferenceMemory()
    mem.set_preference("question_language", "英文")
    mem.set_preference("question_language", "日文")
    pref = mem.get_preference("question_language")
    assert pref is not None
    assert pref.value == "日文"  # 后写覆盖前写


# --- dict ↔ SQLite parity（逐字段含 confidence）------------------------------------


def test_dict_sqlite_parity_field_by_field() -> None:
    ops = [("question_language", "英文"), ("theme", "dark"), ("question_language", "日文")]
    dict_mem: PreferenceMemory = DictPreferenceMemory()
    sqlite_mem: PreferenceMemory = SqlitePreferenceMemory(":memory:")
    for key, value in ops:
        dict_mem.set_preference(key, value)
        sqlite_mem.set_preference(key, value)
    for key in ("question_language", "theme", "missing"):
        d = dict_mem.get_preference(key)
        s = sqlite_mem.get_preference(key)
        if d is None:
            assert s is None
            continue
        assert s is not None
        # 逐字段比对（不只靠 __eq__）——含 confidence，防某版漏写字段被 None-vs-None 掩盖。
        assert d.key == s.key
        assert d.value == s.value
        assert d.confidence == s.confidence
        assert d == s


# --- 跨会话留存 -------------------------------------------------------------------


def test_preference_survives_close_and_reopen(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"

    mem1 = SqlitePreferenceMemory(db)
    mem1.set_preference("question_language", "英文")
    assert mem1.get_preference("question_language") is not None
    mem1.close()
    del mem1

    mem2 = SqlitePreferenceMemory(db)
    pref = mem2.get_preference("question_language")
    assert pref is not None
    assert pref.value == "英文"
    assert pref.confidence == 1.0  # 跨会话 confidence 不变
    mem2.close()


# --- 确定性无 clock / random 泄漏 ---------------------------------------------------


def test_preference_module_has_no_clock_or_random_leak() -> None:
    import ast

    from grandquiz.domain.learning import preference as pref_module

    source = Path(pref_module.__file__).read_text(encoding="utf-8")
    forbidden = {"time", "random", "datetime", "uuid"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[0])
    assert not (imported & forbidden), f"泄漏了非确定性模块：{imported & forbidden}"


# --- 出题语言优先级（偏好 > 硬兜底"中文"）------------------------------------------
#
# 自包含（不 import 共享 harness）：脚本化假 provider 从被替换后的 system prompt 判定语言，
# 回一道对应语言的 MC JSON——既证明有效语言确被下传替换进 message，又让题干可按 CJK 比例断言语言。

_QUOTE = "闭包捕获的是变量而非值"
_CHINESE_Q = "请解释闭包捕获的是变量本身还是值的快照。"
_ENGLISH_Q = "Please explain whether a closure captures the variable itself or a value snapshot."
_CHINESE_OPTIONS = ["值的快照", "变量本身"]
_ENGLISH_OPTIONS = ["a value snapshot", "the variable itself"]
_CORRECT = "变量本身"


class _LanguageEchoProvider:
    """从 system prompt 里被替换后的 ``请用 <语言>`` 指令判定语言，返回对应语言的 MC JSON。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        system = messages[0].content
        english = "请用 英文" in system
        payload = {
            "question": _ENGLISH_Q if english else _CHINESE_Q,
            "options": _ENGLISH_OPTIONS if english else _CHINESE_OPTIONS,
            "answer_index": 1,
            "cited_evidence": [_QUOTE],
        }
        return Completion(text=json.dumps(payload, ensure_ascii=False), usage=Usage())


def _cjk_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    han = sum(1 for c in chars if "一" <= c <= "鿿")
    return han / len(chars)


def _stocked_store() -> LearningStore:
    store = LearningStore()
    resource = LearningResource.create(url="mem://closure")
    store.add_resource(resource)
    store.add_items(
        [
            KnowledgeItem.create(
                resource_id=resource.resource_id,
                concept="闭包",
                summary="函数捕获定义时所在作用域的变量",
                evidence=[Evidence(quote=_QUOTE)],
                confidence=0.9,
            )
        ]
    )
    return store


async def _question_asked(*, preferences: PreferenceMemory | None) -> str:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="pref")
    await assess_once(
        store=_stocked_store(),
        provider=_LanguageEchoProvider(),
        responder=ScriptedResponder(answer=_CORRECT),
        memory=LearningMemory(),
        emitter=emitter,
        rng=new_rng(0),
        preferences=preferences,
    )
    return next(
        str(e.payload["question"]) for e in events if e.type == LearningEvent.QUESTION_ASKED
    )


async def test_preference_sets_question_language_to_english() -> None:
    # 偏好生效：question_language=英文 → 出英文题（ADR-0005：语言只来自偏好，无 task 默认）。
    # mutation：assess_once 若不读偏好（恒用"中文"兜底）→ 出中文题 → 本断言转红。
    prefs = DictPreferenceMemory()
    prefs.set_preference(QUESTION_LANGUAGE_KEY, "英文")
    question = await _question_asked(preferences=prefs)
    assert _cjk_ratio(question) < 0.1  # 英文题（偏好生效）


async def test_chinese_fallback_when_preferences_none() -> None:
    # 中文兜底：不传 preferences（None）→ 出中文题（偏好 > 中文，无偏好即兜底）。
    question = await _question_asked(preferences=None)
    assert _cjk_ratio(question) > 0.6  # 中文题


async def test_chinese_fallback_when_preference_unset() -> None:
    # 中文兜底：传了偏好台账但未设 question_language → 仍出中文题（无该键即兜底）。
    prefs = DictPreferenceMemory()  # 未设 question_language
    question = await _question_asked(preferences=prefs)
    assert _cjk_ratio(question) > 0.6  # 中文题


async def test_preference_set_to_chinese_asks_in_chinese() -> None:
    # 显式把偏好设中文 → 出中文题（与英文用例对称，证明偏好确实驱动语言）。
    prefs = DictPreferenceMemory()
    prefs.set_preference(QUESTION_LANGUAGE_KEY, "中文")
    question = await _question_asked(preferences=prefs)
    assert _cjk_ratio(question) > 0.6  # 中文题


# --- detect_language：纯确定性字符分类，不调 LLM --------------------------------------


def test_detect_language_chinese_text() -> None:
    assert detect_language("请解释闭包捕获的是变量本身还是值") == "中文"


def test_detect_language_english_text() -> None:
    assert detect_language("How does a closure capture variables") == "英文"


def test_detect_language_too_short_returns_none() -> None:
    # 信号太弱（"ok"/"是"这类短应答）→ None，不该被记成一次观察。
    assert detect_language("ok") is None
    assert detect_language("是") is None
    assert detect_language("") is None


def test_detect_language_picks_majority_in_mixed_text() -> None:
    # 中文里夹了英文术语（"closure"）：中文字符仍占多数 → 判中文。
    assert detect_language("闭包 closure 捕获的是变量而非值的快照") == "中文"


# --- record_inferred_preference：推断侧的写入策略（显式永不覆盖 / 一致递增 / 不一致重起步）------


def test_record_inferred_preference_writes_initial_confidence_when_unset() -> None:
    mem = DictPreferenceMemory()
    record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "英文")
    pref = mem.get_preference(QUESTION_LANGUAGE_KEY)
    assert pref is not None
    assert pref.value == "英文"
    assert 0.0 < pref.confidence < 1.0  # 推断值恒 < 1.0（与显式区分）


def test_record_inferred_preference_never_overrides_explicit() -> None:
    mem = DictPreferenceMemory()
    mem.set_preference(QUESTION_LANGUAGE_KEY, "中文")  # 显式设置，confidence=1.0
    record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "英文")  # 推断出不同值
    pref = mem.get_preference(QUESTION_LANGUAGE_KEY)
    assert pref is not None
    assert pref.value == "中文"  # 显式设置未被推断覆盖
    assert pref.confidence == 1.0


def test_record_inferred_preference_increments_confidence_on_repeated_agreement() -> None:
    mem = DictPreferenceMemory()
    record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "英文")
    first = mem.get_preference(QUESTION_LANGUAGE_KEY)
    assert first is not None
    record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "英文")  # 再次观察到同一值
    second = mem.get_preference(QUESTION_LANGUAGE_KEY)
    assert second is not None
    assert second.confidence > first.confidence  # 一致观察 → 置信度递增
    assert second.confidence < 1.0  # 但恒不到显式的 1.0


def test_record_inferred_preference_caps_confidence_below_explicit() -> None:
    mem = DictPreferenceMemory()
    for _ in range(50):  # 反复一致观察也不能逼近甚至撞上显式的 1.0
        record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "英文")
    pref = mem.get_preference(QUESTION_LANGUAGE_KEY)
    assert pref is not None
    assert pref.confidence < 1.0


def test_record_inferred_preference_restarts_on_disagreement() -> None:
    mem = DictPreferenceMemory()
    record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "英文")
    record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "英文")  # 置信度已递增过一次
    boosted = mem.get_preference(QUESTION_LANGUAGE_KEY)
    assert boosted is not None
    record_inferred_preference(mem, QUESTION_LANGUAGE_KEY, "中文")  # 观察到不同值 → 重新起步
    restarted = mem.get_preference(QUESTION_LANGUAGE_KEY)
    assert restarted is not None
    assert restarted.value == "中文"
    assert restarted.confidence < boosted.confidence  # 不是继续累加旧信号的置信度

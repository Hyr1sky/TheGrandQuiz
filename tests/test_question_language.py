"""出题语言可配置测试（M8 → GKB-S2）——``question_language`` 偏好下传出题 / 判卷的 {{LANGUAGE}} 槽。

``LearningTask`` 已消解（ADR-0005）：出题语言是**跨全库的个人设置**，只来自 Preference Memory
（偏好 > 硬兜底"中文"），不再是 task / 材料属性。自包含（不 import 共享 harness）：本地脚本化假
provider 从系统提示里**被替换后**的语言指令判定用哪种语言出题——这既证明 ``{{LANGUAGE}}`` 确实被
下传替换进了发出的 message，又让 QUESTION_ASKED 的 question 带上可按 CJK 比例断言的语言特征。

断言（对应 issue）：
(a) 无偏好（兜底中文）下每条 QUESTION_ASKED 的 question 按 CJK 比例为中文，且跨轮一致；
(b) ``question_language`` 偏好设成"英文"时 question 为英文（CJK 比例趋零）；
(c) 两种语言下 prompt 版本号相同（模板含字面 {{LANGUAGE}}、哈希对象不变），而发出的 message 不同。
"""

import json
from collections.abc import Sequence
from typing import cast

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
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, Usage

_QUOTE = "闭包捕获的是变量而非值"
_CORRECT_OPTION = "变量本身"
# fresh memory → 路由到选择题（MC）；答对未追踪概念 → 不入记忆 → 每轮仍 MC，故题型跨轮一致。
_CHINESE_Q = "请解释闭包捕获的是变量本身还是值的快照。"
_ENGLISH_Q = "Please explain whether a closure captures the variable itself or a value snapshot."
# 题干与选项都随目标语言变——AC 要求断言"每题（question / options）语言 == 有效语言"，故选项也需带
# 可断言的语言特征（中文正确项恒为 _CORRECT_OPTION，使中文轮的 ScriptedResponder 判对、概念保持
# 未追踪 → 每轮仍 MC）。
_CHINESE_OPTIONS = ["值的快照", _CORRECT_OPTION]
_ENGLISH_OPTIONS = ["a value snapshot", "the variable itself"]


class _LanguageEchoProvider:
    """脚本化假 provider：从 system prompt 里被替换后的语言指令判定语言，返回对应语言的 MC JSON。

    出题走 role=enrich；判卷（本测试恒 MC，走确定性代码）不打此 provider。``cited_evidence`` 恒引
    真实证据（与题目语言无关），使锚定门放行。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        system = messages[0].content
        english = "请用 英文" in system  # {{LANGUAGE}} 被替换成"英文"的证据
        question = _ENGLISH_Q if english else _CHINESE_Q
        options = _ENGLISH_OPTIONS if english else _CHINESE_OPTIONS
        payload = {
            "question": question,
            "options": options,
            "answer_index": 1,
            "cited_evidence": [_QUOTE],
        }
        return Completion(text=json.dumps(payload, ensure_ascii=False), usage=Usage())


def _cjk_ratio(text: str) -> float:
    """非空白字符里汉字（CJK 统一表意文字）的占比——中文题趋 1、英文题趋 0。"""
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


def _prefs_for(language: str | None) -> PreferenceMemory | None:
    if language is None:
        return None
    prefs = DictPreferenceMemory()
    prefs.set_preference(QUESTION_LANGUAGE_KEY, language)
    return prefs


async def _run(provider: _LanguageEchoProvider, *, language: str | None = None) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="lang")
    await assess_once(
        store=_stocked_store(),
        provider=provider,
        # 恒答对正确项 → 未追踪概念保持未追踪 → 每轮仍路由到 MC（题型跨轮一致）。
        responder=ScriptedResponder(answer=_CORRECT_OPTION),
        memory=LearningMemory(),
        emitter=emitter,
        rng=new_rng(0),
        preferences=_prefs_for(language),
    )
    return events


def _questions_asked(events: list[AgentEvent]) -> list[str]:
    return [str(e.payload["question"]) for e in events if e.type == LearningEvent.QUESTION_ASKED]


def _options_asked(events: list[AgentEvent]) -> list[str]:
    # 摊平所有 QUESTION_ASKED 的 options——AC 要求断言选项语言，不只题干。
    opts: list[str] = []
    for e in events:
        if e.type == LearningEvent.QUESTION_ASKED:
            raw = e.payload.get("options")
            if isinstance(raw, list):
                opts.extend(str(o) for o in cast("list[object]", raw))
    return opts


async def test_default_language_is_chinese_and_consistent_across_turns() -> None:
    # (a) 无偏好（兜底中文）：每轮 QUESTION_ASKED 的 question 按 CJK 比例为中文，且跨轮一致。
    provider = _LanguageEchoProvider()

    questions: list[str] = []
    options: list[str] = []
    for _ in range(3):  # 多轮：答对未追踪概念保持未追踪 → 每轮仍 MC
        events = await _run(provider, language=None)
        questions.extend(_questions_asked(events))
        options.extend(_options_asked(events))

    # 跨轮一致 = 每轮题干 + 选项都落在有效语言桶（中文）；比 len(set)==1 有意义——后者对返回
    # 常量的假 provider 恒真、无回归保护，故不用它，改断言"每一条都为中文"。
    assert len(questions) == 3
    for q in questions:
        assert _cjk_ratio(q) > 0.6  # 中文题：汉字占绝对多数
    assert options  # MC 必带选项
    for opt in options:
        assert _cjk_ratio(opt) > 0.6  # 选项也为中文（AC：question / options 均随有效语言）


async def test_english_preference_asks_in_english() -> None:
    # (b) question_language 偏好="英文"：question 为英文（CJK 比例趋零）。
    provider = _LanguageEchoProvider()

    events = await _run(provider, language="英文")
    questions = _questions_asked(events)
    options = _options_asked(events)

    assert len(questions) == 1
    assert _cjk_ratio(questions[0]) < 0.1  # 英文题：无汉字
    assert options  # MC 必带选项
    for opt in options:
        assert _cjk_ratio(opt) < 0.1  # 选项也为英文（AC：question / options 均随有效语言）


def _model_started(events: list[AgentEvent]) -> AgentEvent:
    return next(e for e in events if e.type == EventType.MODEL_STARTED)


async def test_prompt_version_stable_across_languages_but_message_differs() -> None:
    # (c) 中文 / 英文两轮：出题 model span 的 prompt_version 相同（模板含字面 {{LANGUAGE}}、
    #     哈希对象不变），而发出的 system message 不同（语言指令被替换成不同语言）。
    zh_events = await _run(_LanguageEchoProvider(), language=None)  # 兜底中文
    en_events = await _run(_LanguageEchoProvider(), language="英文")

    zh_started = _model_started(zh_events)
    en_started = _model_started(en_events)

    assert zh_started.payload["prompt_version"] == en_started.payload["prompt_version"]

    zh_system = str(zh_started.payload["messages"][0]["content"])
    en_system = str(en_started.payload["messages"][0]["content"])
    assert zh_system != en_system  # 发出的 message 按语言不同
    assert "请用 中文" in zh_system
    assert "请用 英文" in en_system

"""无重复出题测试——三条缝，全自包含（本地假 provider + 本地 EventSink，不 import 既有测试）。

修真机 dogfood 暴露的"连续两轮题目内容完全相同"：复考锁定薄弱概念是设计意图（ADR-0003），
要修的是"重问同一道题"。修在出题侧（"LLM 判卷，代码记账"）——代码持有会话内"已问过"台账，
出题时把已问过的题作为"换角度"约束下传，并在结构化输出门加归一化去重校验（重复即 ModelRetry）。

- 缝 2：归一化 + 命中判定的纯函数单测（去重 key = NFKC + 去空白 / 标点 + 转小写后相等）。
- 缝 3：asked_before 含某题 → 假 provider 返回归一化相等的题 → 出题门触发去重重试；持续重复 →
  重试用尽 QuestionError（gate 生效）；再调返回不同题 → 有界重试救回（照 test_question 用公共出题
  函数驱动 gate，不直接调私有 _parse / _parse_mc）。
- 缝 1：多轮会话（同一薄弱 item 复考两轮）——假 provider 若不被约束会重复，断言两轮
  QUESTION_ASKED 的题目文本归一化后不相等（会话内零逐字重复）。
"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.domain.learning.asked_questions import DictAskedQuestionsLedger
from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.assessment.question import (
    QuestionError,
    dedup_key,
    generate_multiple_choice,
    generate_question,
    is_duplicate,
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.providers.base import Completion, Message, Role, Usage

_QUOTE = "闭包捕获的是变量而非值"
_SEED = 42


# --- 缝 2：归一化 / 命中判定纯函数 ---------------------------------------------------


def test_dedup_key_ignores_whitespace_punctuation_and_case() -> None:
    # 去重 key：NFKC + 去空白 / 标点 + 转小写后相等。空白、标点、大小写差异都被吸收。
    assert dedup_key("What IS a Closure?") == dedup_key("what is a closure")
    assert dedup_key("闭包，是什么？") == dedup_key("闭包是什么")
    assert dedup_key(" 闭包\t是\n什么 ？ ") == dedup_key("闭包是什么")


def test_dedup_key_nfkc_folds_fullwidth_to_halfwidth() -> None:
    # NFKC：全角字母 / 数字 / 标点折叠到半角——真机全 / 半角混排不该被当成不同的题。
    assert dedup_key("ＡＢＣ１２３") == dedup_key("abc123")
    assert dedup_key("闭包（closure）？") == dedup_key("闭包 closure")


def test_dedup_key_distinguishes_genuinely_different_questions() -> None:
    # 只吸收表面差异，不吸收实质差异：换角度的题归一化后仍不相等（否则去重会误杀合法换题）。
    assert dedup_key("什么是闭包？") != dedup_key("闭包如何捕获变量？")


def test_is_duplicate_hits_on_normalized_equality() -> None:
    asked = ["什么是闭包？"]
    assert is_duplicate("什么是闭包?", asked)  # 半角问号
    assert is_duplicate("  什么是闭包  ？ ", asked)  # 多余空白
    assert not is_duplicate("闭包捕获的是什么？", asked)  # 换角度 → 不命中


def test_is_duplicate_empty_ledger_never_hits() -> None:
    # 空台账（首次出题 / 不传台账的调用方）：任何题都不命中——去重是纯附加、不影响首题。
    assert not is_duplicate("什么是闭包？", [])


# --- 缝 3：结构化输出门的归一化去重校验（经公共出题函数驱动）+ 有界重试救回 ------------


def _item() -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id="res",
        index=0,
        concept="闭包",
        summary="函数捕获定义时的作用域",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


class _SeqProvider:
    """按调用序返回不同文本——模拟"首调重复（被门挡）、二调换角度（救回）"。计被调次数。"""

    def __init__(self, texts: Sequence[str]) -> None:
        self._texts = list(texts)
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        self.roles.append(role)
        return Completion(text=text, usage=Usage(prompt_tokens=5, completion_tokens=2))


def _open_json(question: str) -> str:
    return json.dumps({"question": question, "cited_evidence": [_QUOTE]}, ensure_ascii=False)


def _mc_json(question: str) -> str:
    return json.dumps(
        {
            "question": question,
            "options": ["值的快照", "变量本身"],
            "answer_index": 1,
            "cited_evidence": [_QUOTE],
        },
        ensure_ascii=False,
    )


async def test_open_duplicate_question_retries_then_recovers() -> None:
    # 去重门 + 有界重试（开放题）：asked_before 含某题，首调返回归一化相等的题 → ModelRetry → 重试；
    # 二调返回换角度的题 → 通过。证明重复被挡在到达学习者之前，且有界重试把合法换题救回。
    provider = _SeqProvider([_open_json("什么是闭包？"), _open_json("闭包如何捕获变量？")])
    emitter, _events = _emitter()

    question = await generate_question(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        asked_before=["什么是闭包?"],  # 半角问号 → 与首调归一化相等
        max_attempts=3,
    )

    assert question.question == "闭包如何捕获变量？"
    assert provider.calls == 2  # 首调命中去重门 → 重试 → 二调救回


async def test_open_persistent_duplicate_exhausts_retries() -> None:
    # 去重门（开放题）：持续返回归一化相等的重复题 → 重试用尽 → QuestionError（provider 被多调）。
    provider = _SeqProvider([_open_json("什么是闭包？")])
    emitter, _events = _emitter()

    with pytest.raises(QuestionError):
        await generate_question(
            _item(),
            provider=provider,
            emitter=emitter,
            parent_span_id="a",
            asked_before=["什么是闭包？"],
            max_attempts=2,
        )
    assert provider.calls == 2  # > 1 即证明去重门触发了重试


async def test_mc_duplicate_question_retries_then_recovers() -> None:
    # 去重门 + 有界重试（选择题）：首调题干与 asked_before 归一化相等 → 重试；二调换角度 → 通过。
    provider = _SeqProvider(
        [_mc_json("闭包捕获的是什么？"), _mc_json("闭包捕获的到底是变量还是值？")]
    )
    emitter, _events = _emitter()

    mc = await generate_multiple_choice(
        _item(),
        provider=provider,
        emitter=emitter,
        parent_span_id="a",
        asked_before=["闭包捕获的是什么?"],  # 半角问号 → 与首调题干归一化相等
        max_attempts=3,
    )

    assert mc.question == "闭包捕获的到底是变量还是值？"
    assert provider.calls == 2


# --- 缝 1：多轮会话内零逐字重复（同一薄弱 item 复考两轮） ------------------------------


class _DupProvider:
    """出题假 provider：未被"已问过"约束时**默认重复**同一道题；见约束则换角度。判卷恒判对。

    体现修复的价值：不注入台账约束时它会逐字重复（红），注入后换题（绿）；即便它无视约束，
    结构化输出门的归一化去重校验也会 raise ModelRetry 兜底。
    """

    _DEFAULT_Q = "什么是闭包？"
    _ALT_Q = "闭包如何捕获它引用的变量？"

    def __init__(self) -> None:
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        self.roles.append(role)
        text = "\n".join(m.content for m in messages)
        if role == "enrich":  # 出题
            question = self._ALT_Q if "已问过" in text else self._DEFAULT_Q
            payload: dict[str, object] = {"question": question, "cited_evidence": [_QUOTE]}
        else:  # 判卷恒判对（让薄弱 item 转观察中、仍留在薄弱优先集，复考锁定同一 item）
            payload = {"verdict": "对", "cited_evidence": [_QUOTE]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


def _single_item_store() -> tuple[LearningStore, str]:
    store = LearningStore()
    resource = LearningResource.create(url="https://example.com/closure")
    store.add_resource(resource)
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        index=0,
        concept="闭包",
        summary="函数捕获定义时的作用域",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )
    store.add_items([item])
    return store, item.item_id


async def test_session_reassessment_produces_no_verbatim_duplicate() -> None:
    # 会话内同一薄弱 item 复考两轮：run_quiz 持有的"已问过"台账跨轮累积、经 assess_once 下传出题；
    # 断两轮 QUESTION_ASKED 的题目文本归一化后不相等（会话内零逐字重复）。
    store, item_id = _single_item_store()
    memory = LearningMemory()
    memory.record_verdict(item_id, "错")  # 预置薄弱 → 复考锁定同一 item

    recently_asked: dict[str, list[str]] = {}  # 会话内台账，跨轮累积（模拟 run_quiz）

    asked_texts: list[str] = []
    for round_index in range(2):
        emitter, events = _emitter()
        await assess_once(
            store=store,
            provider=_DupProvider(),
            responder=ScriptedResponder(answer="我的作答"),
            memory=memory,
            emitter=emitter,
            rng=new_rng(_SEED + round_index),
            recently_asked=recently_asked,
        )
        asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
        assert asked.payload["item_id"] == item_id  # 两轮都锁定同一薄弱概念（复考是设计意图）
        asked_texts.append(str(asked.payload["question"]))

    # 台账记账内容 == 实际发出的题目文本（顺序一致）——这是"代码记账"的命门：仅断长度会放过"记错
    # 内容"的 mutation（如记常量/截断/记错 item 的题），届时下一轮去重门拿垃圾比对、真重复漏网。
    assert recently_asked[item_id] == asked_texts
    # 台账累积了两轮的题；两轮题归一化后不相等——会话内零逐字重复。
    assert len(recently_asked[item_id]) == 2
    assert dedup_key(asked_texts[0]) != dedup_key(asked_texts[1])


_DUP_DEFAULT_Q = "什么是闭包？"  # 须与 _DupProvider._DEFAULT_Q 逐字一致（跨类边界不取私有属性）


async def test_cross_session_ledger_alone_prevents_repeat_without_session_dict() -> None:
    # skeleton-ledger.md #8 的核心场景：没有 recently_asked（模拟全新会话，进程内台账已随上次
    # 会话退出而清空），只有跨会话持久台账里"上次会话问过"的记录——去重防线必须仍然生效。
    store, item_id = _single_item_store()
    memory = LearningMemory()
    memory.record_verdict(item_id, "错")

    asked_questions = DictAskedQuestionsLedger()
    asked_questions.record_asked(item_id, _DUP_DEFAULT_Q)  # 模拟"上次会话问过默认题"

    emitter, events = _emitter()
    await assess_once(
        store=store,
        provider=_DupProvider(),
        responder=ScriptedResponder(answer="我的作答"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        recently_asked=None,  # 新会话：进程内台账是空的（真机场景下压根没传）
        asked_questions=asked_questions,
    )
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    # 假 provider 只在 prompt 里看到"已问过"才会换题——本轮拿到的已问列表完全来自持久台账。
    assert dedup_key(str(asked.payload["question"])) != dedup_key(_DUP_DEFAULT_Q)
    # 本轮新题同时被记进了持久台账（累积，不是覆盖）。
    assert asked_questions.asked_before(item_id) == [
        _DUP_DEFAULT_Q,
        str(asked.payload["question"]),
    ]


async def test_session_dict_and_cross_session_ledger_are_independent() -> None:
    # 两条防线各自累积、互不干扰：record 只进各自的台账，不会串到对方。
    store, item_id = _single_item_store()
    memory = LearningMemory()
    memory.record_verdict(item_id, "错")

    recently_asked: dict[str, list[str]] = {}
    asked_questions = DictAskedQuestionsLedger()

    emitter, events = _emitter()
    await assess_once(
        store=store,
        provider=_DupProvider(),
        responder=ScriptedResponder(answer="我的作答"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
        recently_asked=recently_asked,
        asked_questions=asked_questions,
    )
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    question_text = str(asked.payload["question"])
    assert recently_asked[item_id] == [question_text]  # 会话内台账记了一条
    assert asked_questions.asked_before(item_id) == [question_text]  # 持久台账也记了同一条

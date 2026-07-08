"""R1-S6：交互考核硬化为**受控子流程**——``start_quiz(count)`` 替软工具。

背景（dogfood f0bf345）：S2b 的 ``next_question`` / ``submit_answer`` 把逐轮编排压给 LLM，
deepseek 守不住——编题 / 串题 / 把 MC 答案加 "B. " 前缀毁掉逐字判卷（#2）/ 题目双重渲染（#1）/
confabulate（#3）。硬化方向：**一问一答受控子流程**，LLM 只**触发** start_quiz、拿结构化小结，
**不进逐题循环**、不复述题目、不自己判卷。

``start_quiz(count)`` 内部跑 ``assess_once × N``（**assess_once 一行不改**），用**注入的 Responder**
逐题作答（MC 走 ``questionary.select`` 逐字选项文本 → ``grade_multiple_choice`` 逐字比对 → #2 从根
消失），共享 emitter（内部 span 嵌 TOOL_CALL 之下），返回结构化小结（考几题 / 暴露哪些薄弱点）。

确定性核心（受控循环 / MC 逐字判卷 / 记账事件序 / 语言偏好透传 / 记放一致）走 TDD；出题 / 判卷两
LLM 槽本身经脚本化 / 回放 provider 验证（不 unit-TDD LLM）。start_quiz **只组合** ``assess_once``，
零逻辑重复。
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
    LearningTask,
)
from grandquiz.domain.learning.preference import (
    QUESTION_LANGUAGE_KEY,
    DictPreferenceMemory,
    PreferenceMemory,
)
from grandquiz.domain.learning.responder import Responder, ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.domain.learning.tools import (
    StartQuizResult,
    register_learning_tools,
)
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.providers.base import Completion, Message, Role, Usage
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayProvider

_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}
_QUOTE = "闭包捕获变量而非值"
_MC_CORRECT = "正确选项内容"
_MC_WRONG = "干扰项内容"


# --------------------------------------------------------------------------- #
# 领域装配脚手架
# --------------------------------------------------------------------------- #


def _stored_item(resource_id: str, index: int, concept: str) -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id=resource_id,
        index=index,
        concept=concept,
        summary=f"{concept} 摘要",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )


def _seed_store(store: LearningStore, task: LearningTask, concepts: list[str]) -> list[str]:
    store.add_task(task)
    resource = LearningResource.create(task_id=task.task_id, url=f"file://local/{task.title}")
    store.add_resource(resource)
    items = [_stored_item(resource.resource_id, i, c) for i, c in enumerate(concepts)]
    store.add_items(items)
    return [it.item_id for it in items]


def _emitter_with_events() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


def _ctx(emitter: EventEmitter) -> ToolContext:
    """造一个带 TOOL_CALL 根 span 的执行上下文（start_quiz 把内部 assess_once span 挂到它之下）。"""
    span = emitter.new_span_id()
    return ToolContext(emitter=emitter, parent_span_id=span)


class _McProvider:
    """enrich 出选择题（正确项恒在下标 0，题干按调用序变化以避免会话内去重门误伤）。

    ``basic`` 判卷本路径用不到（MC 判卷走确定性代码）。计自身调用次数（验证 MC 判卷不打 LLM）。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        if role == "enrich":
            payload: dict[str, Any] = {
                "question": f"闭包的核心是什么？#{self.calls}",
                "options": [_MC_CORRECT, _MC_WRONG],
                "answer_index": 0,
                "cited_evidence": [_QUOTE],
            }
        else:
            payload = {"verdict": "错", "cited_evidence": [_QUOTE]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


class _OpenProvider:
    """enrich 出开放题；basic 判卷（可注入 verdict）。计自身调用次数。"""

    def __init__(self, *, verdict: str) -> None:
        self.calls = 0
        self._verdict = verdict

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        payload: dict[str, Any]
        if role == "enrich":
            payload = {"question": "请解释闭包如何捕获变量？", "cited_evidence": [_QUOTE]}
        else:
            payload = {"verdict": self._verdict, "cited_evidence": [_QUOTE]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


class _LanguageCapturingProvider:
    """出选择题并记录每次 enrich 出题的 system 提示（供断言语言偏好确已透传进出题槽）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.enrich_systems: list[str] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        if role == "enrich":
            system = next((m.content for m in messages if m.role == "system"), "")
            self.enrich_systems.append(system)
            payload: dict[str, Any] = {
                "question": "闭包的核心是什么？",
                "options": [_MC_CORRECT, _MC_WRONG],
                "answer_index": 0,
                "cited_evidence": [_QUOTE],
            }
        else:
            payload = {"verdict": "错", "cited_evidence": [_QUOTE]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


class _SelectByIndexResponder:
    """模拟 ``questionary.select``：按下标返回**逐字**选项文本（MC 选择器契约——修 #2 的根）。

    真机 ``InteractiveResponder`` 走 questionary select、返回所选项原文；本类是它的确定性替身，
    同样**逐字**返回 ``options[index]``（绝不加 "B. " 之类前缀）。
    """

    def __init__(self, index: int) -> None:
        self._index = index

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        assert options is not None, "选择题必给 options"
        return options[self._index]


class _PrefixResponder:
    """故意给所选项加前缀（模拟软工具时代 LLM 把 MC 答案写成 "A. xxx" 的坏行为，复现 #2）。"""

    def __init__(self, index: int, prefix: str) -> None:
        self._index = index
        self._prefix = prefix

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        assert options is not None
        return f"{self._prefix}{options[self._index]}"


def _register(
    registry: ToolRegistry,
    *,
    task: LearningTask,
    store: LearningStore,
    memory: LearningMemory,
    provider: Any,
    responder: Responder,
    preferences: PreferenceMemory | None = None,
    quiz_seed: int = 0,
) -> None:
    register_learning_tools(
        registry,
        task=task,
        source=lambda _u: "",
        provider=provider,
        store=store,
        approval=None,  # type: ignore[arg-type]  # start_quiz 不碰 ingest 依赖
        memory=memory,
        max_bytes=1,
        allowed_domains={"local"},
        responder=responder,
        preferences=preferences,
        quiz_seed=quiz_seed,
    )


def _types(events: list[AgentEvent]) -> list[str]:
    return [e.type for e in events]


def _payload_of(events: list[AgentEvent], event_type: str) -> Mapping[str, Any]:
    return next(e.payload for e in events if e.type == event_type)


# --------------------------------------------------------------------------- #
# MC 受控子流程：出题 → 逐字选项作答 → 确定性判卷 → 记账（LLM 不进逐题循环）
# --------------------------------------------------------------------------- #


async def test_start_quiz_mc_wrong_records_weak_and_gives_followup() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    ids = _seed_store(store, task, ["闭包"])  # fresh memory → 路由到选择题
    memory = LearningMemory()
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        task=task,
        store=store,
        memory=memory,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
    )
    emitter, events = _emitter_with_events()

    raw = await registry.dispatch("start_quiz", {"count": 1}, ctx=_ctx(emitter))
    result = StartQuizResult.model_validate_json(raw)

    assert result.status == "completed"
    assert result.asked == 1
    assert [(r.concept, r.verdict) for r in result.rounds] == [("闭包", "错")]
    assert result.rounds[0].concept_state == "薄弱"
    # 小结含暴露的薄弱点（供 LLM 转述）
    assert [(w.concept, w.state) for w in result.weak] == [("闭包", "薄弱")]
    # 代码记账落进 Learning Memory
    assert memory.state_of(ids[0]) == "薄弱"
    # MC 判卷走确定性代码：整轮**只** 1 次 enrich（出题），无 basic 判卷调用
    assert provider.calls == 1
    # 事件序（内部 assess_once 经 scoped emitter 挂 TOOL_CALL 下）：出题 → 判卷 → 记账 → 追问
    learning = [t for t in _types(events) if t.startswith("learning.")]
    assert learning == [
        "learning.question_asked",
        "learning.answer_judged",
        "learning.concept_state_changed",
        "learning.followup_given",
    ]
    judged = _payload_of(events, "learning.answer_judged")
    assert judged["verdict"] == "错" and judged["weak_item_id"] == ids[0]


async def test_start_quiz_mc_correct_no_weak_no_followup() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    ids = _seed_store(store, task, ["闭包"])
    memory = LearningMemory()
    registry = ToolRegistry()
    _register(
        registry,
        task=task,
        store=store,
        memory=memory,
        provider=_McProvider(),
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await registry.dispatch("start_quiz", {"count": 1}, ctx=_ctx(emitter))
    )

    assert result.rounds[0].verdict == "对"
    assert result.weak == []
    assert memory.state_of(ids[0]) is None  # 答对未追踪概念 → 不追踪
    assert "learning.followup_given" not in _types(events)


# --------------------------------------------------------------------------- #
# MC 选择器逐字选项文本（修 #2）：逐字命中 → 对；带前缀 → 错（复现软工具时代的坏行为）
# --------------------------------------------------------------------------- #


async def test_mc_verbatim_option_grades_correct() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    _seed_store(store, task, ["闭包"])
    registry = ToolRegistry()
    _register(
        registry,
        task=task,
        store=store,
        memory=LearningMemory(),
        provider=_McProvider(),
        responder=_SelectByIndexResponder(index=0),  # 逐字返回正确项文本（无 "B. " 前缀）
    )
    emitter, _ = _emitter_with_events()
    result = StartQuizResult.model_validate_json(
        await registry.dispatch("start_quiz", {"count": 1}, ctx=_ctx(emitter))
    )
    assert result.rounds[0].verdict == "对"  # 逐字选项 → 确定性判卷命中


async def test_mc_prefixed_correct_option_grades_wrong() -> None:
    # 复现 #2：若把正确项加 "A. " 前缀（软工具时代 LLM 的坏行为）→ 逐字比对不命中 → 误判为错。
    # 硬化后 start_quiz 走选择器逐字提交，从根杜绝该前缀污染（见上一测试）。
    task = LearningTask.create("Py")
    store = LearningStore()
    _seed_store(store, task, ["闭包"])
    registry = ToolRegistry()
    _register(
        registry,
        task=task,
        store=store,
        memory=LearningMemory(),
        provider=_McProvider(),
        responder=_PrefixResponder(index=0, prefix="A. "),  # 正确项被加前缀
    )
    emitter, _ = _emitter_with_events()
    result = StartQuizResult.model_validate_json(
        await registry.dispatch("start_quiz", {"count": 1}, ctx=_ctx(emitter))
    )
    assert result.rounds[0].verdict == "错"  # 前缀毁掉逐字命中——正是选择器要杜绝的


# --------------------------------------------------------------------------- #
# 受控循环跑 N 题：一次 start_quiz 考 count 题，LLM 不进逐题循环
# --------------------------------------------------------------------------- #


async def test_start_quiz_runs_count_rounds() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    _seed_store(store, task, ["闭包", "装饰器"])
    registry = ToolRegistry()
    provider = _McProvider()
    _register(
        registry,
        task=task,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        # 两题都逐字选对：概念保持未追踪，两题都路由到选择题（受控循环逐题跑、判卷全走确定性代码）。
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await registry.dispatch("start_quiz", {"count": 2}, ctx=_ctx(emitter))
    )

    assert result.asked == 2
    assert [r.verdict for r in result.rounds] == ["对", "对"]
    # 两题各一次 QUESTION_ASKED / ANSWER_JUDGED（受控循环逐题跑）
    assert _types(events).count("learning.question_asked") == 2
    assert _types(events).count("learning.answer_judged") == 2
    # MC 判卷走确定性代码：两题共 2 次 enrich（出题）、零 basic 判卷调用
    assert provider.calls == 2


# --------------------------------------------------------------------------- #
# 选题聚焦（R1-S7）：focus 透传 assess_once → select_target（mixed 覆盖优先 / weak 复习薄弱）
# --------------------------------------------------------------------------- #


async def test_start_quiz_focus_weak_targets_weak_concept() -> None:
    # focus="weak"（"复习薄弱"）逐次透传每题 assess_once → select_target：锁定唯一薄弱概念，
    # 即使有未考过的新概念。构造保证 mutation 可杀——薄弱项刻意放在 index 0，而 mixed 默认（quiz_seed
    # 0）会选 index 1；若 focus 未透传（退回 mixed）则选中 装饰器 而非 闭包 → 断言变红。
    task = LearningTask.create("Py")
    store = LearningStore()
    ids = _seed_store(store, task, ["闭包", "装饰器", "生成器"])
    memory = LearningMemory()
    memory.record_verdict(ids[0], "错")  # 仅 闭包（index 0）薄弱；装饰器 / 生成器 未追踪且未考过
    assert memory.state_of(ids[0]) == "薄弱"
    registry = ToolRegistry()
    _register(
        registry,
        task=task,
        store=store,
        memory=memory,
        provider=_OpenProvider(verdict="对"),  # 薄弱 → 追问（LLM 判卷路径）
        responder=ScriptedResponder(answer="我的作答"),
    )
    emitter, _ = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await registry.dispatch("start_quiz", {"count": 1, "focus": "weak"}, ctx=_ctx(emitter))
    )

    assert result.rounds[0].item_id == ids[0]  # focus=weak 锁定薄弱 闭包（!= mixed 会选的 装饰器）
    assert result.rounds[0].concept == "闭包"


# --------------------------------------------------------------------------- #
# 空库优雅拒答：无题可考 → refused、零 LLM 调用
# --------------------------------------------------------------------------- #


async def test_start_quiz_empty_kb_refused() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()  # 空库
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        task=task,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, _ = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await registry.dispatch("start_quiz", {"count": 3}, ctx=_ctx(emitter))
    )

    assert result.status == "refused"
    assert result.asked == 0
    assert result.rounds == []
    assert result.weak == []
    assert provider.calls == 0  # 空库不调任何 LLM


# --------------------------------------------------------------------------- #
# 语言偏好透传（补 S2b/S4 欠账）：偏好 > task 默认 > 中文
# --------------------------------------------------------------------------- #


async def test_start_quiz_passes_language_preference() -> None:
    task = LearningTask.create("Py")  # task.language 默认中文
    store = LearningStore()
    _seed_store(store, task, ["闭包"])
    preferences = DictPreferenceMemory()
    preferences.set_preference(QUESTION_LANGUAGE_KEY, "英文")  # 偏好压过 task 默认
    provider = _LanguageCapturingProvider()
    registry = ToolRegistry()
    _register(
        registry,
        task=task,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
        preferences=preferences,
    )
    emitter, _ = _emitter_with_events()

    await registry.dispatch("start_quiz", {"count": 1}, ctx=_ctx(emitter))

    # 出题槽的 system 提示确以偏好语言（英文）替换了 {{LANGUAGE}} 哨兵（透传到 assess_once）。
    # 精确锚定替换点 "请用 <语言> 提问"——模板正文另含字面"英文原词"，故只查裸"英文"会假通过。
    assert provider.enrich_systems
    assert "请用 英文 提问" in provider.enrich_systems[0]
    assert "请用 中文 提问" not in provider.enrich_systems[0]  # 未被 task 默认语言（中文）占位


# --------------------------------------------------------------------------- #
# 记放一致：一次 start_quiz（开放路径，出题 + 判卷两 LLM 槽）整轨迹零 token 回放
# --------------------------------------------------------------------------- #


async def test_start_quiz_record_then_replay_is_identical(tmp_path: Path) -> None:
    task = LearningTask.create("Py")
    cassette_path = tmp_path / "quiz.json"

    async def drive(provider: Any) -> tuple[StartQuizResult, list[AgentEvent]]:
        store = LearningStore()
        ids = _seed_store(store, task, ["闭包"])
        memory = LearningMemory()
        memory.record_verdict(ids[0], "错")  # → 薄弱
        memory.record_verdict(ids[0], "对")  # → 观察中 → 开放题（出题 + 判卷两 LLM 槽都被录）
        registry = ToolRegistry()
        _register(
            registry,
            task=task,
            store=store,
            memory=memory,
            provider=provider,
            responder=ScriptedResponder(answer="闭包捕获引用"),
            quiz_seed=42,
        )
        emitter, events = _emitter_with_events()
        raw = await registry.dispatch("start_quiz", {"count": 1}, ctx=_ctx(emitter))
        return StartQuizResult.model_validate_json(raw), events

    # Pass 1：录制——inner 真跑，出题 + 判卷两 LLM 槽进 cassette。
    inner = _OpenProvider(verdict="勉强")
    cassette = Cassette()
    r1, events1 = await drive(RecordingProvider(inner, cassette, _MODELS))
    cassette.save(cassette_path)
    calls_after_record = inner.calls

    # Pass 2：回放——全新 store/memory/registry + 相同输入。
    replay = ReplayProvider(Cassette.load(cassette_path), _MODELS)
    r2, events2 = await drive(replay)

    assert r1 == r2  # 判决 / 记账终态逐字段一致
    assert inner.calls == calls_after_record  # 回放没有多触 inner（烧 0 token）
    assert _types(events1) == _types(events2)  # 事件序列跨记放一致
    assert _payload_of(events1, "learning.question_asked") == _payload_of(
        events2, "learning.question_asked"
    )

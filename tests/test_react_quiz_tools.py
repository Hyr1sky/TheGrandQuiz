"""R1-S2b：交互考核工具——``next_question`` / ``submit_answer``（对话回合驱动）。

把 ``assess_once`` 的"出题→答→判卷"拆成两个 context-aware 同步工具，以对话回合边界当暂停点
（不需 suspend/resume #6）：

- ``next_question()``：选题 + 题型路由 + 分型出题（LLM enrich 槽）→ 发 ``QUESTION_ASKED`` →
  返回题 + options；**持久化待答态**到会话（按 task 键）。
- ``submit_answer(answer)``：读待答态 → 判卷（MC 走确定性代码、开放走 LLM basic 槽）→ 代码算
  ``weak_item_id`` → ``record_verdict`` → 发 ``ANSWER_JUDGED`` / ``CONCEPT_STATE_CHANGED`` →
  （勉强 / 错）``FOLLOWUP_GIVEN`` → 清待答态 → 返回判决 + 追问。

确定性核心（待答态持久 / MC 判卷不打 LLM / 记账事件序 / 跨两工具步可回放）走 TDD；出题 / 判卷两
LLM 槽本身经脚本化 / 回放 provider 验证（不 unit-TDD LLM）。两工具**只组合** assessment 的现有
子函数（selection/routing/question/grading/memory/_compose_solution），零逻辑重复。
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
from grandquiz.domain.learning.store import LearningStore
from grandquiz.domain.learning.tools import (
    NextQuestionResult,
    SubmitAnswerResult,
    register_learning_tools,
)
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.kernel.tools import ModelRetry, ToolContext, ToolRegistry
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
    """造一个带 TOOL_CALL 根 span 的执行上下文（工具把内部事件挂到它之下）。"""
    span = emitter.new_span_id()
    return ToolContext(emitter=emitter, parent_span_id=span)


class _McProvider:
    """enrich 出选择题（正确项恒在下标 0）；basic 判卷（本路径用不到）。计自身调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        payload: dict[str, Any]
        if role == "enrich":
            payload = {
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


class _OpenProvider:
    """enrich 出开放题；basic 判卷（可注入 verdict）。计自身调用次数。"""

    def __init__(self, *, verdict: str) -> None:
        self.calls = 0
        self._verdict = verdict

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
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


def _register(
    registry: ToolRegistry,
    *,
    task: LearningTask,
    store: LearningStore,
    memory: LearningMemory,
    provider: Any,
    quiz_seed: int = 0,
) -> None:
    register_learning_tools(
        registry,
        task=task,
        source=lambda _u: "",
        provider=provider,
        store=store,
        approval=None,  # type: ignore[arg-type]  # 交互考核工具不碰 ingest 依赖
        memory=memory,
        max_bytes=1,
        allowed_domains={"local"},
        quiz_seed=quiz_seed,
    )


def _types(events: list[AgentEvent]) -> list[str]:
    return [e.type for e in events]


def _payload_of(events: list[AgentEvent], event_type: str) -> Mapping[str, Any]:
    return next(e.payload for e in events if e.type == event_type)


# --------------------------------------------------------------------------- #
# MC 路径：首次接触 → 选择题 → 提交作答（MC 判卷走确定性代码、不打 LLM）
# --------------------------------------------------------------------------- #


async def test_next_question_asks_mc_and_persists_pending() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    _seed_store(store, task, ["闭包"])  # fresh memory → 路由到选择题
    provider = _McProvider()
    registry = ToolRegistry()
    _register(registry, task=task, store=store, memory=LearningMemory(), provider=provider)
    emitter, events = _emitter_with_events()

    raw = await registry.dispatch("next_question", {}, ctx=_ctx(emitter))
    result = NextQuestionResult.model_validate_json(raw)

    assert result.status == "asked"
    assert result.question_type == "选择题"
    assert result.options == [_MC_CORRECT, _MC_WRONG]  # options 透给用户视图
    # 出题上脊柱：QUESTION_ASKED 携真实 item + 非空证据 + 题型 + options（answer_index 不泄漏）
    asked = _payload_of(events, "learning.question_asked")
    assert asked["question_type"] == "选择题"
    assert asked["cited_evidence"] == [_QUOTE]
    assert asked["options"] == [_MC_CORRECT, _MC_WRONG]
    assert "answer_index" not in asked
    assert provider.calls == 1  # 只出题这一次 enrich


async def test_submit_wrong_mc_grades_records_weak_and_gives_followup() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    ids = _seed_store(store, task, ["闭包"])
    memory = LearningMemory()
    provider = _McProvider()
    registry = ToolRegistry()
    _register(registry, task=task, store=store, memory=memory, provider=provider)
    emitter, events = _emitter_with_events()

    await registry.dispatch("next_question", {}, ctx=_ctx(emitter))
    calls_after_ask = provider.calls
    raw = await registry.dispatch("submit_answer", {"answer": _MC_WRONG}, ctx=_ctx(emitter))
    result = SubmitAnswerResult.model_validate_json(raw)

    assert result.verdict == "错"
    assert result.weak_item_id == ids[0]
    assert result.concept_state == "薄弱"
    assert result.followup is not None and "闭包" in result.followup
    # MC 判卷走确定性代码：submit 阶段**零** LLM 调用（不占 basic 判卷槽）
    assert provider.calls == calls_after_ask
    # 记账落进 Learning Memory（代码记账）
    assert memory.state_of(ids[0]) == "薄弱"
    # 事件序：ANSWER_JUDGED → CONCEPT_STATE_CHANGED → FOLLOWUP_GIVEN
    types = [t for t in _types(events) if t.startswith("learning.")]
    assert types == [
        "learning.question_asked",
        "learning.answer_judged",
        "learning.concept_state_changed",
        "learning.followup_given",
    ]
    judged = _payload_of(events, "learning.answer_judged")
    assert judged["verdict"] == "错" and judged["weak_item_id"] == ids[0]


async def test_submit_correct_mc_no_weak_no_followup() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    ids = _seed_store(store, task, ["闭包"])
    memory = LearningMemory()
    provider = _McProvider()
    registry = ToolRegistry()
    _register(registry, task=task, store=store, memory=memory, provider=provider)
    emitter, events = _emitter_with_events()

    await registry.dispatch("next_question", {}, ctx=_ctx(emitter))
    raw = await registry.dispatch("submit_answer", {"answer": _MC_CORRECT}, ctx=_ctx(emitter))
    result = SubmitAnswerResult.model_validate_json(raw)

    assert result.verdict == "对"
    assert result.weak_item_id is None
    assert result.followup is None
    assert memory.state_of(ids[0]) is None  # 答对未追踪概念 → 不追踪
    assert "learning.followup_given" not in _types(events)


# --------------------------------------------------------------------------- #
# 待答态生命周期：无待答态拒答 / 提交后清态 / 重复提交拒答
# --------------------------------------------------------------------------- #


async def test_submit_without_pending_raises_model_retry() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    _seed_store(store, task, ["闭包"])
    registry = ToolRegistry()
    _register(registry, task=task, store=store, memory=LearningMemory(), provider=_McProvider())
    emitter, _ = _emitter_with_events()

    try:
        await registry.dispatch("submit_answer", {"answer": _MC_CORRECT}, ctx=_ctx(emitter))
    except ModelRetry:
        pass
    else:
        raise AssertionError("无待答态提交应抛 ModelRetry（提示先 next_question）")


async def test_pending_cleared_after_submit_blocks_double_submit() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    _seed_store(store, task, ["闭包"])
    registry = ToolRegistry()
    _register(registry, task=task, store=store, memory=LearningMemory(), provider=_McProvider())
    emitter, _ = _emitter_with_events()

    await registry.dispatch("next_question", {}, ctx=_ctx(emitter))
    await registry.dispatch("submit_answer", {"answer": _MC_CORRECT}, ctx=_ctx(emitter))
    try:
        await registry.dispatch("submit_answer", {"answer": _MC_CORRECT}, ctx=_ctx(emitter))
    except ModelRetry:
        pass
    else:
        raise AssertionError("提交后待答态应被清空——二次提交无待答态、应抛 ModelRetry")


# --------------------------------------------------------------------------- #
# 开放路径：观察中 → 开放题 → LLM 判卷（basic 槽真被调用）
# --------------------------------------------------------------------------- #


async def test_open_path_uses_llm_grading() -> None:
    task = LearningTask.create("Py")
    store = LearningStore()
    ids = _seed_store(store, task, ["闭包"])
    memory = LearningMemory()
    memory.record_verdict(ids[0], "错")  # → 薄弱
    memory.record_verdict(ids[0], "对")  # → 观察中（路由到开放）
    provider = _OpenProvider(verdict="对")
    registry = ToolRegistry()
    _register(registry, task=task, store=store, memory=memory, provider=provider)
    emitter, _events = _emitter_with_events()

    ask = NextQuestionResult.model_validate_json(
        await registry.dispatch("next_question", {}, ctx=_ctx(emitter))
    )
    assert ask.question_type == "开放"
    assert ask.options is None  # 开放题无 options
    calls_after_ask = provider.calls
    raw = await registry.dispatch(
        "submit_answer", {"answer": "闭包捕获的是引用"}, ctx=_ctx(emitter)
    )
    result = SubmitAnswerResult.model_validate_json(raw)

    # 开放判卷占 basic 槽：submit 阶段确有一次 LLM 调用
    assert provider.calls == calls_after_ask + 1
    assert result.verdict == "对"
    # 观察中（连对 1）+ 再对 → 连对 2 达销账阈值 → 掌握销账 → 终态 None（记账由代码做）
    assert result.concept_state is None
    assert result.followup is None
    assert memory.state_of(ids[0]) is None


# --------------------------------------------------------------------------- #
# 记放一致：next_question + submit_answer 跨两工具步，整轨迹零 token 回放
# --------------------------------------------------------------------------- #


async def test_two_tool_steps_record_then_replay_is_identical(tmp_path: Path) -> None:
    task = LearningTask.create("Py")
    cassette_path = tmp_path / "quiz.json"

    async def drive(provider: Any) -> tuple[SubmitAnswerResult, list[AgentEvent]]:
        store = LearningStore()
        ids = _seed_store(store, task, ["闭包"])
        memory = LearningMemory()
        memory.record_verdict(ids[0], "错")  # → 薄弱
        memory.record_verdict(ids[0], "对")  # → 观察中 → 开放题（出题 + 判卷两 LLM 槽都被录）
        registry = ToolRegistry()
        _register(registry, task=task, store=store, memory=memory, provider=provider, quiz_seed=42)
        emitter, events = _emitter_with_events()
        await registry.dispatch("next_question", {}, ctx=_ctx(emitter))
        raw = await registry.dispatch(
            "submit_answer", {"answer": "闭包捕获引用"}, ctx=_ctx(emitter)
        )
        return SubmitAnswerResult.model_validate_json(raw), events

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

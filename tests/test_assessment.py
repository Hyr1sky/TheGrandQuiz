"""单题考核竖切测试（缝 1，主缝）——跑在事件 / trace 流上，端到端经事件观察。

覆盖 eval 用例：case 2（空库拒答、不调 LLM）、case 3（出题锚定真实 item + 非空证据）、
case 4（答错 → 薄弱按 item_id 入记忆 + 发 CONCEPT_STATE_CHANGED）、case 5（复考出题 ∈ 薄弱优先
候选集、新概念被排除）、case 6（答对一次转观察中、连对两次销账）；外加"整条单题竖切在 replay
下确定"（用 M2 的 Record/Replay Provider）。断言外部行为——事件流、span 树、判决与三态记账、
角色分槽——不耦合实现细节。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource, LearningTask
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.selection import select_target
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.trace import Span, TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, Usage
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayProvider

_ASSESSMENT_STARTED = "assessment.started"
_ASSESSMENT_ENDED = "assessment.ended"
_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}
_SEED = 42
_URL = "https://example.com/react"

# 每个 item 一条独一无二的证据引文（互不为子串）——保证"只有被考 item 的引文出现在其 prompt 里"，
# 假 provider 据此从 messages 里回抽一条**属于该 item 的真实证据**来引用（防幽灵题在真链路上成立）。
_ITEM_DATA = [
    ("闭包", "闭包捕获变量而非值"),
    ("变量提升", "var 声明提升到作用域顶部"),
    ("事件循环", "事件循环调度宏任务与微任务"),
]
_QUOTES = {quote for _concept, quote in _ITEM_DATA}


class _AssessProvider:
    """确定性假 provider：按 role 分槽——enrich 出题、basic 判卷；从 messages 回抽真实证据引用。

    记录每次 role，供断言"出题=enrich、判卷=basic"（两 model span 的角色）。
    """

    def __init__(self, verdict: str) -> None:
        self._verdict = verdict
        self.calls = 0
        self.roles: list[Role] = []

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        self.roles.append(role)
        text = "\n".join(m.content for m in messages)
        quote = next(q for q in _QUOTES if q in text)  # 只有被考 item 的引文会出现在其 prompt 里
        if role == "enrich":
            payload: dict[str, Any] = {
                "question": "该知识点的核心是什么？",
                "cited_evidence": [quote],
            }
        else:
            payload = {"verdict": self._verdict, "cited_evidence": [quote]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


def _harness() -> tuple[EventEmitter, list[AgentEvent], TraceStore]:
    events: list[AgentEvent] = []
    store = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(events.append)
    sink.subscribe(store.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id="run")
    return emitter, events, store


def _summ(spans: list[Span]) -> list[dict[str, Any]]:
    return [
        {"type": s.type, "start_ts": s.start_ts, "end_ts": s.end_ts, "children": _summ(s.children)}
        for s in spans
    ]


def _stocked_store() -> tuple[LearningStore, LearningTask, list[str]]:
    """建一个塞了若干 KnowledgeItem 的 store，返回 (store, task, item_ids)。"""
    store = LearningStore()
    task = LearningTask.create("React")
    resource = LearningResource.create(task_id=task.task_id, url=_URL)
    store.add_task(task)
    store.add_resource(resource)
    items = [
        KnowledgeItem.create(
            resource_id=resource.resource_id,
            index=index,
            concept=concept,
            summary=f"{concept} 的一句话摘要",
            evidence=[Evidence(quote=quote)],
            confidence=0.9,
        )
        for index, (concept, quote) in enumerate(_ITEM_DATA)
    ]
    store.add_items(items)
    return store, task, [item.item_id for item in items]


async def test_empty_kb_refuses_without_calling_any_llm() -> None:
    # eval case 2：空库"考我"→ 拒答、引导喂资源，绝不凭空编题（不调任何 LLM）。
    emitter, events, trace = _harness()
    provider = _AssessProvider(verdict="对")
    memory = LearningMemory()

    result = await assess_once(
        LearningTask.create("React"),
        store=LearningStore(),
        provider=provider,
        responder=ScriptedResponder(answer="任意"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    assert result.status == "refused"
    assert result.item_id is None and result.verdict is None and result.weak_item_id is None
    assert result.concept_state is None
    assert [e.type for e in events] == [
        _ASSESSMENT_STARTED,
        LearningEvent.ASSESSMENT_REFUSED,
        _ASSESSMENT_ENDED,
    ]
    # 不调任何 LLM、不碰 memory：无 model span、provider 0 调用、无状态转移事件、记忆仍空。
    assert EventType.MODEL_STARTED not in {e.type for e in events}
    assert LearningEvent.CONCEPT_STATE_CHANGED not in {e.type for e in events}
    assert provider.calls == 0
    assert memory.weak_item_ids() == set()
    refused = next(e for e in events if e.type == LearningEvent.ASSESSMENT_REFUSED)
    assert refused.payload["reason"] == "empty_kb"
    # span 树只有一个 assessment 根、无子 span。
    roots = trace.span_tree("run")
    assert len(roots) == 1
    assert roots[0].type == "assessment"
    assert roots[0].children == []
    assert roots[0].end_ts is not None
    trace.close()


@pytest.mark.parametrize(
    ("verdict", "weak_expected"), [("错", True), ("勉强", True), ("对", False)]
)
async def test_happy_path_event_stream_trace_and_bookkeeping(
    verdict: str, weak_expected: bool
) -> None:
    emitter, events, trace = _harness()
    store, task, item_ids = _stocked_store()
    provider = _AssessProvider(verdict=verdict)
    memory = LearningMemory()

    result = await assess_once(
        task,
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="我的作答"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    # 事件流：出题 model[enrich] → 提问 → 判卷 model[basic] → 判决 → 三态记账，全在 assessment 内。
    # CONCEPT_STATE_CHANGED 每轮必发（记账结果上脊柱），故三种 verdict 序列一致。
    assert [e.type for e in events] == [
        _ASSESSMENT_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.QUESTION_ASKED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.ANSWER_JUDGED,
        LearningEvent.CONCEPT_STATE_CHANGED,
        _ASSESSMENT_ENDED,
    ]

    # eval case 3：出题锚定真实 item 且 cited_evidence 逐字属于被考 item 自己的证据（防幽灵题）。
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    assert asked.payload["item_id"] in item_ids
    assert asked.payload["cited_evidence"]  # 非空
    quotes_by_item = {
        item.item_id: {ev.quote for ev in item.evidence}
        for item in store.items_for_task(task.task_id)
    }
    assert asked.payload["cited_evidence"][0] in quotes_by_item[asked.payload["item_id"]]

    # LLM 判卷、代码记账：判"错/勉强"→ weak_item_id = 被考 item；判"对"→ None。
    assert result.status == "judged"
    assert result.item_id in item_ids
    assert result.verdict == verdict
    judged = next(e for e in events if e.type == LearningEvent.ANSWER_JUDGED)
    changed = next(e for e in events if e.type == LearningEvent.CONCEPT_STATE_CHANGED)
    # 三态记账（fresh memory）：错 / 勉强 → 薄弱入库；对 → 非薄弱概念不追踪。
    assert changed.payload["item_id"] == result.item_id
    assert changed.payload["from_state"] is None  # 本轮前未追踪
    if weak_expected:
        assert result.weak_item_id == result.item_id
        assert judged.payload["weak_item_id"] == result.item_id
        assert changed.payload["to_state"] == "薄弱"
        assert changed.payload["consecutive_correct"] == 0
        assert result.concept_state == "薄弱"
        assert memory.state_of(result.item_id) == "薄弱"
    else:
        assert result.weak_item_id is None
        assert judged.payload["weak_item_id"] is None
        assert changed.payload["to_state"] is None  # 答对非薄弱概念 → 不追踪
        assert result.concept_state is None
        assert memory.state_of(result.item_id) is None
    # 出题、判卷、记账都锚定同一个被考 item。
    assert asked.payload["item_id"] == result.item_id == judged.payload["item_id"]

    # 两个 LLM 槽分角色：出题=enrich、判卷=basic——从 model span 事件的 role 字段读（角色上脊柱）。
    model_starts = [e for e in events if e.type == EventType.MODEL_STARTED]
    assert [e.payload["role"] for e in model_starts] == ["enrich", "basic"]

    # span 树：assessment 根 → [出题 model, 判卷 model] 两个子 span，皆闭合。
    roots = trace.span_tree("run")
    assert len(roots) == 1
    assert roots[0].type == "assessment"
    assert [c.type for c in roots[0].children] == ["model", "model"]
    assert all(child.end_ts is not None for child in roots[0].children)
    # 领域点事件（span_id=None）不进树，但 parent 链挂在 assessment 根上。
    assessment_root = roots[0].span_id
    for etype in (
        LearningEvent.QUESTION_ASKED,
        LearningEvent.ANSWER_JUDGED,
        LearningEvent.CONCEPT_STATE_CHANGED,
    ):
        point = next(e for e in events if e.type == etype)
        assert point.span_id is None
        assert point.parent_span_id == assessment_root
    trace.close()


class _RecordingInner:
    """录制阶段的真实 inner——与 _AssessProvider 同逻辑，但计被调次数（证明回放不再触碰它）。"""

    def __init__(self, verdict: str) -> None:
        self._verdict = verdict
        self.calls = 0

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        text = "\n".join(m.content for m in messages)
        quote = next(q for q in _QUOTES if q in text)
        if role == "enrich":
            payload: dict[str, Any] = {
                "question": "该知识点的核心是什么？",
                "cited_evidence": [quote],
            }
        else:
            payload = {"verdict": self._verdict, "cited_evidence": [quote]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


async def _run_once(provider: Provider, emitter: EventEmitter) -> None:
    store, task, _ids = _stocked_store()
    await assess_once(
        task,
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="我的作答"),
        memory=LearningMemory(),
        emitter=emitter,
        rng=new_rng(_SEED),
    )


async def test_whole_assessment_slice_is_deterministic_under_replay(tmp_path: Path) -> None:
    cassette_path = tmp_path / "cassette.json"

    # Pass 1：录制——RecordingProvider 包一个计数 inner。两槽（enrich / basic）落两条 cassette 键。
    inner = _RecordingInner(verdict="错")
    cassette = Cassette()
    emitter1, events1, trace1 = _harness()
    await _run_once(RecordingProvider(inner, cassette, _MODELS), emitter1)
    cassette.save(cassette_path)
    tree1 = trace1.span_tree("run")
    assert inner.calls == 2  # 出题 + 判卷各一次

    # Pass 2：回放——ReplayProvider + 重置 ManualClock + 相同 seed rng + 相同 store。
    replay = ReplayProvider(Cassette.load(cassette_path), _MODELS)
    emitter2, events2, trace2 = _harness()
    await _run_once(replay, emitter2)
    tree2 = trace2.span_tree("run")

    # 两遍事件流逐字段一致（type / seq / ts / span / payload）。
    def _rows(evs: list[AgentEvent]) -> list[tuple[str, int, float, str | None, str | None, Any]]:
        return [(e.type, e.seq, e.ts, e.span_id, e.parent_span_id, dict(e.payload)) for e in evs]

    assert _rows(events1) == _rows(events2)
    # span 树结构 / 时序一致。
    assert _summ(tree1) == _summ(tree2)
    # 回放没有再触碰 inner（第二遍 0 调用，证明整条单题竖切在 replay 下确定、不烧 token）。
    assert inner.calls == 2
    trace1.close()
    trace2.close()


# --- M3.3 薄弱记忆 + 三态状态机 + 薄弱优先复考（eval case 4 / 5 / 6，缝 1）---------------


async def _assess_with_verdict(
    store: LearningStore, task: LearningTask, memory: LearningMemory, verdict: str
) -> tuple[Any, list[AgentEvent]]:
    """跑一轮考核，判卷固定给 ``verdict``，复用同一 ``memory`` 累积记账；返回 (result, events)。"""
    emitter, events, trace = _harness()
    result = await assess_once(
        task,
        store=store,
        provider=_AssessProvider(verdict=verdict),
        responder=ScriptedResponder(answer="我的作答"),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )
    trace.close()
    return result, events


async def test_case4_wrong_answer_records_weak_by_item_id() -> None:
    # eval case 4：答错 → 概念按 item_id 写入 Learning Memory（薄弱）+ 发 CONCEPT_STATE_CHANGED。
    store, task, item_ids = _stocked_store()
    memory = LearningMemory()

    result, events = await _assess_with_verdict(store, task, memory, "错")

    target = result.item_id
    assert target in item_ids
    # 按 item_id 锚定入记忆：薄弱，且记忆里只此一个（未污染其它 item）。
    assert memory.state_of(target) == "薄弱"
    assert memory.weak_item_ids() == {target}
    assert result.concept_state == "薄弱"
    # 发 CONCEPT_STATE_CHANGED 到薄弱，payload 锚定同一 item_id。
    changed = next(e for e in events if e.type == LearningEvent.CONCEPT_STATE_CHANGED)
    assert changed.payload["item_id"] == target
    assert changed.payload["from_state"] is None
    assert changed.payload["to_state"] == "薄弱"
    assert changed.payload["consecutive_correct"] == 0
    # 时序：在 ANSWER_JUDGED 之后、assessment.ended 之前。
    types = [e.type for e in events]
    assert (
        types.index(LearningEvent.ANSWER_JUDGED)
        < types.index(LearningEvent.CONCEPT_STATE_CHANGED)
        < types.index(_ASSESSMENT_ENDED)
    )


async def test_case5_reassessment_targets_weak_priority_candidate() -> None:
    # eval case 5：有薄弱概念时，复考出题锚定薄弱优先候选集里的 item，新概念被排除。
    store, task, item_ids = _stocked_store()
    items = store.items_for_task(task.task_id)
    # 全集随机（无记忆）本会选中的 item——用它作对照，证明薄弱优先确实压过了全集随机。
    natural = select_target(items, rng=new_rng(_SEED)).item_id
    # 制造一个"不同于自然选择"的薄弱概念（喂一次错）。
    weak_item = next(i for i in item_ids if i != natural)
    memory = LearningMemory()
    memory.record_verdict(weak_item, "错")
    assert memory.weak_item_ids() == {weak_item}

    _result, events = await _assess_with_verdict(store, task, memory, "对")

    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    # 出题锚定薄弱概念，而非全集随机会选的新概念（薄弱优先候选集把新概念排除）。
    assert asked.payload["item_id"] == weak_item
    assert asked.payload["item_id"] != natural


async def test_case6_one_correct_observes_two_correct_discharges() -> None:
    # eval case 6：连对两次才销账（观察中→销账），且薄弱优先把复考锁定到薄弱 item。
    # 薄弱 item 刻意 != 全集随机自然选择，才能真正区分薄弱优先 vs seed 巧合（照 case 5 同款对照）。
    store, task, item_ids = _stocked_store()
    natural = select_target(store.items_for_task(task.task_id), rng=new_rng(_SEED)).item_id
    target = next(i for i in item_ids if i != natural)
    memory = LearningMemory()
    memory.record_verdict(target, "错")  # 预置薄弱（!= natural）；答错→薄弱经 assess 由 case 4 覆盖
    assert memory.state_of(target) == "薄弱"

    # 答对一次 → 观察中（仍在记忆）；薄弱优先把复考锁定到 target（压过全集随机的 natural）。
    r2, e2 = await _assess_with_verdict(store, task, memory, "对")
    assert r2.item_id == target != natural  # 真正区分薄弱优先 vs seed 巧合
    assert memory.state_of(target) == "观察中"
    assert target in memory.weak_item_ids()  # 观察中仍在表内，答对一次不销账
    assert r2.concept_state == "观察中"
    c2 = next(e for e in e2 if e.type == LearningEvent.CONCEPT_STATE_CHANGED)
    assert (
        c2.payload["from_state"],
        c2.payload["to_state"],
        c2.payload["consecutive_correct"],
    ) == ("薄弱", "观察中", 1)

    # 连续第二次答对 → 销账（从记忆移除）。
    r3, e3 = await _assess_with_verdict(store, task, memory, "对")
    assert r3.item_id == target
    assert memory.state_of(target) is None
    assert target not in memory.weak_item_ids()
    assert r3.concept_state is None
    c3 = next(e for e in e3 if e.type == LearningEvent.CONCEPT_STATE_CHANGED)
    assert (
        c3.payload["from_state"],
        c3.payload["to_state"],
        c3.payload["consecutive_correct"],
    ) == ("观察中", "销账", 2)

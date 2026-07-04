"""单题考核竖切测试（缝 1，主缝）——跑在事件 / trace 流上，端到端经事件观察。

覆盖两个 eval 用例：case 2（空库拒答、不调 LLM）、case 3（出题锚定真实 item + 非空证据）；
外加"整条单题竖切在 replay 下确定"（用 M2 的 Record/Replay Provider）。断言外部行为——
事件流、span 树、判决记账、角色分槽——不耦合实现细节。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource, LearningTask
from grandquiz.domain.learning.responder import ScriptedResponder
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

    result = await assess_once(
        LearningTask.create("React"),
        store=LearningStore(),
        provider=provider,
        responder=ScriptedResponder(answer="任意"),
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    assert result.status == "refused"
    assert result.item_id is None and result.verdict is None and result.weak_item_id is None
    assert [e.type for e in events] == [
        _ASSESSMENT_STARTED,
        LearningEvent.ASSESSMENT_REFUSED,
        _ASSESSMENT_ENDED,
    ]
    # 不调任何 LLM：无 model span、provider 0 调用。
    assert EventType.MODEL_STARTED not in {e.type for e in events}
    assert provider.calls == 0
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

    result = await assess_once(
        task,
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer="我的作答"),
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    # 事件流：出题 model[enrich] → 提问 → 判卷 model[basic] → 判决，全包在 assessment span 内。
    assert [e.type for e in events] == [
        _ASSESSMENT_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.QUESTION_ASKED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.ANSWER_JUDGED,
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
    if weak_expected:
        assert result.weak_item_id == result.item_id
        assert judged.payload["weak_item_id"] == result.item_id
    else:
        assert result.weak_item_id is None
        assert judged.payload["weak_item_id"] is None
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
    for etype in (LearningEvent.QUESTION_ASKED, LearningEvent.ANSWER_JUDGED):
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

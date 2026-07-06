"""单题考核竖切测试（缝 1，主缝）——跑在事件 / trace 流上，端到端经事件观察。

覆盖 eval 用例：case 2（空库拒答、不调 LLM）、case 3（出题锚定真实 item + 非空证据）、
case 4（答错 → 薄弱按 item_id 入记忆 + 发 CONCEPT_STATE_CHANGED）、case 5（复考出题 ∈ 薄弱优先
候选集、新概念被排除）、case 6（答对一次转观察中、连对两次销账）、case 8（题型路由：首次接触 →
选择题、薄弱复考 → 追问）；外加"整条单题竖切在 replay 下确定"（用 M2 的 Record/Replay Provider）。

M3.4（题型路由 + 追问）后，assess_once 按被考概念在 Learning Memory 的状态路由题型：
- fresh memory（首次接触）→ **选择题（MC）**：确定性判卷、**无判卷 model span**、判卷 0 调用。
- 薄弱复考 → **追问**（深挖 prompt 变体 + LLM 判卷）；观察中 → **开放**（标准 LLM 判卷）。
- 判决为"勉强 / 错"→ 后置追问"给正解"，发 FOLLOWUP_GIVEN（判"对"不发；MC 判错也触发）。
故 MC 与 开放 / 追问 的事件序列不同（MC 少一对判卷 model span），测试分别断言。断言外部行为
（事件流、span 树、判决与三态记账、角色分槽），不耦合实现细节。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from grandquiz.domain.learning.assessment import AssessmentResult, assess_once
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource, LearningTask
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.selection import select_target
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.harness import build_event_harness as _harness
from grandquiz.evals.harness import summarize_spans as _summ
from grandquiz.kernel.clock import new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Provider, Role, Usage
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayProvider

_ASSESSMENT_STARTED = "assessment.started"
_ASSESSMENT_ENDED = "assessment.ended"
_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}
_SEED = 42
_URL = "https://example.com/react"

# 选择题的固定选项文本（正确项恒在下标 0）——responder 注入其一即可确定性判对 / 判错。
_MC_CORRECT = "正确选项"
_MC_WRONG = "干扰项"

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

    enrich 出题再按 system prompt 分型：MC prompt（含 ``answer_index`` 字样）→ 产选择题 JSON，
    否则（开放 / 追问共用 schema）→ 产开放题 JSON。记录每次 role，供断言角色分槽 / 判卷是否被调用。
    ``verdict`` 只在 basic 判卷槽生效——MC 判卷是确定性代码、走不到这里，故 verdict 对 MC 无影响。
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
        payload: dict[str, Any]
        if role == "enrich":
            if "answer_index" in text:  # 选择题 prompt → 产 MC JSON（正确项恒在下标 0）
                payload = {
                    "question": "该知识点的核心是什么？",
                    "options": [_MC_CORRECT, _MC_WRONG],
                    "answer_index": 0,
                    "cited_evidence": [quote],
                }
            else:  # 开放 / 追问 prompt → 产开放题 JSON（共用 schema）
                payload = {"question": "该知识点的核心是什么？", "cited_evidence": [quote]}
        else:  # basic → 判卷
            payload = {"verdict": self._verdict, "cited_evidence": [quote]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


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


async def _assess(
    store: LearningStore,
    task: LearningTask,
    memory: LearningMemory,
    *,
    verdict: str = "对",
    answer: str = "我的作答",
) -> tuple[AssessmentResult, list[AgentEvent]]:
    """跑一轮考核（复用同一 ``memory`` 累积记账），返回 (result, events)。

    ``verdict`` 供开放 / 追问的 LLM 判卷槽；``answer`` 供选择题的确定性判卷（选 ``_MC_CORRECT`` /
    ``_MC_WRONG`` 定对错）与作为作答文本。
    """
    emitter, events, trace = _harness()
    result = await assess_once(
        task,
        store=store,
        provider=_AssessProvider(verdict=verdict),
        responder=ScriptedResponder(answer=answer),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )
    trace.close()
    return result, events


async def test_empty_kb_refuses_without_calling_any_llm() -> None:
    # eval case 2：空库"考我"→ 拒答、引导喂资源，绝不凭空编题（不调任何 LLM、不路由题型）。
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
    assert result.concept_state is None and result.question_type is None
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
    ("answer", "expected_verdict", "weak_expected", "followup_expected"),
    [
        (_MC_CORRECT, "对", False, False),  # 选对 → 判对、非薄弱概念不追踪、不追问
        (_MC_WRONG, "错", True, True),  # 选错 → 判错、入薄弱、触发 FOLLOWUP_GIVEN
    ],
)
async def test_fresh_concept_routes_to_mc_with_deterministic_grade(
    answer: str, expected_verdict: str, weak_expected: bool, followup_expected: bool
) -> None:
    # 首次接触概念（memory 空）→ 选择题（MC）：确定性判卷、无判卷 model span、provider 判卷 0 调用。
    emitter, events, trace = _harness()
    store, task, item_ids = _stocked_store()
    provider = _AssessProvider(verdict="对")  # verdict 对 MC 无效（MC 判卷是代码）
    memory = LearningMemory()

    result = await assess_once(
        task,
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer=answer),
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )

    # 事件流：MC 路径只有一对出题 model span（无判卷 model span）；判"错"再追加 FOLLOWUP_GIVEN。
    expected_stream = [
        _ASSESSMENT_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.QUESTION_ASKED,
        LearningEvent.ANSWER_JUDGED,
        LearningEvent.CONCEPT_STATE_CHANGED,
    ]
    if followup_expected:
        expected_stream.append(LearningEvent.FOLLOWUP_GIVEN)
    expected_stream.append(_ASSESSMENT_ENDED)
    types = [e.type for e in events]
    assert types == expected_stream

    # 路由到选择题（首次接触）——决策上脊柱 + 透出到 result。
    assert result.question_type == "选择题"
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    assert asked.payload["question_type"] == "选择题"
    assert asked.payload["item_id"] in item_ids
    assert asked.payload["options"] == [_MC_CORRECT, _MC_WRONG]  # MC 另带 options（用户视图）
    assert "answer_index" not in asked.payload  # 不泄露答案键给用户视图
    # eval case 3：cited_evidence 逐字属于被考 item 自己的证据（防幽灵题）。
    quotes_by_item = {
        item.item_id: {ev.quote for ev in item.evidence}
        for item in store.items_for_task(task.task_id)
    }
    assert asked.payload["cited_evidence"][0] in quotes_by_item[asked.payload["item_id"]]

    # 确定性判卷、不调判卷 LLM：provider 只被调 1 次（出题 enrich）、无 basic 调用。
    assert provider.calls == 1
    assert provider.roles == ["enrich"]

    # 判决 + 三态记账（fresh memory）。
    assert result.status == "judged"
    assert result.item_id in item_ids  # 亦把 item_id 收窄为 str（供下方 state_of）
    assert result.verdict == expected_verdict
    if weak_expected:
        assert result.weak_item_id == result.item_id
        assert result.concept_state == "薄弱"
        assert memory.state_of(result.item_id) == "薄弱"
        # 后置追问给正解：锚定被考 item_id + 正解含该 item 的摘要（确定性组文本，不产幽灵内容）。
        target_item = next(
            i for i in store.items_for_task(task.task_id) if i.item_id == result.item_id
        )
        followup = next(e for e in events if e.type == LearningEvent.FOLLOWUP_GIVEN)
        assert followup.payload["item_id"] == result.item_id
        assert target_item.summary in followup.payload["correct_answer"]
    else:
        assert result.weak_item_id is None
        assert result.concept_state is None
        assert memory.state_of(result.item_id) is None
        assert LearningEvent.FOLLOWUP_GIVEN not in types

    # span 树：assessment 根 → 只有 1 个 model 子 span（出题；MC 判卷是代码、无 span）。
    roots = trace.span_tree("run")
    assert len(roots) == 1
    assert roots[0].type == "assessment"
    assert [c.type for c in roots[0].children] == ["model"]
    assert all(child.end_ts is not None for child in roots[0].children)
    trace.close()


async def test_case8_routing_fresh_to_mc_then_weak_to_probe() -> None:
    # eval case 8：首次接触概念（memory 空）→ 选择题；把某概念喂成薄弱后复考 → 追问。断在事件流上。
    store, task, item_ids = _stocked_store()
    memory = LearningMemory()

    # 首次接触（memory 空）→ 选择题热身；选对 → 非薄弱概念不入记忆，记忆仍空。
    _r1, e1 = await _assess(store, task, memory, answer=_MC_CORRECT)
    asked1 = next(e for e in e1 if e.type == LearningEvent.QUESTION_ASKED)
    assert asked1.payload["question_type"] == "选择题"
    assert memory.weak_item_ids() == set()

    # 把某概念喂成薄弱（模拟此前答错）；复考时薄弱优先锁定它 → 路由到追问深挖。
    weak_item = item_ids[0]
    memory.record_verdict(weak_item, "错")
    assert memory.state_of(weak_item) == "薄弱"

    _r2, e2 = await _assess(store, task, memory, verdict="对")
    asked2 = next(e for e in e2 if e.type == LearningEvent.QUESTION_ASKED)
    assert asked2.payload["item_id"] == weak_item  # 薄弱优先锁定
    assert asked2.payload["question_type"] == "追问"  # 薄弱 → 追问


@pytest.mark.parametrize(
    ("verdict", "followup_expected"),
    [("对", False), ("勉强", True), ("错", True)],
)
async def test_observing_concept_routes_to_open_with_llm_grade_and_followup(
    verdict: str, followup_expected: bool
) -> None:
    # 观察中概念复考 → 开放（标准 LLM 判卷，有判卷 model span）；判"勉强 / 错"→ FOLLOWUP_GIVEN。
    store, task, item_ids = _stocked_store()
    target = item_ids[0]
    memory = LearningMemory()
    memory.record_verdict(target, "错")  # → 薄弱
    memory.record_verdict(target, "对")  # → 观察中
    assert memory.state_of(target) == "观察中"

    result, events = await _assess(store, task, memory, verdict=verdict)

    assert result.item_id == target  # 观察中仍在薄弱优先集，被锁定
    assert result.question_type == "开放"
    # 开放路径：出题 + 判卷两对 model span（LLM 判卷）。
    expected_stream = [
        _ASSESSMENT_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.QUESTION_ASKED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.ANSWER_JUDGED,
        LearningEvent.CONCEPT_STATE_CHANGED,
    ]
    if followup_expected:
        expected_stream.append(LearningEvent.FOLLOWUP_GIVEN)
    expected_stream.append(_ASSESSMENT_ENDED)
    types = [e.type for e in events]
    assert types == expected_stream

    # 路由决策上脊柱（与选择题 / 追问对称）：开放这条也在 QUESTION_ASKED 事件上断言。
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    assert asked.payload["question_type"] == "开放"

    if followup_expected:
        target_item = next(i for i in store.items_for_task(task.task_id) if i.item_id == target)
        followup = next(e for e in events if e.type == LearningEvent.FOLLOWUP_GIVEN)
        assert followup.payload["item_id"] == target
        # 正解含被考 item 的摘要 + 原文依据（evidence 段）——防回归把 evidence 从正解里删掉。
        assert target_item.summary in followup.payload["correct_answer"]
        assert target_item.evidence[0].quote in followup.payload["correct_answer"]
    else:
        assert LearningEvent.FOLLOWUP_GIVEN not in types


async def test_probe_path_wrong_answer_gives_followup() -> None:
    # 追问路径（薄弱概念复考）+ 判错：像开放题一样有判卷 model span，判错触发 FOLLOWUP_GIVEN。
    # 显式覆盖追问分支（此前追问用例只用 verdict=对，追问+错→追问 从未直接跑到）。
    store, task, item_ids = _stocked_store()
    target = item_ids[0]
    memory = LearningMemory()
    memory.record_verdict(target, "错")  # → 薄弱 → 复考走追问
    assert memory.state_of(target) == "薄弱"

    result, events = await _assess(store, task, memory, verdict="错")

    assert result.item_id == target
    assert result.question_type == "追问"
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    assert asked.payload["question_type"] == "追问"
    assert [e.type for e in events] == [
        _ASSESSMENT_STARTED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.QUESTION_ASKED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.ANSWER_JUDGED,
        LearningEvent.CONCEPT_STATE_CHANGED,
        LearningEvent.FOLLOWUP_GIVEN,
        _ASSESSMENT_ENDED,
    ]
    assert memory.state_of(target) == "薄弱"  # 复考判错 → 仍薄弱（连对归 0）


async def test_case4_wrong_answer_records_weak_by_item_id() -> None:
    # eval case 4：首次接触 → 选择题；选错 → 概念按 item_id 写入 Learning Memory（薄弱）+ 发事件。
    store, task, item_ids = _stocked_store()
    memory = LearningMemory()

    result, events = await _assess(store, task, memory, answer=_MC_WRONG)

    target = result.item_id
    assert target in item_ids
    assert result.question_type == "选择题"
    # 按 item_id 锚定入记忆：薄弱，且记忆里只此一个（未污染其它 item）。
    assert memory.state_of(target) == "薄弱"
    assert memory.weak_item_ids() == {target}
    assert result.concept_state == "薄弱"
    changed = next(e for e in events if e.type == LearningEvent.CONCEPT_STATE_CHANGED)
    assert changed.payload["item_id"] == target
    assert changed.payload["from_state"] is None
    assert changed.payload["to_state"] == "薄弱"
    assert changed.payload["consecutive_correct"] == 0
    # 时序：ANSWER_JUDGED < CONCEPT_STATE_CHANGED < FOLLOWUP_GIVEN < assessment.ended。
    types = [e.type for e in events]
    assert (
        types.index(LearningEvent.ANSWER_JUDGED)
        < types.index(LearningEvent.CONCEPT_STATE_CHANGED)
        < types.index(LearningEvent.FOLLOWUP_GIVEN)
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

    _result, events = await _assess(store, task, memory, verdict="对")

    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    # 出题锚定薄弱概念，而非全集随机会选的新概念（薄弱优先候选集把新概念排除）。
    assert asked.payload["item_id"] == weak_item
    assert asked.payload["item_id"] != natural
    assert asked.payload["question_type"] == "追问"  # 薄弱 → 追问


async def test_case6_one_correct_observes_two_correct_discharges() -> None:
    # eval case 6：连对两次才销账（观察中→销账），且薄弱优先把复考锁定到薄弱 item。
    # 薄弱 item 刻意 != 全集随机自然选择，才能真正区分薄弱优先 vs seed 巧合（照 case 5 同款对照）。
    store, task, item_ids = _stocked_store()
    natural = select_target(store.items_for_task(task.task_id), rng=new_rng(_SEED)).item_id
    target = next(i for i in item_ids if i != natural)
    memory = LearningMemory()
    memory.record_verdict(target, "错")  # 预置薄弱（!= natural）
    assert memory.state_of(target) == "薄弱"

    # 答对一次 → 观察中（仍在记忆）；薄弱优先把复考锁定到 target；薄弱 → 追问路径。
    r2, e2 = await _assess(store, task, memory, verdict="对")
    assert r2.item_id == target != natural  # 真正区分薄弱优先 vs seed 巧合
    assert r2.question_type == "追问"
    assert memory.state_of(target) == "观察中"
    assert target in memory.weak_item_ids()  # 观察中仍在表内，答对一次不销账
    assert r2.concept_state == "观察中"
    c2 = next(e for e in e2 if e.type == LearningEvent.CONCEPT_STATE_CHANGED)
    assert (
        c2.payload["from_state"],
        c2.payload["to_state"],
        c2.payload["consecutive_correct"],
    ) == ("薄弱", "观察中", 1)

    # 连续第二次答对 → 销账（从记忆移除）；观察中 → 开放路径。
    r3, e3 = await _assess(store, task, memory, verdict="对")
    assert r3.item_id == target
    assert r3.question_type == "开放"
    assert memory.state_of(target) is None
    assert target not in memory.weak_item_ids()
    assert r3.concept_state is None
    c3 = next(e for e in e3 if e.type == LearningEvent.CONCEPT_STATE_CHANGED)
    assert (
        c3.payload["from_state"],
        c3.payload["to_state"],
        c3.payload["consecutive_correct"],
    ) == ("观察中", "销账", 2)


class _RecordingResponder:
    """记录每次收到的 ``options``（async），恒返回注入 reply——断言 assess_once 的 options 透传。"""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.received_options: list[Sequence[str] | None] = []

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        self.received_options.append(options)
        return self._reply


async def test_mc_round_passes_options_to_responder() -> None:
    # 选择题轮：assess_once 把 mc.options 透传给 responder（供交互式渲染成单选），与 QUESTION_ASKED
    # 事件里的 options 是同一份候选。
    store, task, _ids = _stocked_store()
    memory = LearningMemory()
    responder = _RecordingResponder(_MC_CORRECT)
    emitter, events, trace = _harness()

    result = await assess_once(
        task,
        store=store,
        provider=_AssessProvider(verdict="对"),
        responder=responder,
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )
    trace.close()

    assert result.question_type == "选择题"
    assert responder.received_options == [[_MC_CORRECT, _MC_WRONG]]
    asked = next(e for e in events if e.type == LearningEvent.QUESTION_ASKED)
    assert list(responder.received_options[0] or []) == asked.payload["options"]


async def test_open_or_probe_round_passes_none_options_to_responder() -> None:
    # 非选择题轮（薄弱 → 追问）：assess_once 传 options=None（自由作答，无候选项）。
    store, task, item_ids = _stocked_store()
    memory = LearningMemory()
    memory.record_verdict(item_ids[0], "错")  # 薄弱 → 复考路由到追问（薄弱优先集只此一项）
    responder = _RecordingResponder("我的作答")
    emitter, _events, trace = _harness()

    result = await assess_once(
        task,
        store=store,
        provider=_AssessProvider(verdict="对"),
        responder=responder,
        memory=memory,
        emitter=emitter,
        rng=new_rng(_SEED),
    )
    trace.close()

    assert result.question_type == "追问"
    assert responder.received_options == [None]


async def _run_once(provider: Provider, emitter: EventEmitter) -> None:
    store, task, _ids = _stocked_store()
    await assess_once(
        task,
        store=store,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_CORRECT),  # fresh → MC；选对 → 判对
        memory=LearningMemory(),
        emitter=emitter,
        rng=new_rng(_SEED),
    )


async def test_whole_assessment_slice_is_deterministic_under_replay(tmp_path: Path) -> None:
    cassette_path = tmp_path / "cassette.json"

    # Pass 1：录制——fresh memory → 选择题（MC）。MC 判卷是确定性代码、不调 LLM，故只落 1 条 cassette
    # 键（enrich 出题），比开放题更确定（无判卷 model span）。
    inner = _AssessProvider(verdict="对")
    cassette = Cassette()
    emitter1, events1, trace1 = _harness()
    await _run_once(RecordingProvider(inner, cassette, _MODELS), emitter1)
    cassette.save(cassette_path)
    tree1 = trace1.span_tree("run")
    assert inner.calls == 1  # 只出题；MC 判卷无 LLM 调用

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
    # 回放没有再触碰 inner（仍是 1，证明整条单题竖切在 replay 下确定、不烧 token）。
    assert inner.calls == 1
    trace1.close()
    trace2.close()

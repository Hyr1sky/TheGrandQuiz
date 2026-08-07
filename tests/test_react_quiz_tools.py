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
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

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
from grandquiz.domain.learning.responder import Responder, ScriptedResponder
from grandquiz.domain.learning.store import LearningStore
from grandquiz.domain.learning.tools import register_learning_tools
from grandquiz.domain.learning.tools.start_quiz_tool import StartQuizResult
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.kernel.tools import ModelRetry, ToolContext, ToolRegistry
from grandquiz.providers.base import Completion, Message, Role, Usage
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayProvider

_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}
_QUOTE = "闭包捕获变量而非值"
_MC_CORRECT = "正确选项内容"
_MC_WRONG = "干扰项内容"


def _open_question_payload(question: str) -> dict[str, Any]:
    return {
        "question": question,
        "expected_points": [
            {
                "point_id": "core",
                "description": "说明核心含义",
                "required_claims": ["说明核心含义"],
                "cited_evidence": _QUOTE,
            },
            {
                "point_id": "boundary",
                "description": "说明关键区分",
                "required_claims": ["说明关键区分"],
                "cited_evidence": _QUOTE,
            },
        ],
        "reference_answer": _QUOTE,
        "cited_evidence": [_QUOTE],
    }


def _verdict_payload(verdict: str, *, answer_evidence_ids: list[str]) -> dict[str, Any]:
    if verdict == "对":
        matched, missing, diagnosis = ["core", "boundary"], [], "complete"
    elif verdict == "勉强":
        matched, missing, diagnosis = ["core"], ["boundary"], "missing_key_point"
    else:
        matched, missing, diagnosis = [], ["core", "boundary"], "wrong_focus"
    return {
        "verdict": verdict,
        "point_assessments": [
            {
                "point_id": point_id,
                "label": "matched" if point_id in matched else "missing",
                "answer_evidence_ids": [],
                "claim_assessments": [
                    {
                        "claim_id": f"{point_id}.claim_1",
                        "label": "matched" if point_id in matched else "missing",
                        "answer_evidence_ids": (answer_evidence_ids if point_id in matched else []),
                        "reason": "测试用 claim 判定。",
                    }
                ],
                "reason": "测试用逐点评判。",
            }
            for point_id in [*matched, *missing]
        ],
        "diagnosis": diagnosis,
        "reason": "测试判卷反馈",
        "cited_evidence": [_QUOTE],
    }


# --------------------------------------------------------------------------- #
# 领域装配脚手架
# --------------------------------------------------------------------------- #


def _stored_item(resource_id: str, index: int, concept: str) -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id=resource_id,
        concept=concept,
        summary=f"{concept} 摘要",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )


def _seed_store(
    store: LearningStore, concepts: list[str], *, url: str = "file://local/material"
) -> list[str]:
    resource = LearningResource.create(url=url)
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


async def _dispatch_quiz(
    registry: ToolRegistry, arguments: Mapping[str, Any], *, ctx: ToolContext
) -> str:
    return await registry.dispatch("start_quiz", {"scope": {"mode": "all"}, **arguments}, ctx=ctx)


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
            payload = _open_question_payload("请解释闭包如何捕获变量？")
        else:
            payload = _verdict_payload(
                self._verdict,
                answer_evidence_ids=re.findall(
                    r"^- \[(v1e\d+_\d+)\]",
                    messages[-1].content,
                    flags=re.MULTILINE,
                ),
            )
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
    store: LearningStore,
    memory: LearningMemory,
    provider: Any,
    responder: Responder,
    preferences: PreferenceMemory | None = None,
    quiz_seed: int = 0,
) -> None:
    register_learning_tools(
        registry,
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
    store = LearningStore()
    ids = _seed_store(store, ["闭包"])  # fresh memory → 路由到选择题
    memory = LearningMemory()
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=memory,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
    )
    emitter, events = _emitter_with_events()

    raw = await _dispatch_quiz(registry, {"count": 1}, ctx=_ctx(emitter))
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
    store = LearningStore()
    ids = _seed_store(store, ["闭包"])
    memory = LearningMemory()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=memory,
        provider=_McProvider(),
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 1}, ctx=_ctx(emitter))
    )

    assert result.rounds[0].verdict == "对"
    assert result.weak == []
    assert memory.state_of(ids[0]) is None  # 答对未追踪概念 → 不追踪
    assert "learning.followup_given" not in _types(events)


# --------------------------------------------------------------------------- #
# MC 选择器逐字选项文本（修 #2）：逐字命中 → 对；带前缀 → 错（复现软工具时代的坏行为）
# --------------------------------------------------------------------------- #


async def test_mc_verbatim_option_grades_correct() -> None:
    store = LearningStore()
    _seed_store(store, ["闭包"])
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=_McProvider(),
        responder=_SelectByIndexResponder(index=0),  # 逐字返回正确项文本（无 "B. " 前缀）
    )
    emitter, _ = _emitter_with_events()
    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 1}, ctx=_ctx(emitter))
    )
    assert result.rounds[0].verdict == "对"  # 逐字选项 → 确定性判卷命中


async def test_mc_prefixed_correct_option_grades_wrong() -> None:
    # 复现 #2：若把正确项加 "A. " 前缀（软工具时代 LLM 的坏行为）→ 逐字比对不命中 → 误判为错。
    # 硬化后 start_quiz 走选择器逐字提交，从根杜绝该前缀污染（见上一测试）。
    store = LearningStore()
    _seed_store(store, ["闭包"])
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=_McProvider(),
        responder=_PrefixResponder(index=0, prefix="A. "),  # 正确项被加前缀
    )
    emitter, _ = _emitter_with_events()
    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 1}, ctx=_ctx(emitter))
    )
    assert result.rounds[0].verdict == "错"  # 前缀毁掉逐字命中——正是选择器要杜绝的


# --------------------------------------------------------------------------- #
# 受控循环跑 N 题：一次 start_quiz 考 count 题，LLM 不进逐题循环
# --------------------------------------------------------------------------- #


async def test_start_quiz_runs_count_rounds() -> None:
    store = LearningStore()
    _seed_store(store, ["闭包", "装饰器"])
    registry = ToolRegistry()
    provider = _McProvider()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        # 两题都逐字选对：概念保持未追踪，两题都路由到选择题（受控循环逐题跑、判卷全走确定性代码）。
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 2}, ctx=_ctx(emitter))
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
    store = LearningStore()
    ids = _seed_store(store, ["闭包", "装饰器", "生成器"])
    memory = LearningMemory()
    memory.record_verdict(ids[0], "错")  # 仅 闭包（index 0）薄弱；装饰器 / 生成器 未追踪且未考过
    assert memory.state_of(ids[0]) == "薄弱"
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=memory,
        provider=_OpenProvider(verdict="对"),  # 薄弱 → 追问（LLM 判卷路径）
        responder=ScriptedResponder(answer="我的作答"),
    )
    emitter, _ = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 1, "focus": "weak"}, ctx=_ctx(emitter))
    )

    assert result.rounds[0].item_id == ids[0]  # focus=weak 锁定薄弱 闭包（!= mixed 会选的 装饰器）
    assert result.rounds[0].concept == "闭包"


# --------------------------------------------------------------------------- #
# 空库优雅拒答：无题可考 → refused、零 LLM 调用
# --------------------------------------------------------------------------- #


async def test_start_quiz_empty_kb_refused() -> None:
    store = LearningStore()  # 空库
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, _ = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 3}, ctx=_ctx(emitter))
    )

    assert result.status == "refused"
    assert result.asked == 0
    assert result.rounds == []
    assert result.weak == []
    assert provider.calls == 0  # 空库不调任何 LLM


# --------------------------------------------------------------------------- #
# 全局 KB 召回（修 #2 / ADR-0005）：start_quiz 选题候选池恒为全库，不绑任何标题
# --------------------------------------------------------------------------- #


async def test_start_quiz_recalls_items_from_global_kb() -> None:
    # 修 #2 / ADR-0005：会话不再绑标题，start_quiz 候选池 = 全库（store.all_items()）。任意 ingest
    # 进来的 item 都能被考到——LearningTask 已消解，无 task 分区可把知识切成孤岛。
    store = LearningStore()
    ids = _seed_store(store, ["闭包"])  # 知识进全局 KB 单池
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
    )
    emitter, _ = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 1}, ctx=_ctx(emitter))
    )

    assert result.status == "completed"  # 全局读 → 非空库
    assert result.asked == 1
    assert result.rounds[0].item_id == ids[0]  # 考到了全库里的 item
    assert result.rounds[0].concept == "闭包"  # concept_by_id 也走全局读（非 fallback 到 item_id）


# --------------------------------------------------------------------------- #
# 目录式 scope 透传（GKB-S4，修 #1 考错库）：resource_ids 收窄到指定材料 / 空命中拒答
# --------------------------------------------------------------------------- #


async def test_start_quiz_scope_honors_resource_ids() -> None:
    # scope 透传：库里有两份材料，resource_ids=[resA] → start_quiz 只从 resA 出题（透传 assess_once
    # → apply_scope）。不传 scope（全库随机）本可能选中 resB，故本断言钉死过滤而非巧合。
    store = LearningStore()
    a_ids = _seed_store(store, ["闭包", "变量提升"], url="file://local/a")
    b_ids = _seed_store(store, ["事件循环"], url="file://local/b")
    res_a = LearningResource.create(url="file://local/a").resource_id
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, _ = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(
            registry,
            {"count": 3, "scope": {"mode": "selected", "resource_ids": [res_a]}},
            ctx=_ctx(emitter),
        )
    )

    assert result.status == "completed"
    assert result.asked >= 1
    assert all(r.item_id in a_ids for r in result.rounds)  # 全在 scope 内（resA）
    assert not any(r.item_id in b_ids for r in result.rounds)  # 绝不出 scope 外（resB）


async def test_start_quiz_empty_scope_refused() -> None:
    # 点了库里没有的 resource_id → 过滤后空 → empty_scope 拒答（诚实，不静默考别的库），零 LLM。
    store = LearningStore()
    _seed_store(store, ["闭包"])
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(
            registry,
            {
                "count": 3,
                "scope": {"mode": "selected", "resource_ids": ["幽灵资源"]},
            },
            ctx=_ctx(emitter),
        )
    )

    assert result.status == "refused"
    assert result.asked == 0 and result.rounds == []
    assert provider.calls == 0  # 空 scope 在选题前拒答，绝不调 LLM
    refused = _payload_of(events, "learning.assessment_refused")
    assert refused["reason"] == "empty_scope"


async def test_start_quiz_unresolved_scope_refuses_without_llm_call() -> None:
    store = LearningStore()
    _seed_store(store, ["闭包"])
    provider = _McProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(
            registry,
            {"scope": {"mode": "unresolved", "requested_label": "不存在的文章"}},
            ctx=_ctx(emitter),
        )
    )

    assert result.status == "refused"
    assert provider.calls == 0
    refused = _payload_of(events, "learning.assessment_refused")
    assert refused == {"reason": "unresolved_scope", "requested_label": "不存在的文章"}


async def test_start_quiz_scope_is_required_and_selected_cannot_be_empty() -> None:
    store = LearningStore()
    _seed_store(store, ["闭包"])
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=_McProvider(),
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, _events = _emitter_with_events()

    with pytest.raises(ModelRetry, match="scope"):
        await registry.dispatch("start_quiz", {"count": 1}, ctx=_ctx(emitter))
    with pytest.raises(ModelRetry, match="resource_ids"):
        await registry.dispatch(
            "start_quiz",
            {"scope": {"mode": "selected", "resource_ids": []}},
            ctx=_ctx(emitter),
        )


# --------------------------------------------------------------------------- #
# 用户显式题型透传（GKB-S5，修 #1 错题型；ADR-0006）：question_type 经工具透传 assess_once
# --------------------------------------------------------------------------- #


async def test_start_quiz_question_type_intent_overrides_routing() -> None:
    # 参数级透传 + honor：fresh memory 自适应本会路由到"选择题"（routed），但用户点"简答" →
    # resolve_question_type 冻结映射到"开放"（effective 胜过自适应）。QUESTION_ASKED 同记 routed
    # / effective 供断言意图透传。mutation 可杀——若 question_type 未经工具透传 assess_once，
    # effective 退回选择题 → 走 MC 确定性判卷（provider.calls==1），两处断言齐红。
    store = LearningStore()
    _seed_store(store, ["闭包"])
    provider = _OpenProvider(verdict="对")  # 开放路径：enrich 出开放题 + basic 判卷
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer="我的作答"),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"count": 1, "question_type": "简答"}, ctx=_ctx(emitter))
    )

    assert result.status == "completed"
    asked = _payload_of(events, "learning.question_asked")
    assert asked["routed"] == "选择题"  # fresh memory 自适应本会给选择题
    assert asked["effective"] == "开放"  # 但用户点"简答" → 冻结映射到开放，胜过自适应（透传 honor）
    assert provider.calls == 2  # 开放判卷走 LLM（出题 + 判卷），非 MC 确定性判卷（那样只 1 次）


# --------------------------------------------------------------------------- #
# 语言偏好透传：偏好 > 硬兜底"中文"（ADR-0005：语言只来自偏好）
# --------------------------------------------------------------------------- #


async def test_start_quiz_passes_language_preference() -> None:
    store = LearningStore()
    _seed_store(store, ["闭包"])
    preferences = DictPreferenceMemory()
    preferences.set_preference(QUESTION_LANGUAGE_KEY, "英文")  # 偏好压过中文兜底
    provider = _LanguageCapturingProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
        preferences=preferences,
    )
    emitter, _ = _emitter_with_events()

    await _dispatch_quiz(registry, {"count": 1}, ctx=_ctx(emitter))

    # 出题槽的 system 提示确以偏好语言（英文）替换了 {{LANGUAGE}} 哨兵（透传到 assess_once）。
    # 精确锚定替换点 "请用 <语言> 提问"——模板正文另含字面"英文原词"，故只查裸"英文"会假通过。
    assert provider.enrich_systems
    assert "请用 英文 提问" in provider.enrich_systems[0]
    assert "请用 中文 提问" not in provider.enrich_systems[0]  # 未被中文兜底占位


# --------------------------------------------------------------------------- #
# 记放一致：一次 start_quiz（开放路径，出题 + 判卷两 LLM 槽）整轨迹零 token 回放
# --------------------------------------------------------------------------- #


async def test_start_quiz_record_then_replay_is_identical(tmp_path: Path) -> None:
    cassette_path = tmp_path / "quiz.json"

    async def drive(provider: Any) -> tuple[StartQuizResult, list[AgentEvent]]:
        store = LearningStore()
        ids = _seed_store(store, ["闭包"])
        memory = LearningMemory()
        memory.record_verdict(ids[0], "错")  # → 薄弱
        memory.record_verdict(ids[0], "对")  # → 观察中 → 开放题（出题 + 判卷两 LLM 槽都被录）
        registry = ToolRegistry()
        _register(
            registry,
            store=store,
            memory=memory,
            provider=provider,
            responder=ScriptedResponder(answer="闭包捕获引用"),
            quiz_seed=42,
        )
        emitter, events = _emitter_with_events()
        raw = await _dispatch_quiz(registry, {"count": 1}, ctx=_ctx(emitter))
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


# --------------------------------------------------------------------------- #
# SE-S4 集成（脚本化 provider）：分段逐题按位置解析题型，复用 resolve_question_type（无新裁决）
# --------------------------------------------------------------------------- #


class _SegmentProvider:
    """按出题提示词分型响应：MC 提示（含"单项选择题"）→ MC JSON；否则 → 开放题 JSON。

    据此可在一次 start_quiz 里既出选择题又出开放题（供分段调度断言逐题题型序列）；basic 判卷官
    注入 verdict="对"（开放题走 LLM 判卷路径、概念保持未追踪）。题干带调用序避免会话内去重门误伤。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        payload: dict[str, Any]
        if role == "enrich":
            if any("单项选择题" in m.content for m in messages):
                payload = {
                    "question": f"闭包的核心是什么？#{self.calls}",
                    "options": [_MC_CORRECT, _MC_WRONG],
                    "answer_index": 0,
                    "cited_evidence": [_QUOTE],
                }
            else:
                payload = _open_question_payload(f"请解释闭包如何捕获变量？#{self.calls}")
        else:
            payload = _verdict_payload(
                "对",
                answer_evidence_ids=re.findall(
                    r"^- \[(v1e\d+_\d+)\]",
                    messages[-1].content,
                    flags=re.MULTILINE,
                ),
            )
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


class _EitherResponder:
    """选择题（有 options）→ 逐字返回 options[index]；开放题（options=None）→ 返回固定文本。"""

    def __init__(self, *, index: int = 0, text: str = "我的作答") -> None:
        self._index = index
        self._text = text

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        return options[self._index] if options is not None else self._text


def _effectives(events: list[AgentEvent]) -> list[str]:
    """逐题 QUESTION_ASKED 的 effective 题型序列（断言分段调度确落到实际出题）。"""
    return [str(e.payload["effective"]) for e in events if e.type == "learning.question_asked"]


async def test_start_quiz_segments_schedule_per_position_types() -> None:
    # 分段：前 3 选择题 + 后 2 简答 → 逐题 effective 序列 = 选择×3 + 开放×2（简答冻结映射到开放）。
    # fresh memory 自适应本会给全"选择题"，故后 2 题的"开放"证明分段逐题透传了 intent——mutation
    # 可杀：若 segments 未接线 / 未逐位置透传，后 2 题退回"选择题"，序列断言变红。
    store = LearningStore()
    # 5 概念 = 5 题：mixed 覆盖优先逐题选不同 item，5 题全答对 → 概念保持未追踪、routed 恒为选择题。
    _seed_store(store, ["闭包", "装饰器", "生成器", "迭代器", "协程"])
    provider = _SegmentProvider()
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=_EitherResponder(index=0),  # MC 逐字选对；开放返回文本
    )
    emitter, events = _emitter_with_events()

    segs = [
        {"count": 3, "question_type": "选择题"},
        {"count": 2, "question_type": "简答"},
    ]
    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(registry, {"segments": segs}, ctx=_ctx(emitter))
    )

    assert result.status == "completed"
    assert result.asked == 5  # 总题数 = 各段 count 之和（3 + 2）；顶层 count 缺省被忽略
    assert _effectives(events) == ["选择题", "选择题", "选择题", "开放", "开放"]


async def test_start_quiz_segment_unknown_intent_falls_back_soft() -> None:
    # 未知题型意图段 → resolve_question_type 回落自适应（fresh memory → 选择题），
    # 不炸（fail-soft）。
    store = LearningStore()
    _seed_store(store, ["闭包", "装饰器"])
    provider = _McProvider()  # 回落到选择题 → MC 路径
    registry = ToolRegistry()
    _register(
        registry,
        store=store,
        memory=LearningMemory(),
        provider=provider,
        responder=ScriptedResponder(answer=_MC_CORRECT),
    )
    emitter, events = _emitter_with_events()

    result = StartQuizResult.model_validate_json(
        await _dispatch_quiz(
            registry,
            {"segments": [{"count": 2, "question_type": "念咒题"}]},
            ctx=_ctx(emitter),
        )
    )

    assert result.status == "completed"
    assert result.asked == 2  # 未知意图不炸，正常出 2 题
    assert _effectives(events) == ["选择题", "选择题"]  # 未知短语 → 回落自适应（fresh → 选择题）

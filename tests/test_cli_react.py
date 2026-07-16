"""R1-S4：真机 ReAct CLI（``grandquiz react``）——组装 + 会话循环 + Rich 呈现（骨架 + 回放冒烟）。

覆盖（AFK，不触真网 / 真 key）：
- ``ScopedEmitter`` 加固：去掉 partial-subclass 脆弱，任意未覆写的 EventEmitter 成员经
  ``__getattr__`` 委托 inner，不再 AttributeError（钉死回归）。
- ``QuizEventPrinter`` 新事件（AGENT_TURN_* / TOOL_CALL_*）渲染 + 动态文本 escape。
- ``run_react`` 会话循环：脚本化 provider 按剧本返回 tool_calls + 触发工具 + final，断言
  "入库→出题→答→判卷" 多步轨迹装配跑通、事件流正确。
- 整会话零 token 回放（record → replay，inner.calls 不变、事件序一致）。

不测真机模型（人机边界，见 backlog）。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest.fetch import FetchError
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.preference import QUESTION_LANGUAGE_KEY, SqlitePreferenceMemory
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.tools._scoped_emitter import ScopedEmitter
from grandquiz.interfaces.cli.app import (
    _file_source,  # pyright: ignore[reportPrivateUsage]
    run_react,
)
from grandquiz.interfaces.cli.composition import (
    _SYSTEM_PARTITION_BUDGET,  # pyright: ignore[reportPrivateUsage]
)
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.context import HeuristicTokenCounter
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Role, ToolCall, Usage
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayProvider

_QUOTE = "闭包捕获变量而非值"
_MC_CORRECT = "正确选项内容"
_MC_WRONG = "干扰项内容"
_MATERIAL_URL = "file://local/py.md"
_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}

_READER_JSON = json.dumps(
    {
        "topic": "闭包与作用域",
        "candidates": [
            {
                "concept": "闭包",
                "summary": "闭包捕获的是变量引用",
                "evidence": [{"quote": _QUOTE}],
                "confidence": 0.9,
            }
        ],
    },
    ensure_ascii=False,
)


class _ReactScriptProvider:
    """脚本化 provider：按 role + 系统提示分流，驱动整条 react 会话（不触真网 / 真 key）。

    真机里 role=basic 同槽承担三件事，本假件按系统提示区分：ReAct 决策（react 系统提示）、Reader
    深读（"深读器"提示）、判卷（"判卷官"提示）；role=enrich 出题。ReAct 决策据"是否已有 tool 结果"
    决定继续调工具 or 收敛 final，据最后一条 user 消息关键词选工具（R1-S6：考核触发 start_quiz，
    逐题一问一答在工具内部跑）。计自身调用次数（验证零 token 回放）。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        system = messages[0].content if messages and messages[0].role == "system" else ""
        if role == "enrich":  # 出题槽：恒出选择题（正确项在下标 0）
            payload = {
                "question": "闭包的核心是什么？",
                "options": [_MC_CORRECT, _MC_WRONG],
                "answer_index": 0,
                "cited_evidence": [_QUOTE],
            }
            return Completion(
                text=json.dumps(payload, ensure_ascii=False),
                usage=Usage(prompt_tokens=7, completion_tokens=3),
            )
        if "深读器" in system:  # Reader 深读槽
            return Completion(text=_READER_JSON, usage=Usage(prompt_tokens=9, completion_tokens=4))
        if "判卷官" in system:  # 判卷槽（本剧本走 MC 判卷、不打此槽，留作完备）
            return Completion(
                text=json.dumps({"verdict": "对", "cited_evidence": [_QUOTE]}, ensure_ascii=False),
                usage=Usage(prompt_tokens=5, completion_tokens=2),
            )
        # ReAct 决策槽（react 系统提示）：有 tool 结果 → 收敛 final；否则据 user 关键词选工具。
        tool_results = [m for m in messages if m.role == "tool"]
        user_messages = [m for m in messages if m.role == "user"]
        last_user = user_messages[-1].content if user_messages else ""
        if tool_results:
            return Completion(
                text="我已经帮你处理好了。", usage=Usage(prompt_tokens=5, completion_tokens=2)
            )
        if "入库" in last_user:
            call = ToolCall(id="c1", name="ingest", arguments={"url": _MATERIAL_URL})
        else:  # "考我一题" → 触发受控考核子流程（逐题一问一答在 start_quiz 内部跑，LLM 不进循环）
            call = ToolCall(id="c2", name="start_quiz", arguments={"count": 1})
        return Completion(
            text="", tool_calls=[call], usage=Usage(prompt_tokens=4, completion_tokens=1)
        )


_SESSION = ["请把材料入库", "考我一题"]


async def _drive_react(
    *, provider: Any, db_path: Path, materials_dir: Path, console: Console
) -> str:
    """把一整条两回合会话（入库 → 触发 start_quiz 考一题）喂给 run_react，返回 trace_id。

    考核内部逐题作答由注入的 ``ScriptedResponder`` 提供：恒选干扰项文本（逐字提交 → 确定性判错 →
    薄弱账落库），这正是 R1-S6 里 MC 选择器"逐字选项文本"契约的确定性替身（真机走 questionary）。
    """
    return await run_react(
        title="Py",
        db_path=db_path,
        materials_dir=materials_dir,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
        console=console,
        user_messages=_SESSION,
        seed=42,
        trace_db_path=db_path.parent / "trace.db",
    )


def _seed_material(materials_dir: Path) -> None:
    materials_dir.mkdir(parents=True, exist_ok=True)
    (materials_dir / "py.md").write_text(f"{_QUOTE}，这是关于闭包的材料。", encoding="utf-8")


def _event_types(trace_db: Path, trace_id: str) -> list[str]:
    store = TraceStore(trace_db)
    try:
        return [e.type for e in store.events(trace_id)]
    finally:
        store.close()


def _question_asked_payload(trace_db: Path, trace_id: str) -> Any:
    store = TraceStore(trace_db)
    try:
        return next(
            e.payload for e in store.events(trace_id) if e.type == LearningEvent.QUESTION_ASKED
        )
    finally:
        store.close()


def _emitter() -> tuple[EventEmitter, list[Any]]:
    events: list[Any] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


# --------------------------------------------------------------------------- #
# ScopedEmitter 加固：__getattr__ 委托 inner（未覆写成员不再 AttributeError）
# --------------------------------------------------------------------------- #


def test_scoped_emitter_delegates_unoverridden_member_to_inner() -> None:
    inner, events = _emitter()
    scoped = ScopedEmitter(inner, "root-span")

    # 覆写的三成员仍走 inner（单一真源：共享 seq / span 计数器）。
    inner.new_span_id()  # 推进 inner span 计数器
    assert scoped.trace_id == "t"
    assert scoped.new_span_id().startswith("t:")
    # 回归钉死：未覆写的内部态（此前因不调 super().__init__ 而缺失）现委托 inner，不再炸。
    assert scoped._span_counter == inner._span_counter  # pyright: ignore[reportPrivateUsage]

    # emit：无父事件重挂到 root_parent；带显式父的事件原样透传。
    scoped.emit("x.point")
    assert events[-1].parent_span_id == "root-span"
    scoped.emit("y.point", parent_span_id="explicit")
    assert events[-1].parent_span_id == "explicit"


# --------------------------------------------------------------------------- #
# QuizEventPrinter：ReAct 骨架事件（AGENT_TURN_* / TOOL_CALL_*）渲染 + 动态文本 escape
# --------------------------------------------------------------------------- #


def _event(event_type: str, payload: dict[str, Any]) -> AgentEvent:
    return AgentEvent(type=event_type, seq=0, ts=0.0, trace_id="t", payload=payload)


def test_printer_renders_tool_call_started() -> None:
    console = Console(record=True, width=80)
    QuizEventPrinter(console)(
        _event(EventType.TOOL_CALL_STARTED, {"tool_name": "start_quiz", "arguments": {}})
    )
    assert "start_quiz" in console.export_text()


def test_printer_renders_agent_turn_user_message_escaped() -> None:
    console = Console(record=True, width=80)
    # AGENT_TURN_STARTED 回显用户消息；含 markup 元字符不得抛 MarkupError（一律 escape）。
    QuizEventPrinter(console)(
        _event(EventType.AGENT_TURN_STARTED, {"user_message": "考我 [bold]闭包[/] 里的 [/red"})
    )
    out = console.export_text()
    assert "[bold]闭包[/]" in out  # 字面呈现，未被当 markup 吞掉


def test_printer_escapes_tool_name_markup() -> None:
    console = Console(record=True, width=80)
    # 工具名理论上可含元字符（防御性 escape）：不抛 MarkupError 即算过。
    QuizEventPrinter(console)(
        _event(EventType.TOOL_CALL_STARTED, {"tool_name": "weird[/x", "arguments": {}})
    )
    assert "weird[/x" in console.export_text()


def test_printer_shows_reason_on_wrong_verdict() -> None:
    # dogfood 痛点：答错看不出问题所在——判官 reason 以"问题：…"呈现，指出缺 / 偏了哪点。
    console = Console(record=True, width=100)
    QuizEventPrinter(console)(
        _event(
            LearningEvent.ANSWER_JUDGED,
            {"verdict": "错", "answer": "我记不清了", "reason": "没有回答捕获的是变量还是值"},
        )
    )
    out = console.export_text()
    assert "问题：没有回答捕获的是变量还是值" in out


def test_printer_shows_reason_on_borderline_verdict() -> None:
    console = Console(record=True, width=100)
    QuizEventPrinter(console)(
        _event(
            LearningEvent.ANSWER_JUDGED,
            {"verdict": "勉强", "answer": "大概是变量吧", "reason": "方向对但不够精确"},
        )
    )
    assert "问题：方向对但不够精确" in console.export_text()


def test_printer_escapes_reason_markup() -> None:
    # reason 是 LLM 动态文本、可含 markup 元字符 → 插入前 escape，不抛 MarkupError、字面呈现。
    console = Console(record=True, width=100)
    QuizEventPrinter(console)(
        _event(
            LearningEvent.ANSWER_JUDGED,
            {"verdict": "错", "answer": "答", "reason": "漏了 [bold]变量[/] 这点 [/red"},
        )
    )
    assert "[bold]变量[/]" in console.export_text()


def test_printer_omits_problem_line_when_reason_empty() -> None:
    # MC（代码判卷、无判官）reason 为空串 → 不打"问题："行，避免空诊断噪声。
    console = Console(record=True, width=100)
    QuizEventPrinter(console)(
        _event(LearningEvent.ANSWER_JUDGED, {"verdict": "错", "answer": "干扰项", "reason": ""})
    )
    assert "问题：" not in console.export_text()


def test_printer_correct_verdict_has_no_problem_line() -> None:
    # 判"对"：不呈现"问题："（reason 只在错 / 勉强诊断）。
    console = Console(record=True, width=100)
    QuizEventPrinter(console)(
        _event(
            LearningEvent.ANSWER_JUDGED,
            {"verdict": "对", "answer": "捕获的是变量本身", "reason": "命中要点"},
        )
    )
    assert "问题：" not in console.export_text()


# --------------------------------------------------------------------------- #
# run_react 会话循环：入库 → 出题 → 答 → 判卷 多步轨迹装配跑通（脚本化 provider）
# --------------------------------------------------------------------------- #


async def test_react_session_ingest_then_quiz_then_judge(tmp_path: Path) -> None:
    materials = tmp_path / "materials"
    _seed_material(materials)
    db = tmp_path / "learning.db"
    trace_db = tmp_path / "trace.db"
    console = Console(record=True, width=100)

    trace_id = await _drive_react(
        provider=_ReactScriptProvider(), db_path=db, materials_dir=materials, console=console
    )

    # 入库真的落进 SQLite（ingest 工具经 ReAct 循环触发 → 深读 → 审批 → 入库）。
    store = SqliteLearningStore(db)
    concepts = [it.concept for it in store.all_items()]
    item_id = store.all_items()[0].item_id
    store.close()
    assert concepts == ["闭包"]

    # 判错 → 薄弱账落持久 SQLite（代码记账，跨会话留存）。
    memory = SqliteLearningMemory(db)
    assert memory.weak_item_ids() == {item_id}
    assert memory.state_of(item_id) == "薄弱"
    memory.close()

    # 事件脊柱按序穿过整条竖切：入库(ITEM_CREATED) → 出题 → 判卷 → 记账 → 追问，两个 AGENT_TURN
    # （R1-S6：考核收敛成单次 start_quiz 工具调用——逐题问答在工具内部跑，不再占额外对话回合）。
    types = _event_types(trace_db, trace_id)
    assert types.count(EventType.AGENT_TURN_STARTED) == 2
    for expected in (
        LearningEvent.ITEM_CREATED,
        LearningEvent.QUESTION_ASKED,
        LearningEvent.ANSWER_JUDGED,
        LearningEvent.CONCEPT_STATE_CHANGED,
        LearningEvent.FOLLOWUP_GIVEN,
    ):
        assert expected in types
    learning = [t for t in types if t.startswith("learning.")]
    assert learning.index(LearningEvent.ITEM_CREATED) < learning.index(LearningEvent.QUESTION_ASKED)
    assert learning.index(LearningEvent.QUESTION_ASKED) < learning.index(
        LearningEvent.ANSWER_JUDGED
    )
    # 会话结束打印了 trace_id。
    assert trace_id in console.export_text()


async def test_react_session_zero_token_replay(tmp_path: Path) -> None:
    materials = tmp_path / "materials"
    _seed_material(materials)

    # Pass 1：录制——inner 真跑，ReAct 决策 + Reader + 出题三处 LLM 输出进 cassette。
    inner = _ReactScriptProvider()
    cassette = Cassette()
    db1 = tmp_path / "one" / "learning.db"
    db1.parent.mkdir(parents=True)
    trace_id1 = await _drive_react(
        provider=RecordingProvider(inner, cassette, _MODELS),
        db_path=db1,
        materials_dir=materials,
        console=Console(record=True, width=100),
    )
    cassette_path = tmp_path / "react.json"
    cassette.save(cassette_path)
    calls_after_record = inner.calls
    assert calls_after_record > 0

    # Pass 2：回放——全新 store/memory/registry/runner + 相同输入，零 token。
    replay = ReplayProvider(Cassette.load(cassette_path), _MODELS)
    db2 = tmp_path / "two" / "learning.db"
    db2.parent.mkdir(parents=True)
    trace_id2 = await _drive_react(
        provider=replay,
        db_path=db2,
        materials_dir=materials,
        console=Console(record=True, width=100),
    )

    assert inner.calls == calls_after_record  # 回放没有多触 inner（烧 0 token）
    # 事件序列 + 出题 payload 跨记放一致（整会话脊柱可复现）。
    assert _event_types(db1.parent / "trace.db", trace_id1) == _event_types(
        db2.parent / "trace.db", trace_id2
    )
    assert _question_asked_payload(db1.parent / "trace.db", trace_id1) == _question_asked_payload(
        db2.parent / "trace.db", trace_id2
    )


# --------------------------------------------------------------------------- #
# run_react 端到端：脚本化"选材料 + 定题型"轨迹——scope honor + 题型 honor（GKB-S6）
# --------------------------------------------------------------------------- #


class _ScopeTypeScriptProvider:
    """脚本化 provider：模拟用户"考<某份材料>的简答题"——ReAct 决策槽发一次
    ``start_quiz(resource_ids=[该材料 id], question_type='简答')``；enrich 出开放题、basic 判卷。

    驱动端到端 scope + 题型 honor（不触真网 / 真 key）：``resource_id`` 由构造注入（模拟 LLM 从
    注入的库存清单认出用户意图对应的 exact id）。有 tool 结果即收敛 final，故整会话恰一次考核。
    """

    def __init__(self, resource_id: str) -> None:
        self.calls = 0
        self._resource_id = resource_id

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        system = messages[0].content if messages and messages[0].role == "system" else ""
        if role == "enrich":  # 出题槽：effective=开放 → 出开放题（非 MC）
            return Completion(
                text=json.dumps(
                    {"question": "请解释闭包如何捕获变量？", "cited_evidence": [_QUOTE]},
                    ensure_ascii=False,
                ),
                usage=Usage(prompt_tokens=7, completion_tokens=3),
            )
        if "判卷官" in system:  # 开放判卷槽（本剧本真的打这个槽）
            return Completion(
                text=json.dumps({"verdict": "对", "cited_evidence": [_QUOTE]}, ensure_ascii=False),
                usage=Usage(prompt_tokens=5, completion_tokens=2),
            )
        # ReAct 决策槽：有 tool 结果 → final；否则发带 scope + 题型的 start_quiz。
        if [m for m in messages if m.role == "tool"]:
            return Completion(text="考完了。", usage=Usage(prompt_tokens=5, completion_tokens=2))
        call = ToolCall(
            id="q1",
            name="start_quiz",
            arguments={
                "count": 1,
                "resource_ids": [self._resource_id],
                "question_type": "简答",
            },
        )
        return Completion(
            text="", tool_calls=[call], usage=Usage(prompt_tokens=4, completion_tokens=1)
        )


def _seed_two_resources(db_path: Path) -> tuple[str, list[str], list[str]]:
    """在 run_react 打开的同一 learning db 预置两份材料；返回 (res_a_id, a_item_ids, b_item_ids)。

    多资源 stocked 场景：a=闭包那份（用户要考的），b=事件循环那份（scope 外，绝不应被考到）。
    """
    res_a = LearningResource.create(url="file://local/a.md")
    a_items = [
        KnowledgeItem.create(
            resource_id=res_a.resource_id,
            concept=concept,
            summary=f"{concept} 摘要",
            evidence=[Evidence(quote=_QUOTE)],
            confidence=0.9,
        )
        for i, concept in enumerate(["闭包", "作用域"])
    ]
    res_b = LearningResource.create(url="file://local/b.md")
    b_items = [
        KnowledgeItem.create(
            resource_id=res_b.resource_id,
            concept="事件循环",
            summary="事件循环 摘要",
            evidence=[Evidence(quote=_QUOTE)],
            confidence=0.9,
        )
    ]
    store = SqliteLearningStore(db_path)
    store.add_resource(res_a)
    store.add_items(a_items)
    store.add_resource(res_b)
    store.add_items(b_items)
    store.close()
    return res_a.resource_id, [it.item_id for it in a_items], [it.item_id for it in b_items]


async def test_react_scope_and_question_type_honored_end_to_end(tmp_path: Path) -> None:
    # 端到端"选材料 + 定题型"：脚本化 LLM 发
    # start_quiz(resource_ids=[res_a], question_type="简答")，
    # 逐题一问一答走 ScriptedResponder。断言 (1) scope honor——出题只落 res_a、绝不出 res_b；
    # (2) 题型 honor——fresh memory 自适应本会给"选择题"(routed)，但用户点"简答" → effective=开放。
    db = tmp_path / "learning.db"
    trace_db = tmp_path / "trace.db"
    res_a, a_ids, b_ids = _seed_two_resources(db)
    console = Console(record=True, width=100)

    provider = _ScopeTypeScriptProvider(resource_id=res_a)
    trace_id = await run_react(
        title="Py",
        db_path=db,
        materials_dir=tmp_path,
        provider=provider,
        responder=ScriptedResponder(answer="我的作答"),
        console=console,
        user_messages=["考闭包那份材料的简答题"],
        seed=42,
        trace_db_path=trace_db,
    )

    store = TraceStore(trace_db)
    try:
        asked = [
            e.payload for e in store.events(trace_id) if e.type == LearningEvent.QUESTION_ASKED
        ]
    finally:
        store.close()

    assert asked, "应至少出一题"
    # scope honor：所有出题都落在 res_a（跨材料选对——绝不出 scope 外的 res_b）。
    assert all(p["item_id"] in a_ids for p in asked)
    assert not any(p["item_id"] in b_ids for p in asked)
    # 题型 honor：用户点"简答" → effective=开放，胜过 fresh memory 自适应会给的"选择题"(routed)。
    assert all(p["routed"] == "选择题" for p in asked)
    assert all(p["effective"] == "开放" for p in asked)


# --------------------------------------------------------------------------- #
# run_react 会话循环：单轮冒未预期异常被兜住，不杀整场会话（修 dogfood "神了" 崩溃）
# --------------------------------------------------------------------------- #


class _CrashFirstTurnProvider:
    """首轮（user 含"崩"）model 调用直接抛未预期异常；其余轮正常收敛 final。计调用次数。

    模拟 run_agent_turn 里冒出的未预期异常（模型层炸 / MaxIterations 等）——会话循环须兜住这一轮、
    继续下一轮，绝不让一轮坏 turn 杀整场（dogfood "神了" 的会话级鲁棒）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        user_messages = [m for m in messages if m.role == "user"]
        last_user = user_messages[-1].content if user_messages else ""
        if "崩" in last_user:
            raise RuntimeError("模型炸了")  # 未打标 → FATAL，会从 run_agent_turn 冒出
        return Completion(text="好的，收到。", usage=Usage(prompt_tokens=1, completion_tokens=1))


async def test_react_session_survives_crashing_turn(tmp_path: Path) -> None:
    materials = tmp_path / "materials"
    _seed_material(materials)
    db = tmp_path / "learning.db"
    console = Console(record=True, width=100)

    provider = _CrashFirstTurnProvider()
    trace_id = await run_react(
        title="Py",
        db_path=db,
        materials_dir=materials,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
        console=console,
        user_messages=["让你崩一下", "正常问一句"],
        seed=42,
        trace_db_path=tmp_path / "trace.db",
    )

    out = console.export_text()
    # 第一轮崩溃被兜住（打印友好提示），第二轮仍正常回复——整场会话没被一轮坏 turn 杀掉。
    assert "好的，收到。" in out
    assert provider.calls == 2  # 两轮都跑到（坏轮没吞掉后续轮）
    assert trace_id  # 会话正常结束、返回 trace_id（未从 run_react 冒泡崩溃）


# --------------------------------------------------------------------------- #
# run_react 装配 ContextBuilder：学情记忆（薄弱 + 偏好）注入 ReAct 系统前言区
# --------------------------------------------------------------------------- #


class _CaptureReactProvider:
    """记录 ReAct 决策槽（role=basic + react 系统提示）收到的 messages，恒收敛 final、不调工具。"""

    def __init__(self) -> None:
        self.react_messages: list[list[Message]] = []

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        system = messages[0].content if messages and messages[0].role == "system" else ""
        if role == "basic" and "考核驱动的学习助手" in system:
            self.react_messages.append(list(messages))
        return Completion(text="好的。", usage=Usage(prompt_tokens=1, completion_tokens=1))


def _seed_weak_concept(db_path: Path) -> None:
    """在 run_react 打开的同一 learning db 里预置一个判错的薄弱概念（跨会话留存的确定性替身）。"""
    resource = LearningResource.create(url=_MATERIAL_URL)
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="闭包",
        summary="闭包捕获的是变量引用",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )
    store = SqliteLearningStore(db_path)
    store.add_resource(resource)
    store.add_items([item])
    store.close()
    memory = SqliteLearningMemory(db_path)
    memory.record_verdict(item.item_id, "错")  # → 薄弱
    memory.close()


async def test_react_injects_learner_context_into_system(tmp_path: Path) -> None:
    # 兑现"记忆互通复用"：预置薄弱概念后，agent 不调工具就在系统前言区看到它（学情注入分区）。
    db = tmp_path / "learning.db"
    _seed_weak_concept(db)
    provider = _CaptureReactProvider()
    await run_react(
        title="Py",
        db_path=db,
        materials_dir=tmp_path,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
        console=Console(record=True, width=100),
        user_messages=["我哪里薄弱"],
        seed=42,
        trace_db_path=tmp_path / "trace.db",
    )
    assert provider.react_messages, "ReAct 决策槽应被调用"
    system_blocks = [m.content for m in provider.react_messages[0] if m.role == "system"]
    joined = "\n".join(system_blocks)
    assert "闭包" in joined  # 薄弱概念名注入
    assert "薄弱" in joined  # 状态注入
    # 学情块与 react 系统提示是分开的两条 system 消息（分区装配，非拼进一条）。
    assert len(system_blocks) == 2


async def test_react_infers_question_language_preference_from_user_message(tmp_path: Path) -> None:
    # 自进化第一个具体能力：react 每轮观察用户原文语言，推断写入 question_language 偏好——
    # 不需要用户显式 --prefer-lang，纯从这轮英文消息本身就能学出来。
    db = tmp_path / "learning.db"
    provider = _CaptureReactProvider()
    await run_react(
        title="Py",
        db_path=db,
        materials_dir=tmp_path,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
        console=Console(record=True, width=100),
        user_messages=["Please tell me which concepts I am weak at"],
        seed=42,
        trace_db_path=tmp_path / "trace.db",
    )
    preferences = SqlitePreferenceMemory(db)
    try:
        pref = preferences.get_preference(QUESTION_LANGUAGE_KEY)
        assert pref is not None
        assert pref.value == "英文"
        assert 0.0 < pref.confidence < 1.0  # 推断值，与显式设置（1.0）可区分
    finally:
        preferences.close()


async def test_react_short_message_does_not_touch_language_preference(tmp_path: Path) -> None:
    # 信号太弱的消息（如"好的"）不该被记成一次语言观察——detect_language 对短文本返回 None。
    db = tmp_path / "learning.db"
    provider = _CaptureReactProvider()
    await run_react(
        title="Py",
        db_path=db,
        materials_dir=tmp_path,
        provider=provider,
        responder=ScriptedResponder(answer=_MC_WRONG),
        console=Console(record=True, width=100),
        user_messages=["好的"],
        seed=42,
        trace_db_path=tmp_path / "trace.db",
    )
    preferences = SqlitePreferenceMemory(db)
    try:
        assert preferences.get_preference(QUESTION_LANGUAGE_KEY) is None
    finally:
        preferences.close()


# --------------------------------------------------------------------------- #
# Context compression（C-wire 增量 1）：生产装配的 budget 数字须留够真实内容的余量
# --------------------------------------------------------------------------- #


def test_react_system_prompt_fits_under_system_partition_budget() -> None:
    # 生产装配用真实 react 系统提示（非假件）：token 数须显著低于 _SYSTEM_PARTITION_BUDGET，
    # 防未来提示膨胀被 BudgetCompressionPolicy 静默头截断（钉死 gap-review 的具体数字选择）。
    tokens = HeuristicTokenCounter().count(load_prompt("react_system").text)
    assert tokens < _SYSTEM_PARTITION_BUDGET


# --------------------------------------------------------------------------- #
# _file_source 路径穿越守卫：解析后仍须在 materials_dir 内，否则拒（归一 FetchError）
# --------------------------------------------------------------------------- #


def test_file_source_reads_normal_local_file(tmp_path: Path) -> None:
    # 正常 file://local/<名> 不被误伤：读到材料内容。
    materials = tmp_path / "materials"
    _seed_material(materials)
    source = _file_source(materials)
    assert "闭包" in source("file://local/py.md")


def test_file_source_reads_normal_nested_file(tmp_path: Path) -> None:
    # 子目录下的正常相对路径仍放行（守卫只挡越界，不挡目录内嵌套）。
    materials = tmp_path / "materials"
    (materials / "sub").mkdir(parents=True)
    (materials / "sub" / "a.md").write_text("嵌套材料", encoding="utf-8")
    source = _file_source(materials)
    assert source("file://local/sub/a.md") == "嵌套材料"


def test_file_source_rejects_dotdot_traversal(tmp_path: Path) -> None:
    # 双点穿越逃出材料目录读任意文件 → 拒（FetchError），报错含目录路径。
    materials = tmp_path / "materials"
    materials.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("绝密", encoding="utf-8")
    source = _file_source(materials)
    with pytest.raises(FetchError) as exc:
        source("file://local/../secret.txt")
    assert str(materials.resolve()) in str(exc.value)


def test_file_source_rejects_deep_traversal_to_etc_passwd(tmp_path: Path) -> None:
    # 经典攻击 file://local/../../etc/passwd 逃逸 → 拒，不读到目录外文件。
    materials = tmp_path / "materials"
    materials.mkdir(parents=True)
    source = _file_source(materials)
    with pytest.raises(FetchError):
        source("file://local/../../../../../../etc/passwd")


def test_file_source_rejects_absolute_path_escape(tmp_path: Path) -> None:
    # 绝对路径注入（url path 以 / 开头指向目录外）→ 拒。
    materials = tmp_path / "materials"
    materials.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("目录外", encoding="utf-8")
    source = _file_source(materials)
    with pytest.raises(FetchError):
        # 多段 ../ 归一后指向 tmp_path/outside.txt（materials 的父目录），越界。
        source("file://local/../outside.txt")

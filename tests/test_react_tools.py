"""R1-S2：非交互考官 / 记忆工具接入 ReAct 循环——工具边界隔离 + 端到端零 token 回放。

两个 domain 工具注册进 kernel ``ToolRegistry``（住 domain 层、kernel↛domain 不破）：

- ``ingest(url)``：wrap ``ingest_resource``——内部 span（fetch / Reader model / item_created）嵌在
  本次 TOOL_CALL span 之下（经作用域化 emitter 把编排根 span 重挂到 ctx.parent_span_id）；ReAct
  消息上下文只收结构化结果字符串（入库知识点数 + 概念名），看不到考官内部 model 调用 / 消息。
- ``query_weak_concepts()``：只读 Learning Memory + store，无 LLM、确定性。

确定性核心（query 结构 / 隔离不变量 / 记放一致）走 TDD；ingest 内部的 Reader LLM 槽经
脚本化 / 回放 provider 验证（不 unit-TDD LLM 本身）。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
    LearningTask,
)
from grandquiz.domain.learning.store import LearningStore
from grandquiz.domain.learning.tools import (
    IngestToolResult,
    WeakConceptsResult,
    register_learning_tools,
)
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.kernel.trace import Span, TraceStore
from grandquiz.providers.base import Completion, Message, Role, ToolCall, Usage
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayProvider

_URL = "file://local/material.txt"
_CONTENT = "事件脊柱是脊柱；零 token 回放是回放。"
_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}

# 脚本化 Reader 输出（两个候选）——独立于抓取内容，只需非空证据即可铸出 KnowledgeItem。
_READER_JSON = json.dumps(
    {
        "candidates": [
            {
                "concept": "事件脊柱",
                "summary": "同一条事件流的四个消费者",
                "evidence": [{"quote": "事件脊柱是脊柱"}],
                "confidence": 0.9,
            },
            {
                "concept": "确定性回放",
                "summary": "零 token 回放",
                "evidence": [{"quote": "零 token 回放是回放"}],
                "confidence": 0.8,
            },
        ]
    },
    ensure_ascii=False,
)
_EXPECTED_CONCEPTS = ["事件脊柱", "确定性回放"]


# --------------------------------------------------------------------------- #
# 领域装配脚手架
# --------------------------------------------------------------------------- #


def _stored_item(resource_id: str, index: int, concept: str) -> KnowledgeItem:
    return KnowledgeItem.create(
        resource_id=resource_id,
        index=index,
        concept=concept,
        summary=f"{concept} 摘要",
        evidence=[Evidence(quote=f"{concept} 原文")],
        confidence=0.9,
    )


def _seed_store(store: LearningStore, task: LearningTask, concepts: list[str]) -> list[str]:
    """给某 task 建一个资源 + 一批 item，返回 item_id 列表（顺序同 concepts）。"""
    store.add_task(task)
    resource = LearningResource.create(task_id=task.task_id, url=f"file://local/{task.title}")
    store.add_resource(resource)
    items = [_stored_item(resource.resource_id, i, c) for i, c in enumerate(concepts)]
    store.add_items(items)
    return [it.item_id for it in items]


def _fixed_source(_url: str) -> str:
    return _CONTENT


def _keep_all(_item: KnowledgeItem) -> bool:
    return True


def _ingest_deps(task: LearningTask, provider: Any) -> dict[str, Any]:
    """ingest 工具的注入依赖（source 注入固定内容、审批 keep-all，同 CLI 组装点形状）。"""
    return {
        "source": _fixed_source,
        "provider": provider,
        "approval": ScriptedApprovalGate(keep=_keep_all),
        "max_bytes": 1_000_000,
        "allowed_domains": {"local"},
    }


def _emitter_with_events() -> tuple[EventEmitter, list[Any]]:
    events: list[Any] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


# --------------------------------------------------------------------------- #
# query_weak_concepts —— 确定性只读工具（TDD 核心）
# --------------------------------------------------------------------------- #


async def test_query_weak_concepts_reports_tracked_concepts_sorted() -> None:
    task = LearningTask.create("React")
    store = LearningStore()
    ids = _seed_store(store, task, ["hooks", "fiber", "context"])
    memory = LearningMemory()
    memory.record_verdict(ids[0], "错")  # hooks → 薄弱
    memory.record_verdict(ids[1], "错")  # fiber → 薄弱
    memory.record_verdict(ids[1], "对")  # fiber → 观察中
    # ids[2] context 从未考 → 不追踪，不应出现

    registry = ToolRegistry()
    register_learning_tools(
        registry, task=task, store=store, memory=memory, **_ingest_deps(task, provider=None)
    )
    raw = await registry.dispatch("query_weak_concepts", {})
    result = WeakConceptsResult.model_validate_json(raw)

    # 只含被追踪者、携真实 concept 名与状态；context 从未考 → 不追踪、不出现
    assert {(w.concept, w.state) for w in result.weak} == {("hooks", "薄弱"), ("fiber", "观察中")}
    assert "context" not in {w.concept for w in result.weak}
    # item_id 升序不变量（确定性顺序）
    assert [w.item_id for w in result.weak] == sorted(w.item_id for w in result.weak)


async def test_query_weak_concepts_excludes_other_task_items() -> None:
    task = LearningTask.create("React")
    other = LearningTask.create("Rust")
    store = LearningStore()
    react_ids = _seed_store(store, task, ["hooks"])
    other_ids = _seed_store(store, other, ["ownership"])
    memory = LearningMemory()
    memory.record_verdict(react_ids[0], "错")  # 本任务薄弱
    memory.record_verdict(other_ids[0], "错")  # 他任务薄弱——不应泄漏进本任务查询

    registry = ToolRegistry()
    register_learning_tools(
        registry, task=task, store=store, memory=memory, **_ingest_deps(task, provider=None)
    )
    result = WeakConceptsResult.model_validate_json(
        await registry.dispatch("query_weak_concepts", {})
    )
    assert {w.concept for w in result.weak} == {"hooks"}


async def test_query_weak_concepts_empty_when_no_weak() -> None:
    task = LearningTask.create("React")
    store = LearningStore()
    _seed_store(store, task, ["hooks"])
    registry = ToolRegistry()
    register_learning_tools(
        registry, task=task, store=store, memory=LearningMemory(), **_ingest_deps(task, None)
    )
    result = WeakConceptsResult.model_validate_json(
        await registry.dispatch("query_weak_concepts", {})
    )
    assert result.weak == []


# --------------------------------------------------------------------------- #
# 脚本化 provider：ReAct 选工具 + 内部 Reader 深读共用一个 provider（按 messages 分流）
# --------------------------------------------------------------------------- #


class _ScriptedReactIngestProvider:
    """确定性：无 tool 结果 → 出 ingest tool_call；见 tool 结果 → final；Reader 消息 → 结构化 JSON。

    ReAct 的选工具调用与 ingest 内部的 Reader 调用都走 role="basic"，靠 messages 内容分流：
    Reader 的 user 消息含"不可信抓取内容"标记，据此返回 Reader JSON（内部 model 调用）。计自身次数。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        self.calls += 1
        joined = "\n".join(m.content for m in messages)
        if "不可信抓取内容" in joined:  # 内部 Reader 深读调用
            return Completion(text=_READER_JSON, usage=Usage(prompt_tokens=11, completion_tokens=5))
        if any(m.role == "tool" for m in messages):  # 见到工具结果 → 收敛 final
            return Completion(
                text="已完成 ingest", usage=Usage(prompt_tokens=4, completion_tokens=3)
            )
        return Completion(  # 首轮：请求调 ingest
            text="",
            tool_calls=[ToolCall(id="c1", name="ingest", arguments={"url": _URL})],
            usage=Usage(prompt_tokens=3, completion_tokens=1),
        )


def _build_ingest_registry(task: LearningTask, provider: Any) -> tuple[ToolRegistry, LearningStore]:
    store = LearningStore()
    registry = ToolRegistry()
    register_learning_tools(
        registry, task=task, store=store, memory=LearningMemory(), **_ingest_deps(task, provider)
    )
    return registry, store


# --------------------------------------------------------------------------- #
# ingest 工具：结构化结果 + 隔离不变量 + span 嵌套在 TOOL_CALL 之下
# --------------------------------------------------------------------------- #


async def test_react_ingest_returns_structured_result() -> None:
    task = LearningTask.create("React")
    provider = _ScriptedReactIngestProvider()
    registry, store = _build_ingest_registry(task, provider)
    emitter, _ = _emitter_with_events()
    runner = Runner(provider=provider, emitter=emitter, tools=registry)

    reply = await runner.run_agent_turn("请 ingest 这份材料")
    assert reply == "已完成 ingest"
    # 工具确实入库两个概念
    assert [it.concept for it in store.items_for_task(task.task_id)] == _EXPECTED_CONCEPTS


async def test_react_ingest_spans_nest_under_tool_call_and_messages_are_isolated() -> None:
    task = LearningTask.create("React")
    provider = _ScriptedReactIngestProvider()
    registry, _ = _build_ingest_registry(task, provider)
    store_trace = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(store_trace.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")
    runner = Runner(provider=provider, emitter=emitter, tools=registry)

    await runner.run_agent_turn("请 ingest 这份材料")

    roots = store_trace.span_tree("t")
    assert len(roots) == 1
    root = roots[0]
    assert root.type == "agent_turn"
    # ReAct 主体：model（选工具）→ tool_call（ingest）→ model（final）
    assert [c.type for c in root.children] == ["model", "tool_call", "model"]

    tool_span = root.children[1]
    assert tool_span.input["tool_name"] == "ingest"
    # 内部编排根 span（ingest）重挂在 TOOL_CALL 之下（不是 agent_turn 的直接子节点）
    assert [c.type for c in tool_span.children] == ["ingest"]
    ingest_span = tool_span.children[0]
    # Reader 的 model span 嵌在 ingest 之下（深层，不冒到 ReAct 主体）
    assert "model" in [c.type for c in ingest_span.children]

    # 隔离：ReAct 只收结构化结果字符串
    assert tool_span.output is not None
    result = IngestToolResult.model_validate_json(str(tool_span.output["result"]))
    assert result.status == "read"
    assert result.item_count == 2
    assert result.concepts == _EXPECTED_CONCEPTS

    # 隔离：第二次 ReAct model 调用（final）的 messages 里，tool 结果就是那段结构化 JSON，
    # 且**不含**任何 Reader 内部消息（考官内部 model 调用 / 不可信内容不泄漏进 ReAct 上下文）。
    final_model = root.children[2]
    msgs: list[dict[str, Any]] = list(final_model.input["messages"])
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert IngestToolResult.model_validate_json(tool_msgs[0]["content"]).concepts == (
        _EXPECTED_CONCEPTS
    )
    assert not any("不可信抓取内容" in m["content"] for m in msgs)


async def test_ingest_internal_model_spans_never_bubble_to_agent_turn() -> None:
    """加固：agent_turn 直属 model span 只有 ReAct 两次（选工具 + final），Reader 那次不在其列。"""
    task = LearningTask.create("React")
    provider = _ScriptedReactIngestProvider()
    registry, _ = _build_ingest_registry(task, provider)
    store_trace = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(store_trace.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id="t")
    runner = Runner(provider=provider, emitter=emitter, tools=registry)
    await runner.run_agent_turn("请 ingest 这份材料")

    root = store_trace.span_tree("t")[0]
    direct_models = [c for c in root.children if c.type == "model"]
    assert len(direct_models) == 2  # Reader 的第三次 model 调用不在 agent_turn 直属层


# --------------------------------------------------------------------------- #
# 记放一致：一次"ReAct 调 ingest"的 turn，整轨迹零 token 回放（inner.calls 不变）
# --------------------------------------------------------------------------- #


def _summ(spans: list[Span]) -> list[dict[str, Any]]:
    return [
        {
            "type": s.type,
            "start_ts": s.start_ts,
            "end_ts": s.end_ts,
            "tokens": s.tokens,
            "children": _summ(s.children),
        }
        for s in spans
    ]


async def test_react_ingest_record_then_replay_is_byte_identical(tmp_path: Path) -> None:
    task = LearningTask.create("React")
    cassette_path = tmp_path / "react_ingest.json"

    # Pass 1：录制——inner 脚本化 provider 真跑，ReAct 选工具 + Reader 深读都进 cassette。
    inner = _ScriptedReactIngestProvider()
    cassette = Cassette()
    recording = RecordingProvider(inner, cassette, _MODELS)
    registry1, _ = _build_ingest_registry(task, recording)
    store1 = TraceStore(":memory:")
    sink1 = EventSink()
    sink1.subscribe(store1.record)
    runner1 = Runner(
        provider=recording,
        emitter=EventEmitter(sink1, ManualClock(), trace_id="t"),
        tools=registry1,
    )
    r1 = await runner1.run_agent_turn("请 ingest 这份材料")
    cassette.save(cassette_path)
    tree1 = store1.span_tree("t")
    inner_calls_after_record = inner.calls
    assert inner_calls_after_record == 3  # 选工具 + Reader 深读 + final

    # Pass 2：回放——全新 Runner / registry / store / memory + 重置 ManualClock + 相同输入。
    replay = ReplayProvider(Cassette.load(cassette_path), _MODELS)
    registry2, _ = _build_ingest_registry(task, replay)
    store2 = TraceStore(":memory:")
    sink2 = EventSink()
    sink2.subscribe(store2.record)
    runner2 = Runner(
        provider=replay,
        emitter=EventEmitter(sink2, ManualClock(), trace_id="t"),
        tools=registry2,
    )
    r2 = await runner2.run_agent_turn("请 ingest 这份材料")
    tree2 = store2.span_tree("t")

    assert r1 == r2 == "已完成 ingest"
    assert inner.calls == inner_calls_after_record  # 回放没有多触 inner（烧 0 token）
    assert _summ(tree1) == _summ(tree2)  # span 树结构 / ts / tokens 全对齐


def test_tool_context_is_kernel_generic() -> None:
    """ToolContext 是 kernel 级通用信封：只携 emitter + parent_span_id，不认识领域语义。"""
    emitter, _ = _emitter_with_events()
    ctx = ToolContext(emitter=emitter, parent_span_id="t:s3")
    assert ctx.parent_span_id == "t:s3"
    assert ctx.emitter is emitter


async def test_query_tool_dispatch_ignores_context() -> None:
    """query 工具是 context-free：即便 dispatch 递入 ctx 也照常工作（wants_context=False）。"""
    task = LearningTask.create("React")
    store = LearningStore()
    ids = _seed_store(store, task, ["hooks"])
    memory = LearningMemory()
    memory.record_verdict(ids[0], "错")
    registry = ToolRegistry()
    register_learning_tools(
        registry, task=task, store=store, memory=memory, **_ingest_deps(task, None)
    )
    emitter, _ = _emitter_with_events()
    raw = await registry.dispatch(
        "query_weak_concepts", {}, ctx=ToolContext(emitter=emitter, parent_span_id="t:s0")
    )
    assert WeakConceptsResult.model_validate_json(raw).weak[0].concept == "hooks"

"""ingest 竖切测试（缝 1，主缝）——跑在事件 / trace 流上，端到端经事件观察。

覆盖两个 eval 用例：case 1（未审批候选不入库）、case 7（fetch 失败不产幽灵 item）；
外加"整条 ingest 竖切在 replay 下确定"（用 M2 的 Record/Replay Provider）。
断言外部行为——事件流、span 树、store 记账——不耦合实现细节。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from grandquiz.domain.learning.approval import APPROVAL_REQUESTED, ScriptedApprovalGate
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.harness import build_event_harness as _harness
from grandquiz.evals.harness import summarize_spans as _summ
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Provider, Role, Usage
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayMiss, ReplayProvider

_ALLOWED = {"example.com"}
_URL = "https://example.com/react-hooks"
_MODELS: dict[Role, str] = {"basic": "deepseek-x", "enrich": "qwen-x"}

_INGEST_STARTED = "ingest.started"
_INGEST_ENDED = "ingest.ended"

# 三个候选；审批只保留其中两个（闭包 / 事件循环），丢弃"变量提升"。
_READER_JSON = json.dumps(
    {
        "topic": "React Hooks 与 JS 运行时",
        "candidates": [
            {"concept": "闭包", "summary": "s1", "evidence": [{"quote": "q1"}], "confidence": 0.9},
            {
                "concept": "变量提升",
                "summary": "s2",
                "evidence": [{"quote": "q2"}],
                "confidence": 0.8,
            },
            {
                "concept": "事件循环",
                "summary": "s3",
                "evidence": [{"quote": "q3"}],
                "confidence": 0.7,
            },
        ],
    }
)


class _FixedProvider:
    """返回固定 JSON、计被调次数。``role`` 接收但忽略。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        return Completion(text=self.text, usage=Usage(prompt_tokens=7, completion_tokens=3))


def _keep_two(item: KnowledgeItem) -> bool:
    return item.concept in {"闭包", "事件循环"}


async def test_happy_path_only_approved_items_enter_store() -> None:
    emitter, events, trace = _harness()
    store = LearningStore()

    result = await ingest_resource(
        _URL,
        source=lambda _url: "React hooks 深读材料",
        provider=_FixedProvider(_READER_JSON),
        store=store,
        approval=ScriptedApprovalGate(keep=_keep_two),
        emitter=emitter,
        max_bytes=4096,
        allowed_domains=_ALLOWED,
    )

    # 事件流：资源建档 → 注入中和 hook → model span → 候选 → 审批 → 通过 → K×入库，包在 ingest 内。
    assert [e.type for e in events] == [
        _INGEST_STARTED,
        LearningEvent.RESOURCE_CREATED,
        LearningEvent.RESOURCE_READ,
        EventType.HOOK_INVOKED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.ITEMS_EXTRACTED,
        APPROVAL_REQUESTED,
        LearningEvent.RESOURCE_APPROVED,
        LearningEvent.ITEM_CREATED,
        LearningEvent.ITEM_CREATED,
        _INGEST_ENDED,
    ]

    # eval case 1：Reader 出 3 个候选，审批只放 2 个，store 里就只有这 2 个（未审批不入库）。
    assert result.status == "read"
    stored = store.items_for_resource(result.resource_id)
    assert [i.concept for i in stored] == ["闭包", "事件循环"]
    assert len(result.items) == 2
    # 审批预览事件确实含全部 3 个候选（审批发生在入库前）。
    extracted = next(e for e in events if e.type == LearningEvent.ITEMS_EXTRACTED)
    assert len(extracted.payload["candidates"]) == 3
    # 资源持久化了原始内容 + hash、status=read、仍不可信。
    resource = store.get_resource(result.resource_id)
    assert resource is not None
    assert resource.status == "read"
    assert resource.raw_content == "React hooks 深读材料"
    assert resource.content_hash is not None
    assert resource.trusted is False
    # GKB-S3：Reader 抽出的资源级 topic 落库到 resources.topic（深读成功才产）。
    assert resource.topic == "React Hooks 与 JS 运行时"

    # span 树：ingest 为根，Reader 的 model span 挂其下；领域事件是点事件（不进树）。
    roots = trace.span_tree("run")
    assert len(roots) == 1
    assert roots[0].type == "ingest"
    assert [c.type for c in roots[0].children] == ["model"]
    # 点事件（span_id=None，被 build_span_tree 跳过、不进树），单独断言其 parent 链。
    ingest_root = roots[0].span_id
    for etype in (
        LearningEvent.RESOURCE_CREATED,
        LearningEvent.RESOURCE_READ,
        LearningEvent.ITEM_CREATED,
    ):
        point = next(e for e in events if e.type == etype)
        assert point.span_id is None
        assert point.parent_span_id == ingest_root
    trace.close()


async def test_fetch_failure_marks_resource_failed_and_produces_no_ghost_items() -> None:
    emitter, events, trace = _harness()
    store = LearningStore()

    def _boom(_url: str) -> str:
        raise RuntimeError("抓取超时")

    provider = _FixedProvider(_READER_JSON)
    result = await ingest_resource(
        _URL,
        source=_boom,
        provider=provider,
        store=store,
        approval=ScriptedApprovalGate(keep=_keep_two),
        emitter=emitter,
        max_bytes=4096,
        allowed_domains=_ALLOWED,
    )

    # 失败分支不 raise（部分失败不炸整条流），返回 failed、无 item。
    assert result.status == "failed"
    assert result.items == []
    # 事件流：建档后直接失败，无 model span、无 item_created。
    assert [e.type for e in events] == [
        _INGEST_STARTED,
        LearningEvent.RESOURCE_CREATED,
        LearningEvent.RESOURCE_FETCH_FAILED,
        _INGEST_ENDED,
    ]
    assert EventType.MODEL_STARTED not in {e.type for e in events}
    assert LearningEvent.ITEM_CREATED not in {e.type for e in events}
    assert provider.calls == 0  # fetch 失败，深读根本没发生

    # eval case 7：资源标记 failed，该资源 0 个 item（无幽灵 item）。
    resource = store.get_resource(result.resource_id)
    assert resource is not None
    assert resource.status == "failed"
    # GKB-S3：深读失败的资源不产 topic（保持 None，深读成功才写 resources.topic）。
    assert resource.topic is None
    assert store.items_for_resource(result.resource_id) == []
    trace.close()


async def _run_once(provider: Provider, emitter: EventEmitter) -> None:
    await ingest_resource(
        _URL,
        source=lambda _url: "React hooks 深读材料",
        provider=provider,
        store=LearningStore(),
        approval=ScriptedApprovalGate(keep=_keep_two),
        emitter=emitter,
        max_bytes=4096,
        allowed_domains=_ALLOWED,
    )


async def test_whole_ingest_slice_is_deterministic_under_replay(tmp_path: Path) -> None:
    cassette_path = tmp_path / "cassette.json"

    # Pass 1：录制——RecordingProvider 包一个计数 inner。
    class _Inner:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(
            self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
        ) -> Completion:
            self.calls += 1
            return Completion(text=_READER_JSON, usage=Usage(prompt_tokens=7, completion_tokens=3))

    inner = _Inner()
    cassette = Cassette()
    emitter1, events1, trace1 = _harness()
    await _run_once(RecordingProvider(inner, cassette, _MODELS), emitter1)
    cassette.save(cassette_path)
    tree1 = trace1.span_tree("run")
    assert inner.calls == 1

    # Pass 2：回放——ReplayProvider + 重置 ManualClock + 相同输入。
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
    # 回放没有再触碰 inner（第二遍 0 调用，证明整条竖切在 replay 下确定、不烧 token）。
    assert inner.calls == 1
    trace1.close()
    trace2.close()


async def test_replay_miss_propagates_loudly() -> None:
    # 空 cassette 回放：ReplayMiss（cassette 缺录 = harness bug）必须大声冒泡，不被吞成 failed——
    # 否则 eval 配置错误被静默掩盖。这也让"第二遍 0 调用"非空洞（证明响应确来自 cassette）。
    emitter, _events, trace = _harness()
    replay = ReplayProvider(Cassette(), _MODELS)
    with pytest.raises(ReplayMiss):
        await _run_once(replay, emitter)
    trace.close()


async def test_provider_exception_propagates_and_closes_ingest_span() -> None:
    # provider 基础设施异常（网络/超时/5xx）不是领域失败：闭合 ingest + model span 后原样冒泡，
    # 不吞成 failed（掩盖故障）、不留悬空 span、不产幽灵 item。
    emitter, events, trace = _harness()
    store = LearningStore()

    class _Raising:
        async def complete(
            self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
        ) -> Completion:
            raise RuntimeError("网络超时")

    with pytest.raises(RuntimeError):
        await ingest_resource(
            _URL,
            source=lambda _url: "React hooks 深读材料",
            provider=_Raising(),
            store=store,
            approval=ScriptedApprovalGate(keep=_keep_two),
            emitter=emitter,
            max_bytes=4096,
            allowed_domains=_ALLOWED,
        )

    # 事件以 INGEST_ENDED 收尾、含 MODEL_ENDED——ingest 与 model span 都闭合，无悬空。
    types = [e.type for e in events]
    assert types[-1] == _INGEST_ENDED
    assert EventType.MODEL_ENDED in types
    roots = trace.span_tree("run")
    assert len(roots) == 1
    assert roots[0].type == "ingest"
    assert roots[0].end_ts is not None
    assert [c.type for c in roots[0].children] == ["model"]
    assert roots[0].children[0].end_ts is not None
    # 无幽灵 item：该资源 0 个 item。
    created = next(e for e in events if e.type == LearningEvent.RESOURCE_CREATED)
    assert store.items_for_resource(created.payload["resource_id"]) == []
    trace.close()

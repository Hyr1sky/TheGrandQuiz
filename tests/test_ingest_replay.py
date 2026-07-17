"""端到端回放测试——用真实录制的 cassette 逐字节回放整条 ingest 竖切，零 token、无网络。

这是"eval 不烧 token、完全确定"的具体兑现：cassette 由 `scripts/record_ingest.py` 对真实
deepseek 录制（材料 `tests/materials/eval_paper.txt`），此处用 `ReplayProvider` 重放。
若有人改了 `prompts/reader_extract.md` 或材料，messages 变 → replay_key 变 → ReplayMiss，
本测试会红——正是"prompt 漂移需重录"的信号（golden fixture 的预期维护流）。
"""

import json
from pathlib import Path
from typing import cast

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Role
from grandquiz.providers.replay import Cassette, ReplayProvider

_MATERIAL = Path("tests/materials/eval_paper.txt")
_CASSETTE = Path("tests/fixtures/reader_extract.cassette.json")
_EXPECTED_ITEMS = 12  # golden：改 prompt / 材料并真实重录后同步更新


def _keep_all(_item: KnowledgeItem) -> bool:
    return True


async def test_recorded_ingest_replays_deterministically_without_live_calls() -> None:
    content = _MATERIAL.read_text(encoding="utf-8")
    # 从 cassette 复原 role→model 映射（录制时用的真实模型），使 replay_key 对齐、无需 .env。
    raw: dict[str, dict[str, str]] = json.loads(_CASSETTE.read_text(encoding="utf-8"))
    model_for_role = cast("dict[Role, str]", {e["role"]: e["model"] for e in raw.values()})
    replay = ReplayProvider(Cassette.load(_CASSETTE), model_for_role)
    store = LearningStore()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="replay")

    result = await ingest_resource(
        "https://example.com/sample",
        source=lambda _url: content,
        provider=replay,  # 纯回放：命中即返回、未命中 ReplayMiss；绝不触网、不烧 token
        store=store,
        approval=ScriptedApprovalGate(keep=_keep_all),
        emitter=emitter,
        max_bytes=1_000_000,
        allowed_domains={"example.com"},
    )

    assert result.status == "read"
    assert len(result.items) == _EXPECTED_ITEMS
    batch_events = [event for event in events if event.type == LearningEvent.READER_BATCH_STARTED]
    seen_node_ids = [node_id for event in batch_events for node_id in event.payload["node_ids"]]
    assessable_node_ids = [
        node.node_id
        for node in store.document_nodes(result.resource_id)
        if node.kind not in {"document", "section"}
    ]
    assert seen_node_ids == assessable_node_ids
    assert len(seen_node_ids) == len(set(seen_node_ids))
    assert sum(event.type == EventType.MODEL_STARTED for event in events) == len(raw)
    # 每个入库 item 都 grounded：概念 / 摘要非空、至少一条非空证据引文
    for item in result.items:
        assert item.concept and item.summary
        assert item.evidence and all(evidence.quote for evidence in item.evidence)

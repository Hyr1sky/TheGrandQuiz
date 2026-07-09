"""端到端 wiring——证明 ingest / assess 调用方**零改动**即可换用 SQLite 实现（M7 走骨架台账 #2）。

同一 SqliteLearningStore / SqliteLearningMemory 喂给 ingest_resource 与 assess_once 两个编排：
ingest 把获批 item 落 SQLite → assess 从同一 SQLite store 选题、判卷、记账到 SQLite memory。
两个编排的调用点一字未改（只是形参类型从具体类放宽为 ``Store`` / ``Memory`` 协议），故此测试跑通
即证明"替换不改调用方"。其余 dict 版测试保持不动（快、无 I/O）。
"""

import json
from collections.abc import Sequence
from pathlib import Path

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.kernel.clock import ManualClock, new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Role, Usage

_ALLOWED = {"example.com"}
_URL = "https://example.com/react-hooks"
_QUOTE = "闭包捕获变量而非值"
_MC_CORRECT = "正确选项"
_MC_WRONG = "干扰项"

# Reader 深读输出：单个候选（审批放行），其证据引文 = _QUOTE（供 assess provider 回抽引用）。
_READER_JSON = json.dumps(
    {
        "candidates": [
            {
                "concept": "闭包",
                "summary": "闭包捕获的是变量引用",
                "evidence": [{"quote": _QUOTE}],
                "confidence": 0.9,
            }
        ]
    },
    ensure_ascii=False,
)


class _ReaderProvider:
    """Reader 槽用：恒返回固定候选 JSON。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        return Completion(text=_READER_JSON, usage=Usage(prompt_tokens=7, completion_tokens=3))


class _AssessProvider:
    """出题 / 判卷槽：enrich 出题（MC → 选择题 JSON），basic 判卷；从 prompt 回抽真实证据。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        text = "\n".join(m.content for m in messages)
        if role == "enrich" and "answer_index" in text:  # 选择题出题（正确项恒在下标 0）
            payload: dict[str, object] = {
                "question": "该知识点的核心是什么？",
                "options": [_MC_CORRECT, _MC_WRONG],
                "answer_index": 0,
                "cited_evidence": [_QUOTE],
            }
        elif role == "enrich":  # 开放 / 追问出题
            payload = {"question": "该知识点的核心是什么？", "cited_evidence": [_QUOTE]}
        else:  # basic 判卷
            payload = {"verdict": "错", "cited_evidence": [_QUOTE]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


def _harness(trace_id: str) -> tuple[EventEmitter, list[AgentEvent], TraceStore]:
    events: list[AgentEvent] = []
    trace = TraceStore(":memory:")
    sink = EventSink()
    sink.subscribe(events.append)
    sink.subscribe(trace.record)
    emitter = EventEmitter(sink, ManualClock(), trace_id=trace_id)
    return emitter, events, trace


async def test_ingest_then_assess_run_unchanged_on_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"
    store = SqliteLearningStore(db)
    memory = SqliteLearningMemory(db)

    # --- ingest：调用点与 dict 版逐字相同，store 换成 SqliteLearningStore ---
    emitter1, _events1, trace1 = _harness("ingest")
    ingest_result = await ingest_resource(
        _URL,
        source=lambda _url: "React hooks 深读材料",
        provider=_ReaderProvider(),
        store=store,
        approval=ScriptedApprovalGate(keep=lambda _item: True),
        emitter=emitter1,
        max_bytes=4096,
        allowed_domains=_ALLOWED,
    )
    trace1.close()

    assert ingest_result.status == "read"
    stored = store.all_items()
    assert [i.concept for i in stored] == ["闭包"]
    target_id = stored[0].item_id

    # --- assess：调用点与 dict 版逐字相同，store / memory 换成 SQLite 版 ---
    emitter2, _events2, trace2 = _harness("assess")
    assess_result = await assess_once(
        store=store,
        provider=_AssessProvider(),
        responder=ScriptedResponder(answer=_MC_WRONG),  # 选错 → 判错 → 入薄弱
        memory=memory,
        emitter=emitter2,
        rng=new_rng(42),
    )
    trace2.close()

    # 从 SQLite store 选到了刚 ingest 的 item；判错落账到 SQLite memory。
    assert assess_result.status == "judged"
    assert assess_result.item_id == target_id
    assert assess_result.verdict == "错"
    assert assess_result.weak_item_id == target_id
    # 薄弱账真的写进了 SqliteLearningMemory（经未改动的 assess_once 调用点）。
    assert memory.state_of(target_id) == "薄弱"
    assert memory.weak_item_ids() == {target_id}

    store.close()
    memory.close()

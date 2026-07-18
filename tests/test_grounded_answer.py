"""自然材料问答：selected search → bounded read → exact citation。"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.grounded_answer import (
    GroundedAnswerRequest,
    GroundedDocumentAnswer,
)
from grandquiz.domain.learning.models import LearningResource
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, ToolSpec, Usage


class _GroundedAnswerProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        assert role == "basic"
        assert tools is None
        assert "不可信" in messages[0].content
        assert "durable processor 失败必须阻断当前 turn" in messages[1].content
        return Completion(
            text=json.dumps(
                {
                    "answer": "承重处理器失败时必须阻断当前 turn。",
                    "citations": [
                        {
                            "node_key": "n0",
                            "quote": "durable processor 失败必须阻断当前 turn",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=320, completion_tokens=40),
        )


class _NoEvidenceProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        return Completion(
            text=json.dumps(
                {"answer": "材料中没有足够证据回答该问题。", "citations": []},
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=80, completion_tokens=20),
        )


class _AmbiguousQuoteProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        return Completion(
            text=json.dumps(
                {
                    "answer": "事件出现了。",
                    "citations": [{"node_key": "n0", "quote": "事件"}],
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=80, completion_tokens=20),
        )


class _AlwaysOverBudgetCounter:
    def count(self, text: str) -> int:
        return 10_000


async def test_grounded_answer_reads_only_candidates_and_returns_exact_citation(
    tmp_path: Path,
) -> None:
    decoys = [f"## 普通章节 {index}\n\n这是第 {index} 段常规说明。\n" for index in range(40)]
    quote = "durable processor 失败必须阻断当前 turn"
    target = f"## 承重事件\n\n{quote}，不能被 observer 隔离。\n"
    content = "# Agent Runtime\n\n" + "\n".join([*decoys[:20], target, *decoys[20:]])
    resource = LearningResource.create(url="https://example.com/grounded-answer").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Agent Runtime",
        }
    )
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [])
    provider = _GroundedAnswerProvider()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="grounded-answer")

    result = await GroundedDocumentAnswer(store=store, provider=provider).answer(
        GroundedAnswerRequest(
            query="durable processor",
            resource_ids=[resource.resource_id],
            max_candidates=3,
            max_read_chars=400,
        ),
        emitter=emitter,
    )

    assert result.status == "answered"
    assert result.answer == "承重处理器失败时必须阻断当前 turn。"
    assert len(result.citations) == 1
    assert result.citations[0].quote == quote
    revision = store.current_revision(resource.resource_id)
    assert revision is not None
    assert result.citations[0].revision_id == revision.revision_id
    assert result.metrics.model_calls == 1
    assert result.metrics.total_tokens == 360
    assert 0 < result.metrics.read_chars < len(content) // 4
    assert provider.calls == 1
    assert [event.type for event in events] == [
        LearningEvent.GROUNDED_ANSWER_STARTED,
        LearningEvent.DOCUMENT_NODES_SEARCHED,
        LearningEvent.DOCUMENT_NODE_READ,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.CITATION_RESOLVED,
        LearningEvent.GROUNDED_ANSWER_ENDED,
    ]
    store.close()


async def test_grounded_answer_rejects_invalid_scope_before_reading(tmp_path: Path) -> None:
    store = SqliteLearningStore(tmp_path / "learning.db")
    provider = _GroundedAnswerProvider()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="invalid-scope")

    result = await GroundedDocumentAnswer(store=store, provider=provider).answer(
        GroundedAnswerRequest(query="durable processor", resource_ids=["missing-resource"]),
        emitter=emitter,
    )

    assert result.status == "invalid_scope"
    assert result.citations == []
    assert result.metrics.read_chars == 0
    assert result.metrics.model_calls == 0
    assert provider.calls == 0
    assert [event.type for event in events] == [
        LearningEvent.GROUNDED_ANSWER_STARTED,
        LearningEvent.DOCUMENT_SEARCH_REJECTED,
        LearningEvent.GROUNDED_ANSWER_ENDED,
    ]
    store.close()


async def test_grounded_answer_relaxes_multi_phrase_query_within_exact_scope(
    tmp_path: Path,
) -> None:
    quote = "durable processor 失败必须阻断当前 turn"
    content = f"# Runtime\n\n## Events\n\n{quote}。\n"
    resource = LearningResource.create(url="https://example.com/query-relaxation").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Runtime",
        }
    )
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [])
    provider = _GroundedAnswerProvider()
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="query-relaxation")

    result = await GroundedDocumentAnswer(store=store, provider=provider).answer(
        GroundedAnswerRequest(
            query="事件总线 durable processor",
            resource_ids=[resource.resource_id],
            max_read_chars=200,
        ),
        emitter=emitter,
    )

    assert result.status == "answered"
    assert result.citations[0].quote == quote
    assert provider.calls == 1
    store.close()


async def test_grounded_answer_returns_no_evidence_without_retrying_or_citing(
    tmp_path: Path,
) -> None:
    content = "# Runtime\n\n## Events\n\n事件是信封。\n"
    resource = LearningResource.create(url="https://example.com/no-evidence").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Runtime",
        }
    )
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [])
    provider = _NoEvidenceProvider()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="no-evidence")

    result = await GroundedDocumentAnswer(store=store, provider=provider).answer(
        GroundedAnswerRequest(query="事件 信封", resource_ids=[resource.resource_id]),
        emitter=emitter,
        parent_span_id="tool-call",
    )

    assert result.status == "no_evidence"
    assert result.answer == "材料中没有足够证据回答该问题。"
    assert result.citations == []
    assert result.metrics.model_calls == 1
    assert provider.calls == 1
    assert LearningEvent.CITATION_REJECTED not in [event.type for event in events]
    assert events[-1].type == LearningEvent.GROUNDED_ANSWER_ENDED
    assert events[-1].parent_span_id == "tool-call"
    store.close()


async def test_grounded_answer_stops_before_model_when_prompt_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    content = "# Runtime\n\n## Events\n\n事件是信封。\n"
    resource = LearningResource.create(url="https://example.com/prompt-budget").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Runtime",
        }
    )
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [])
    provider = _GroundedAnswerProvider()
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="prompt-budget")

    result = await GroundedDocumentAnswer(
        store=store,
        provider=provider,
        token_counter=_AlwaysOverBudgetCounter(),
    ).answer(
        GroundedAnswerRequest(
            query="事件 信封",
            resource_ids=[resource.resource_id],
            max_prompt_tokens=256,
        ),
        emitter=emitter,
    )

    assert result.status == "budget_exhausted"
    assert result.citations == []
    assert result.metrics.model_calls == 0
    assert result.metrics.max_prompt_tokens == 10_000
    assert provider.calls == 0
    store.close()


async def test_grounded_answer_rejects_quote_repeated_in_the_read_window(tmp_path: Path) -> None:
    content = "# Runtime\n\n## Events\n\n事件驱动 trace，事件也驱动 hook。\n"
    resource = LearningResource.create(url="https://example.com/ambiguous-quote").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Runtime",
        }
    )
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [])
    provider = _AmbiguousQuoteProvider()
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="ambiguous-quote")

    result = await GroundedDocumentAnswer(store=store, provider=provider).answer(
        GroundedAnswerRequest(
            query="事件",
            resource_ids=[resource.resource_id],
            max_attempts=1,
        ),
        emitter=emitter,
    )

    assert result.status == "citation_rejected"
    assert result.citations == []
    assert result.metrics.model_calls == 1
    assert provider.calls == 1
    rejected = next(event for event in events if event.type == LearningEvent.CITATION_REJECTED)
    assert rejected.payload["classification"] == "structured_answer_invalid"
    store.close()

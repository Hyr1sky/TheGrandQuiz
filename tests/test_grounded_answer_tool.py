"""GroundedDocumentAnswer 的 ReAct 高层工具入口。"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from grandquiz.domain.learning.grounded_answer import GroundedAnswerResult
from grandquiz.domain.learning.models import LearningResource
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.tools.grounded_answer_tool import make_grounded_answer_tool
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.providers.base import Completion, Message, Role, ToolCall, ToolSpec, Usage


class _Provider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return Completion(
            text=json.dumps(
                {
                    "answer": "事件信封让 trace 复用同一条事件流。",
                    "citations": [{"node_key": "n0", "quote": "事件是信封"}],
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=100, completion_tokens=20),
        )


class _NaturalQuestionProvider:
    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        if tools is None:
            assert "untrusted_evidence_windows" in messages[1].content
            return Completion(
                text=json.dumps(
                    {
                        "answer": "事件以信封承载不透明 payload。",
                        "citations": [{"node_key": "n0", "quote": "事件是信封"}],
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(prompt_tokens=100, completion_tokens=20),
            )
        assert "answer_from_documents" in messages[0].content
        tool_results = [message for message in messages if message.role == "tool"]
        if not tool_results:
            user = next(message.content for message in messages if message.role == "user")
            assert "工具" not in user
            return Completion(
                text="",
                tool_calls=[
                    ToolCall(
                        id="grounded-1",
                        name="answer_from_documents",
                        arguments={
                            "query": "事件 信封",
                            "resource_ids": [self.resource_id],
                            "max_read_chars": 200,
                        },
                    )
                ],
                usage=Usage(prompt_tokens=90, completion_tokens=10),
            )
        grounded = GroundedAnswerResult.model_validate_json(tool_results[-1].content)
        assert grounded.status == "answered" and grounded.citations
        return Completion(
            text="事件以信封承载不透明 payload；出处：Runtime > Events（逐字：事件是信封）。",
            usage=Usage(prompt_tokens=100, completion_tokens=20),
        )


async def test_high_level_tool_reuses_grounded_answer_workflow(tmp_path: Path) -> None:
    content = "# Runtime\n\n## Events\n\n事件是信封，trace 复用同一事件流。\n"
    resource = LearningResource.create(url="https://example.com/runtime").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Runtime",
        }
    )
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [])
    registry = ToolRegistry()
    registry.register(make_grounded_answer_tool(store=store, provider=_Provider()))
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="grounded-tool")

    result = GroundedAnswerResult.model_validate_json(
        await registry.dispatch(
            "answer_from_documents",
            {
                "query": "事件 信封",
                "resource_ids": [resource.resource_id],
                "max_read_chars": 200,
            },
            ctx=ToolContext(emitter=emitter, parent_span_id="tool-call"),
        )
    )

    assert result.status == "answered"
    assert result.citations[0].quote == "事件是信封"
    assert result.metrics.model_calls == 1
    store.close()


async def test_natural_question_routes_through_one_grounded_tool_call(tmp_path: Path) -> None:
    content = "# Runtime\n\n## Events\n\n事件是信封，承载不透明 payload。\n"
    resource = LearningResource.create(url="https://example.com/natural-grounded").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Runtime",
        }
    )
    store = SqliteLearningStore(tmp_path / "learning.db")
    store.replace_snapshot(resource, [])
    provider = _NaturalQuestionProvider(resource.resource_id)
    registry = ToolRegistry()
    registry.register(make_grounded_answer_tool(store=store, provider=provider))
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="natural-grounded")
    prompt = load_prompt("react_system")
    runner = Runner(
        provider=provider,
        emitter=emitter,
        system_prompt=prompt.text,
        prompt_version=prompt.version,
        tools=registry,
        max_iterations=4,
    )

    answer = await runner.run_agent_turn("根据这份材料，事件为什么被称为信封？请给出出处。")

    model_ended = [event for event in events if event.type == EventType.MODEL_ENDED]
    tool_started = [event for event in events if event.type == EventType.TOOL_CALL_STARTED]
    citations = [event for event in events if event.type == "learning.citation_resolved"]
    assert "Runtime > Events" in answer
    assert provider.calls == 3
    assert len(model_ended) == 3
    assert len(tool_started) == 1
    assert tool_started[0].payload["tool_name"] == "answer_from_documents"
    assert len(citations) == 1
    assert citations[0].payload["source"] == "node_read"
    assert sum(event.payload["usage"]["total_tokens"] for event in model_ended) == 340
    store.close()

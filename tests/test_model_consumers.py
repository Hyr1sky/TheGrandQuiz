"""Real consumer wiring, with provider failures as offline transport results."""

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from grandquiz.domain.learning.assessment.grading import grade_answer
from grandquiz.domain.learning.assessment.question import (
    ExpectedPoint,
    QuestionSpec,
    generate_question,
)
from grandquiz.domain.learning.grounded_answer import GroundedAnswerRequest, GroundedDocumentAnswer
from grandquiz.domain.learning.ingest.reader import Reader
from grandquiz.domain.learning.judge import judge_distractor
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.summarizer import LLMSummarizer
from grandquiz.evals.quality import QualityJudge, QualityRequest
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.hooks import HookManager
from grandquiz.kernel.runner import Runner
from grandquiz.providers.base import Completion, Message, ToolSpec
from grandquiz.providers.failure import ProviderFailure, ProviderFailureCategory
from grandquiz.providers.models import ModelBindings, with_identity
from grandquiz.providers.profiles import ModelIdentity


class FailingModel:
    def __init__(self, error: ProviderFailure) -> None:
        self.error = error

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        raise self.error


@pytest.mark.parametrize("purpose", ["question_generation", "answer_grading", "distractor_review"])
async def test_consumers_use_their_bound_model_and_record_its_actual_identity(purpose: str) -> None:
    errors = [
        ProviderFailure(category=category, retryable=False)
        for category in (
            ProviderFailureCategory.AUTHENTICATION,
            ProviderFailureCategory.PERMISSION_DENIED,
            ProviderFailureCategory.QUOTA_EXHAUSTED,
        )
    ]
    purposes = ("question_generation", "answer_grading", "distractor_review")
    bindings = ModelBindings(
        tuple(
            (
                name,
                with_identity(
                    FailingModel(error),
                    ModelIdentity(
                        purpose=name,
                        selection_source="purpose_override",
                        configuration_fingerprint=str(index + 1) * 64,
                        policy_fingerprint="a" * 64,
                    ),
                ),
            )
            for index, (name, error) in enumerate(zip(purposes, errors, strict=True))
        )
    )
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="bound-model-test")
    item = KnowledgeItem.create(
        resource_id="res",
        concept="闭包",
        summary="捕获变量",
        confidence=0.9,
        evidence=[Evidence(quote="闭包捕获变量")],
    )
    with pytest.raises(ProviderFailure) as caught:
        if purpose == "question_generation":
            await generate_question(item, provider=bindings, emitter=emitter, parent_span_id=None)
        elif purpose == "answer_grading":
            question = QuestionSpec(
                question="捕获什么？",
                expected_points=[
                    ExpectedPoint(
                        point_id="p",
                        description="捕获变量",
                        cited_evidence="闭包捕获变量",
                    )
                ],
                reference_answer="变量",
                cited_evidence=["闭包捕获变量"],
            )
            await grade_answer(
                question, "变量", provider=bindings, emitter=emitter, parent_span_id=None
            )
        else:
            await judge_distractor(
                item, "捕获什么？", "变量", "值", provider=bindings, emitter=emitter
            )
    assert caught.value is errors[purposes.index(purpose)]
    started = [event for event in events if event.type == EventType.MODEL_STARTED]
    ended = [event for event in events if event.type == EventType.MODEL_ENDED]
    assert len(started) == len(ended) == 1
    assert started[0].span_id == ended[0].span_id
    expected_identity = bindings.identity_for(purpose)
    assert expected_identity is not None
    assert started[0].payload["model_identity"] == expected_identity.model_dump()
    assert "role" not in started[0].payload
    assert ended[0].payload["ok"] is False


@pytest.mark.parametrize(
    "purpose", ["chat", "material_reading", "grounded_answer", "summarization", "eval_quality"]
)
async def test_remaining_consumers_preserve_their_bound_identity(
    purpose: str, tmp_path: Path
) -> None:
    error = ProviderFailure(category=ProviderFailureCategory.AUTHENTICATION, retryable=False)
    identity = ModelIdentity(
        purpose=purpose,
        selection_source="default",
        configuration_fingerprint="1" * 64,
        policy_fingerprint="2" * 64,
    )
    bound = with_identity(FailingModel(error), identity)
    bindings = ModelBindings(((purpose, bound),))
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="consumer-test")
    content = "# 闭包\n\n闭包捕获变量。"
    resource = LearningResource.create(url="https://example.test/material").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "闭包",
        }
    )
    with pytest.raises(ProviderFailure) as caught:
        if purpose == "chat":
            await Runner(provider=bound, emitter=emitter).run_turn("hello")
        elif purpose == "material_reading":
            await Reader(hooks=HookManager()).read(
                resource,
                content,
                provider=bindings,
                emitter=emitter,
                parent_span_id=None,
            )
        elif purpose == "summarization":
            await LLMSummarizer(bindings, emitter).summarize(
                "", [Message(role="user", content="hi")]
            )
        elif purpose == "eval_quality":
            await QualityJudge(provider=bindings).evaluate(
                QualityRequest(
                    rubric_id="grounded_answer",
                    question="闭包？",
                    candidate="变量",
                    reference="变量",
                ),
                emitter=emitter,
            )
        else:
            store = SqliteLearningStore(tmp_path / "learning.db")
            try:
                store.replace_snapshot(resource, [])
                await GroundedDocumentAnswer(store=store, provider=bindings).answer(
                    GroundedAnswerRequest(query="闭包", resource_ids=[resource.resource_id]),
                    emitter=emitter,
                )
            finally:
                store.close()
    assert caught.value is error
    started = [event for event in events if event.type == EventType.MODEL_STARTED]
    assert len(started) == 1
    assert started[0].payload["model_identity"] == identity.model_dump()
    assert "role" not in started[0].payload

"""Tier-2 QualityJudge 的公共契约与校准行为。"""

import json
from collections.abc import Sequence

import pytest

from grandquiz.evals.quality import QualityJudge, QualityJudgeError, QualityRequest
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, Role, Usage


class _FixedProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self._text = json.dumps(payload, ensure_ascii=False)
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        self.calls += 1
        return Completion(
            text=self._text,
            usage=Usage(prompt_tokens=120, completion_tokens=30),
        )


class _SequenceProvider:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._texts = [json.dumps(payload, ensure_ascii=False) for payload in payloads]
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        text = self._texts[self.calls]
        self.calls += 1
        return Completion(text=text, usage=Usage(prompt_tokens=10, completion_tokens=5))


class _RawProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        return Completion(text=self._text, usage=Usage(prompt_tokens=10, completion_tokens=5))


class _RaisingProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        raise RuntimeError("judge provider unavailable")


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="quality"), events


async def test_single_json_fence_is_normalized_before_strict_verdict_validation() -> None:
    payload = {
        "rubric_id": "grounded_answer",
        "criteria": [
            {
                "criterion_id": criterion_id,
                "score": 4,
                "rationale": "逐字证据支持。",
                "candidate_evidence": "事件信封",
                "reference_evidence": "事件信封",
            }
            for criterion_id in (
                "semantic_support",
                "question_coverage",
                "learning_usefulness",
            )
        ],
        "overall_rationale": "回答可采用。",
    }
    provider = _RawProvider(f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```")
    emitter, _ = _emitter()

    result = await QualityJudge(provider=provider, max_attempts=1).evaluate(
        QualityRequest(
            rubric_id="grounded_answer",
            question="什么是事件信封？",
            candidate="事件信封",
            reference="事件信封",
        ),
        emitter=emitter,
    )

    assert result.passed is True


async def test_fully_supported_answer_passes_with_auditable_judge_events() -> None:
    candidate = "AgentEvent 是事件信封；trace 与 hook 复用同一事件流。"
    reference = (
        "AgentEvent 是包含 type、元数据和不透明 payload 的事件信封。"
        "trace、hook、流式输出与 eval replay 复用同一条事件流。"
    )
    provider = _FixedProvider(
        {
            "rubric_id": "grounded_answer",
            "criteria": [
                {
                    "criterion_id": "semantic_support",
                    "score": 4,
                    "rationale": "两项结论均有原文支持。",
                    "candidate_evidence": "AgentEvent 是事件信封",
                    "reference_evidence": (
                        "AgentEvent 是包含 type、元数据和不透明 payload 的事件信封"
                    ),
                },
                {
                    "criterion_id": "question_coverage",
                    "score": 4,
                    "rationale": "直接解释了信封及事件流作用。",
                    "candidate_evidence": "trace 与 hook 复用同一事件流",
                    "reference_evidence": "trace、hook、流式输出与 eval replay 复用同一条事件流",
                },
                {
                    "criterion_id": "learning_usefulness",
                    "score": 3,
                    "rationale": "回答简洁且保留了关键关系。",
                    "candidate_evidence": "AgentEvent 是事件信封",
                    "reference_evidence": "事件信封",
                },
            ],
            "overall_rationale": "回答被材料支持并覆盖问题。",
        }
    )
    emitter, events = _emitter()

    result = await QualityJudge(provider=provider).evaluate(
        QualityRequest(
            rubric_id="grounded_answer",
            question="为什么 AgentEvent 被称为事件信封？",
            candidate=candidate,
            reference=reference,
        ),
        emitter=emitter,
    )

    assert result.passed is True
    assert {item.criterion_id: item.score for item in result.criteria} == {
        "semantic_support": 4,
        "question_coverage": 4,
        "learning_usefulness": 3,
    }
    assert result.usage.total_tokens == 150
    assert result.prompt_version.startswith("quality_judge@")
    assert provider.calls == 1
    assert [event.type for event in events] == [
        "eval.quality_judge.started",
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        "eval.quality_judge.ended",
    ]
    assert events[-1].payload == {
        "ok": True,
        "rubric_id": "grounded_answer",
        "rubric_version": "grounded_answer@v2",
        "criterion_count": 3,
        "passed": True,
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


async def test_missing_criterion_is_retried_before_judge_can_pass() -> None:
    candidate = "事件信封让 trace 与 hook 复用同一条事件流。"
    reference = "trace 与 hook 复用同一条事件流。"
    valid_criteria = [
        {
            "criterion_id": criterion_id,
            "score": 3,
            "rationale": "达到要求。",
            "candidate_evidence": "trace 与 hook 复用同一条事件流",
            "reference_evidence": "trace 与 hook 复用同一条事件流",
        }
        for criterion_id in (
            "semantic_support",
            "question_coverage",
            "learning_usefulness",
        )
    ]
    provider = _SequenceProvider(
        [
            {
                "rubric_id": "grounded_answer",
                "criteria": valid_criteria[:2],
                "overall_rationale": "遗漏了一个维度。",
            },
            {
                "rubric_id": "grounded_answer",
                "criteria": valid_criteria,
                "overall_rationale": "所有维度都达到要求。",
            },
        ]
    )
    emitter, _ = _emitter()

    result = await QualityJudge(provider=provider, max_attempts=2).evaluate(
        QualityRequest(
            rubric_id="grounded_answer",
            question="事件信封有什么作用？",
            candidate=candidate,
            reference=reference,
        ),
        emitter=emitter,
    )

    assert result.passed is True
    assert provider.calls == 2
    assert result.usage.total_tokens == 30


async def test_model_cannot_self_report_a_pass_over_low_criterion_scores() -> None:
    candidate = "事件信封统一事件消费者。"
    reference = "事件信封包含 type、元数据和 payload。"
    provider = _FixedProvider(
        {
            "rubric_id": "grounded_answer",
            "passed": True,
            "criteria": [
                {
                    "criterion_id": criterion_id,
                    "score": 1,
                    "rationale": "没有达到要求。",
                    "candidate_evidence": "事件信封",
                    "reference_evidence": "事件信封",
                }
                for criterion_id in (
                    "semantic_support",
                    "question_coverage",
                    "learning_usefulness",
                )
            ],
            "overall_rationale": "模型声称通过，但代码不应采信。",
        }
    )
    emitter, _ = _emitter()

    result = await QualityJudge(provider=provider).evaluate(
        QualityRequest(
            rubric_id="grounded_answer",
            question="事件信封有什么作用？",
            candidate=candidate,
            reference=reference,
        ),
        emitter=emitter,
    )

    assert result.passed is False


async def test_unregistered_rubric_fails_before_calling_the_provider() -> None:
    provider = _FixedProvider({})
    emitter, events = _emitter()

    with pytest.raises(QualityJudgeError, match="未注册 rubric"):
        await QualityJudge(provider=provider).evaluate(
            QualityRequest(
                rubric_id="yaml_injected_rubric",
                question="问题",
                candidate="回答",
                reference="参考",
            ),
            emitter=emitter,
        )

    assert provider.calls == 0
    assert events == []


async def test_fabricated_audit_evidence_is_retried_before_judge_can_pass() -> None:
    candidate = "事件信封让 trace 与 hook 复用同一条事件流。"
    reference = "trace 与 hook 复用同一条事件流。"

    def criteria(candidate_evidence: str) -> list[dict[str, object]]:
        return [
            {
                "criterion_id": criterion_id,
                "score": 3,
                "rationale": "达到要求。",
                "candidate_evidence": candidate_evidence,
                "reference_evidence": "trace 与 hook 复用同一条事件流",
            }
            for criterion_id in (
                "semantic_support",
                "question_coverage",
                "learning_usefulness",
            )
        ]

    provider = _SequenceProvider(
        [
            {
                "rubric_id": "grounded_answer",
                "criteria": criteria("候选回答里不存在的依据"),
                "overall_rationale": "依据是伪造的。",
            },
            {
                "rubric_id": "grounded_answer",
                "criteria": criteria("trace 与 hook 复用同一条事件流"),
                "overall_rationale": "依据可审计。",
            },
        ]
    )
    emitter, _ = _emitter()

    result = await QualityJudge(provider=provider, max_attempts=2).evaluate(
        QualityRequest(
            rubric_id="grounded_answer",
            question="事件信封有什么作用？",
            candidate=candidate,
            reference=reference,
        ),
        emitter=emitter,
    )

    assert result.passed is True
    assert provider.calls == 2


async def test_invalid_judge_output_exhaustion_fails_closed_and_closes_span() -> None:
    invalid: dict[str, object] = {
        "rubric_id": "grounded_answer",
        "criteria": [
            {
                "criterion_id": criterion_id,
                "score": 4,
                "rationale": "声称优秀。",
                "candidate_evidence": "伪造 candidate 依据",
                "reference_evidence": "伪造 reference 依据",
            }
            for criterion_id in (
                "semantic_support",
                "question_coverage",
                "learning_usefulness",
            )
        ],
        "overall_rationale": "无法审计。",
    }
    provider = _SequenceProvider([invalid, invalid])
    emitter, events = _emitter()

    with pytest.raises(QualityJudgeError):
        await QualityJudge(provider=provider, max_attempts=2).evaluate(
            QualityRequest(
                rubric_id="grounded_answer",
                question="事件信封有什么作用？",
                candidate="事件信封统一事件消费者。",
                reference="事件信封包含 type、元数据和 payload。",
            ),
            emitter=emitter,
        )

    assert provider.calls == 2
    assert [event.type for event in events] == [
        "eval.quality_judge.started",
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        "eval.quality_judge.ended",
    ]
    assert events[-1].payload.keys() == {
        "ok",
        "rubric_id",
        "rubric_version",
        "criterion_count",
        "classification",
        "error_fingerprint",
        "usage",
    }
    assert events[-1].payload["ok"] is False
    assert events[-1].payload["classification"] == "structured_verdict_invalid"


async def test_provider_failure_closes_model_and_quality_spans_then_propagates() -> None:
    emitter, events = _emitter()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await QualityJudge(provider=_RaisingProvider()).evaluate(
            QualityRequest(
                rubric_id="grounded_answer",
                question="事件信封有什么作用？",
                candidate="事件信封统一事件消费者。",
                reference="事件信封包含 type、元数据和 payload。",
            ),
            emitter=emitter,
        )

    assert [event.type for event in events] == [
        "eval.quality_judge.started",
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        "eval.quality_judge.ended",
    ]
    assert events[-2].payload["ok"] is False
    assert events[-1].payload == {
        "ok": False,
        "rubric_id": "grounded_answer",
        "rubric_version": "grounded_answer@v2",
        "criterion_count": 3,
        "classification": "provider_error",
    }

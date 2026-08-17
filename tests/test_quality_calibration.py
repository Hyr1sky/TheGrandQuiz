"""Tier-2 judge 的人工标注 calibration gate。"""

import json
from collections import deque
from collections.abc import Sequence

from grandquiz.evals.quality import QualityJudge
from grandquiz.evals.quality_calibration import (
    CalibratedQualitySuite,
    QualityCalibrationError,
    load_calibration_samples,
    run_calibration,
)
from grandquiz.evals.quality_contracts import CalibrationSample, ScoreRange
from grandquiz.evals.quality_dataset import (
    compile_quality_calibration_pack,
    load_question_quality_development_gold,
)
from grandquiz.providers.base import Completion, Message, Role, Usage


class _FixedProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self._text = json.dumps(payload, ensure_ascii=False)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        return Completion(
            text=self._text,
            usage=Usage(prompt_tokens=40, completion_tokens=20),
        )


class _SequenceProvider:
    def __init__(self, payloads: Sequence[dict[str, object]]) -> None:
        self._payloads = deque(payloads)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        return Completion(
            text=json.dumps(self._payloads.popleft(), ensure_ascii=False),
            usage=Usage(prompt_tokens=40, completion_tokens=20),
        )


async def test_registered_but_unadjudicated_question_rubric_fails_before_judge_call() -> None:
    provider = _FixedProvider({})

    try:
        await CalibratedQualitySuite.create(
            provider=provider,
            rubric_id="question_quality",
        )
    except QualityCalibrationError as exc:
        assert "owner-adjudicated calibration pack" in str(exc)
    else:
        raise AssertionError("an uncalibrated rubric must fail closed")


async def test_owner_compiled_question_pack_can_calibrate_its_rubric() -> None:
    criteria = (
        "evidence_support",
        "demand_alignment",
        "answer_leakage",
        "response_design",
        "learning_usefulness",
    )
    provider = _FixedProvider(
        {
            "rubric_id": "question_quality",
            "criteria": [
                {
                    "criterion_id": criterion_id,
                    "score": 4,
                    "rationale": "与 owner 边界一致。",
                    "candidate_evidence": "QuestionSpec",
                    "reference_evidence": "AgentEvent",
                }
                for criterion_id in criteria
            ],
            "overall_rationale": "全部边界满足。",
        }
    )
    boundaries = (
        ("good", "multiple_choice"),
        ("partial", "open_response"),
        ("leaked", "multiple_choice"),
        ("unsupported", "open_response"),
        ("misleading", "multiple_choice"),
    )
    calibration = compile_quality_calibration_pack(
        {
            "schema_version": "quality-calibration-pack.v1",
            "pack_id": "question-quality-development-gold-01",
            "rubric_id": "question_quality",
            "evidence_class": "development_gold",
            "label_status": "human_adjudicated",
            "annotator": "owner",
            "adjudicated_at": "2026-08-17",
            "blind_to_judge_output": True,
            "samples": [
                {
                    "sample_id": boundary,
                    "boundary": boundary,
                    "question_format": question_format,
                    "question": "Review this question.",
                    "candidate": "QuestionSpec",
                    "reference": "AgentEvent",
                    "expected_scores": {
                        criterion_id: {"min": 4, "max": 4} for criterion_id in criteria
                    },
                }
                for boundary, question_format in boundaries
            ],
        }
    )

    suite = await CalibratedQualitySuite.create(
        provider=provider,
        rubric_id="question_quality",
        calibration=calibration,
    )

    assert suite.calibration.passed is True
    assert suite.calibration.judge_tokens == 300
    assert suite.calibration.schema_version == "quality-calibration-report.v2"
    assert suite.calibration.rubric_id == "question_quality"
    assert suite.calibration.pack_id == calibration.pack_id
    assert suite.calibration.evidence_class == "development_gold"
    assert suite.calibration.pack_content_sha256 == calibration.content_sha256
    assert suite.calibration.prompt_versions[0].startswith("quality_judge@")
    assert suite.calibration.trace_id == "quality-calibration"


async def test_repository_question_quality_gold_runs_as_development_calibration() -> None:
    calibration = load_question_quality_development_gold()
    payloads: list[dict[str, object]] = []
    for sample in calibration.samples:
        payloads.append(
            {
                "rubric_id": "question_quality",
                "criteria": [
                    {
                        "criterion_id": criterion_id,
                        "score": score.min,
                        "rationale": "与 owner Development Gold 边界一致。",
                        "candidate_evidence": "learner_visible",
                        "reference_evidence": "AgentEvent",
                    }
                    for criterion_id, score in sample.expected_scores.items()
                ],
                "overall_rationale": "开发期校准边界满足。",
            }
        )

    suite = await CalibratedQualitySuite.create(
        provider=_SequenceProvider(payloads),
        rubric_id="question_quality",
        calibration=calibration,
    )

    assert suite.calibration.passed is True
    assert suite.calibration.evidence_class == "development_gold"
    assert suite.calibration.pack_content_sha256 == calibration.content_sha256
    assert [result.sample_id for result in suite.calibration.results] == [
        "good-mc",
        "partial-open",
        "leaked-mc",
        "unsupported-open",
        "misleading-mc",
    ]


async def test_calibration_passes_when_every_human_score_range_agrees() -> None:
    candidate = "AgentEvent 是事件信封，并让 trace 与 hook 复用同一事件流。"
    reference = "AgentEvent 是事件信封。trace 与 hook 复用同一事件流。"
    provider = _FixedProvider(
        {
            "rubric_id": "grounded_answer",
            "criteria": [
                {
                    "criterion_id": criterion_id,
                    "score": score,
                    "rationale": "与人工标注一致。",
                    "candidate_evidence": "AgentEvent 是事件信封",
                    "reference_evidence": "AgentEvent 是事件信封",
                }
                for criterion_id, score in (
                    ("semantic_support", 4),
                    ("question_coverage", 4),
                    ("learning_usefulness", 3),
                )
            ],
            "overall_rationale": "回答质量达到要求。",
        }
    )
    sample = CalibrationSample(
        sample_id="fully-supported",
        rubric_id="grounded_answer",
        question="事件信封有什么作用？",
        candidate=candidate,
        reference=reference,
        expected_scores={
            "semantic_support": ScoreRange(min=4, max=4),
            "question_coverage": ScoreRange(min=3, max=4),
            "learning_usefulness": ScoreRange(min=3, max=4),
        },
    )

    report = await run_calibration([sample], judge=QualityJudge(provider=provider))

    assert report.passed is True
    assert report.agreement == 1.0
    assert report.exact_agreement == 1.0
    assert report.judge_tokens == 60
    assert report.results[0].sample_id == "fully-supported"
    assert report.results[0].failures == []
    assert report.events[0].type == "eval.quality_judge.started"
    assert report.events[-1].type == "eval.quality_judge.ended"


async def test_any_human_range_disagreement_fails_the_calibration_gate() -> None:
    provider = _FixedProvider(
        {
            "rubric_id": "grounded_answer",
            "criteria": [
                {
                    "criterion_id": criterion_id,
                    "score": 1,
                    "rationale": "没有达到人工边界。",
                    "candidate_evidence": "事件信封",
                    "reference_evidence": "事件信封",
                }
                for criterion_id in (
                    "semantic_support",
                    "question_coverage",
                    "learning_usefulness",
                )
            ],
            "overall_rationale": "不通过。",
        }
    )
    sample = CalibrationSample(
        sample_id="human-boundary",
        rubric_id="grounded_answer",
        question="事件信封有什么作用？",
        candidate="事件信封统一事件消费者。",
        reference="事件信封包含 type、元数据和 payload。",
        expected_scores={
            criterion_id: ScoreRange(min=4, max=4)
            for criterion_id in (
                "semantic_support",
                "question_coverage",
                "learning_usefulness",
            )
        },
    )

    report = await run_calibration([sample], judge=QualityJudge(provider=provider))

    assert report.passed is False
    assert report.agreement == 0.0
    assert report.exact_agreement == 0.0
    assert len(report.results[0].failures) == 3


def test_repository_calibration_set_covers_four_human_boundaries() -> None:
    samples = load_calibration_samples()

    assert [sample.sample_id for sample in samples] == [
        "fully-supported",
        "partially-supported",
        "unsupported-embellishment",
        "justified-refusal",
    ]
    expected_criteria = {
        "semantic_support",
        "question_coverage",
        "learning_usefulness",
    }
    assert all(set(sample.expected_scores) == expected_criteria for sample in samples)

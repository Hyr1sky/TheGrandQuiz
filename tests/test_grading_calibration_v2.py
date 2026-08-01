"""Production-grader calibration against explicitly labelled human samples."""

import json
from collections.abc import Sequence
from pathlib import Path

import yaml

from grandquiz.domain.learning.assessment.question import ExpectedPoint, QuestionSpec
from grandquiz.evals.grading_calibration import (
    GradingCalibrationPolicy,
    GradingCalibrationSample,
    load_grading_calibration_samples,
    run_grading_calibration,
)
from grandquiz.providers.base import Completion, Message, Role, Usage


class _SequenceProvider:
    def __init__(self, payloads: list[object]) -> None:
        self._payloads = list(payloads)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        del messages, role, tools
        payload = self._payloads.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return Completion(text=text, usage=Usage(prompt_tokens=80, completion_tokens=20))


def _question() -> QuestionSpec:
    return QuestionSpec(
        question="HTTP/1.0 默认如何处理连接？",
        expected_points=[
            ExpectedPoint(
                point_id="default_close",
                description="说明响应后默认关闭连接",
                cited_evidence="HTTP/1.0 默认在响应后关闭 TCP 连接。",
            ),
            ExpectedPoint(
                point_id="keep_alive",
                description="说明可协商 Keep-Alive",
                cited_evidence="客户端与服务端可通过 Keep-Alive 扩展复用连接。",
            ),
        ],
        reference_answer="默认关闭；双方支持时可以通过 Keep-Alive 扩展复用。",
        cited_evidence=[
            "HTTP/1.0 默认在响应后关闭 TCP 连接。",
            "客户端与服务端可通过 Keep-Alive 扩展复用连接。",
        ],
    )


def _sample(*, blind: bool = True) -> GradingCalibrationSample:
    return GradingCalibrationSample(
        sample_id="http10-connection",
        annotator="owner",
        blind_to_model_output=blind,
        question=_question(),
        learner_answer="默认短连接，请求响应完成后关闭；也可以协商 Keep-Alive。",
        human_verdict="对",
        human_matched_points=["default_close", "keep_alive"],
        human_missing_points=[],
    )


def _verdict(
    label: str,
    *,
    matched: list[str],
    missing: list[str],
    diagnosis: str,
) -> dict[str, object]:
    return {
        "verdict": label,
        "matched_points": matched,
        "missing_points": missing,
        "diagnosis": diagnosis,
        "reason": "逐点评判。",
        "cited_evidence": ["HTTP/1.0 默认在响应后关闭 TCP 连接。"],
    }


async def test_exact_blind_agreement_passes_and_reports_token_cost() -> None:
    provider = _SequenceProvider(
        [
            _verdict(
                "对",
                matched=["default_close", "keep_alive"],
                missing=[],
                diagnosis="complete",
            )
        ]
    )

    report = await run_grading_calibration(
        [_sample()],
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.status == "passed"
    assert report.eligible_sample_count == 1
    assert report.verdict_agreement == 1.0
    assert report.point_accuracy == 1.0
    assert report.serious_false_negative_count == 0
    assert report.total_tokens == 100
    assert report.eligible_total_tokens == 100
    assert report.exploratory_sample_count == 0
    assert report.exploratory_total_tokens == 0
    assert report.eligible_average_tokens == 100.0
    assert report.results[0].attempts == 1
    assert report.results[0].retries == 0


async def test_human_correct_model_wrong_is_a_serious_false_negative() -> None:
    provider = _SequenceProvider(
        [
            _verdict(
                "错",
                matched=[],
                missing=["default_close", "keep_alive"],
                diagnosis="wrong_focus",
            )
        ]
    )

    report = await run_grading_calibration(
        [_sample()],
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.status == "failed"
    assert report.serious_false_negative_count == 1
    assert report.verdict_agreement == 0.0
    assert report.point_accuracy == 0.0


async def test_non_blind_label_is_reported_but_cannot_open_the_gate() -> None:
    provider = _SequenceProvider(
        [
            _verdict(
                "对",
                matched=["default_close", "keep_alive"],
                missing=[],
                diagnosis="complete",
            )
        ]
    )

    report = await run_grading_calibration(
        [_sample(blind=False)],
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.status == "insufficient_evidence"
    assert report.sample_count == 1
    assert report.eligible_sample_count == 0
    assert report.exploratory_sample_count == 1
    assert report.eligible_total_tokens == 0
    assert report.exploratory_total_tokens == 100
    assert report.results[0].eligible is False


async def test_report_counts_schema_retry_and_all_model_tokens() -> None:
    provider = _SequenceProvider(
        [
            "not-json",
            _verdict(
                "对",
                matched=["default_close", "keep_alive"],
                missing=[],
                diagnosis="complete",
            ),
        ]
    )

    report = await run_grading_calibration(
        [_sample()],
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.status == "passed"
    assert report.results[0].attempts == 2
    assert report.results[0].retries == 1
    assert report.total_tokens == 200
    assert report.eligible_average_tokens == 200.0


def test_human_sample_yaml_round_trips_through_the_versioned_contract(tmp_path: Path) -> None:
    path = tmp_path / "grading-labels.yaml"
    path.write_text(
        yaml.safe_dump([_sample().model_dump(mode="json")], allow_unicode=True),
        encoding="utf-8",
    )

    loaded = load_grading_calibration_samples(path)

    assert loaded == [_sample()]

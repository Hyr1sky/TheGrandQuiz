"""Production-grader calibration against explicitly labelled human samples."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import yaml

from grandquiz.domain.learning.assessment.question import ExpectedPoint, QuestionSpec
from grandquiz.domain.learning.eval_inbox import DatasetSnapshotItemV1, DatasetSnapshotV1
from grandquiz.domain.learning.grading_samples import AnswerProvenance
from grandquiz.evals.grading_calibration import (
    CalibrationRunManifest,
    GradingCalibrationPolicy,
    GradingCalibrationReport,
    GradingCalibrationSample,
    load_grading_calibration_samples,
    run_grading_calibration,
    run_snapshot_grading_calibration,
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


def _sample(
    *,
    blind: bool = True,
    answer_provenance: AnswerProvenance = "unassisted_human",
    respondent_model: str | None = None,
) -> GradingCalibrationSample:
    return GradingCalibrationSample(
        sample_id="http10-connection",
        annotator="owner",
        blind_to_model_output=blind,
        answer_provenance=answer_provenance,
        respondent_model=respondent_model,
        question=_question(),
        learner_answer="默认短连接，请求响应完成后关闭；也可以协商 Keep-Alive。",
        human_verdict="对",
        human_matched_points=["default_close", "keep_alive"],
        human_missing_points=[],
    )


def test_model_authored_answer_never_opens_the_release_gate() -> None:
    sample = _sample(answer_provenance="model")

    assert sample.eligible is False


def _verdict(
    label: str,
    *,
    matched: list[str],
    missing: list[str],
    diagnosis: str,
) -> dict[str, object]:
    return {
        "verdict": label,
        "point_assessments": [
            {
                "point_id": point_id,
                "label": "matched" if point_id in matched else "missing",
                "answer_evidence_ids": ["v1e000_033"] if point_id in matched else [],
                "reason": "测试用逐点评判。",
            }
            for point_id in [*matched, *missing]
        ],
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
        run_manifest=CalibrationRunManifest(
            provider="scripted",
            model="fixed-verdict",
            thinking_mode="disabled",
            dataset_snapshot_id="snapshot-1",
            dataset_content_sha256="sha-1",
        ),
    )

    assert report.schema_version == "grading-calibration-report.v4"
    assert report.run_manifest.model == "fixed-verdict"
    assert report.run_manifest.prompt_version.startswith("answer_grade@")
    assert report.status == "passed"
    assert report.eligible_sample_count == 1
    assert report.verdict_agreement == 1.0
    assert report.point_accuracy == 1.0
    assert report.serious_false_negative_count == 0
    assert report.total_tokens == 100
    assert report.total_prompt_tokens == 80
    assert report.total_completion_tokens == 20
    assert report.eligible_total_tokens == 100
    assert report.exploratory_sample_count == 0
    assert report.exploratory_total_tokens == 0
    assert report.eligible_average_tokens == 100.0
    assert report.eligible_valid_output_count == 1
    assert report.eligible_invalid_output_count == 0
    assert report.eligible_valid_output_rate == 1.0
    assert report.results[0].attempts == 1
    assert report.results[0].retries == 0
    assert report.results[0].model_matched_points == ["default_close", "keep_alive"]
    assert report.results[0].model_missing_points == []
    assert report.results[0].model_diagnosis == "complete"
    assert report.results[0].model_reason == "逐点评判。"
    assert report.results[0].model_cited_evidence == ["HTTP/1.0 默认在响应后关闭 TCP 连接。"]
    assert [
        point.model_dump(mode="json") for point in report.results[0].model_point_assessments
    ] == [
        {
            "point_id": "default_close",
            "label": "matched",
            "answer_evidence_ids": ["v1e000_033"],
            "answer_evidence": "默认短连接，请求响应完成后关闭；也可以协商 Keep-Alive。",
            "reason": "测试用逐点评判。",
        },
        {
            "point_id": "keep_alive",
            "label": "matched",
            "answer_evidence_ids": ["v1e000_033"],
            "answer_evidence": "默认短连接，请求响应完成后关闭；也可以协商 Keep-Alive。",
            "reason": "测试用逐点评判。",
        },
    ]
    assert report.results[0].prompt_tokens == 80
    assert report.results[0].completion_tokens == 20
    assert report.results[0].latency_ms >= 0
    assert report.results[0].derived_verdict == "对"
    assert report.results[0].human_matched_points == ["default_close", "keep_alive"]
    assert report.results[0].human_missing_points == []
    assert report.results[0].output_valid is True
    assert report.results[0].failure_kind is None


async def test_claim_aware_report_records_its_prompt_and_nested_evidence() -> None:
    sample = _sample()
    question = sample.question.model_copy(
        update={
            "expected_points": [
                point.model_copy(update={"required_claims": [point.description]})
                for point in sample.question.expected_points
            ]
        }
    )
    sample = sample.model_copy(update={"question": question})
    answer_evidence_ids = ["v1e000_033"]
    provider = _SequenceProvider(
        [
            {
                "verdict": "对",
                "point_assessments": [
                    {
                        "point_id": point.point_id,
                        "label": "matched",
                        "answer_evidence_ids": [],
                        "claim_assessments": [
                            {
                                "claim_id": f"{point.point_id}.claim_1",
                                "label": "matched",
                                "answer_evidence_ids": answer_evidence_ids,
                                "reason": "答案支持该 claim。",
                            }
                        ],
                        "reason": "全部 claim 命中。",
                    }
                    for point in question.expected_points
                ],
                "diagnosis": "complete",
                "reason": "全部评分点命中。",
                "cited_evidence": ["HTTP/1.0 默认在响应后关闭 TCP 连接。"],
            }
        ]
    )

    report = await run_grading_calibration(
        [sample],
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.status == "passed"
    assert report.run_manifest.prompt_version.startswith("answer_grade_claims@")
    assessments = report.results[0].model_point_assessments
    assert [claim.claim_id for claim in assessments[0].claim_assessments] == [
        "default_close.claim_1"
    ]
    assert assessments[0].claim_assessments[0].answer_evidence == sample.learner_answer


async def test_report_separates_contract_validity_from_grading_quality() -> None:
    valid = _verdict(
        "对",
        matched=["default_close", "keep_alive"],
        missing=[],
        diagnosis="complete",
    )
    invalid = _verdict(
        "对",
        matched=["default_close", "keep_alive"],
        missing=[],
        diagnosis="complete",
    )
    point_assessments = cast("list[Any]", invalid["point_assessments"])
    first_assessment = cast("dict[str, object]", point_assessments[0])
    first_assessment["answer_evidence"] = "默认...关闭"
    provider = _SequenceProvider([valid, invalid, invalid, invalid])

    report = await run_grading_calibration(
        [_sample(), _sample().model_copy(update={"sample_id": "contract-invalid"})],
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.schema_version == "grading-calibration-report.v4"
    assert report.eligible_valid_output_count == 1
    assert report.eligible_invalid_output_count == 1
    assert report.eligible_valid_output_rate == 0.5
    assert report.verdict_agreement == 1.0
    assert report.point_accuracy == 1.0
    assert report.results[1].output_valid is False
    assert report.results[1].failure_kind == "grading_contract"
    assert report.results[1].model_verdict is None
    assert report.results[1].derived_verdict is None


def test_v2_report_without_point_assessments_remains_readable() -> None:
    """v3 增加审计证据，但不应让历史 v2 报告失读。"""
    payload = {
        "schema_version": "grading-calibration-report.v2",
        "run_manifest": CalibrationRunManifest(
            provider="scripted",
            model="legacy",
        ).model_dump(mode="json"),
        "policy": GradingCalibrationPolicy(min_eligible_samples=1).model_dump(mode="json"),
        "status": "passed",
        "sample_count": 1,
        "eligible_sample_count": 1,
        "exploratory_sample_count": 0,
        "verdict_agreement": 1.0,
        "point_accuracy": 1.0,
        "serious_false_negative_count": 0,
        "serious_false_positive_count": 0,
        "total_prompt_tokens": 80,
        "total_completion_tokens": 20,
        "total_tokens": 100,
        "eligible_total_tokens": 100,
        "exploratory_total_tokens": 0,
        "eligible_average_tokens": 100.0,
        "results": [
            {
                "sample_id": "legacy",
                "eligible": True,
                "human_verdict": "对",
                "human_matched_points": ["default_close", "keep_alive"],
                "human_missing_points": [],
                "model_verdict": "对",
                "derived_verdict": "对",
                "model_matched_points": ["default_close", "keep_alive"],
                "model_missing_points": [],
                "model_diagnosis": "complete",
                "model_reason": "legacy",
                "model_cited_evidence": ["原文"],
                "verdict_agreed": True,
                "point_correct_count": 2,
                "point_count": 2,
                "serious_false_negative": False,
                "serious_false_positive": False,
                "attempts": 1,
                "retries": 0,
                "prompt_tokens": 80,
                "completion_tokens": 20,
                "tokens": 100,
                "latency_ms": 1.0,
                "error": None,
            }
        ],
    }

    report = GradingCalibrationReport.model_validate(payload)

    assert report.schema_version == "grading-calibration-report.v2"
    assert report.results[0].model_point_assessments == []
    assert report.eligible_valid_output_rate is None

    v3_report = GradingCalibrationReport.model_validate(
        {**payload, "schema_version": "grading-calibration-report.v3"}
    )
    assert v3_report.schema_version == "grading-calibration-report.v3"
    assert v3_report.eligible_valid_output_rate is None


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


async def test_model_answer_provenance_is_auditable_but_exploratory() -> None:
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
        [_sample(answer_provenance="model", respondent_model="deepseek-v4-pro")],
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.status == "insufficient_evidence"
    assert report.results[0].eligible is False
    assert report.results[0].answer_provenance == "model"
    assert report.results[0].respondent_model == "deepseek-v4-pro"


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


async def test_snapshot_adapter_runs_only_release_gate_eligible_blind_samples() -> None:
    sample = _sample()
    snapshot = DatasetSnapshotV1(
        snapshot_id="snapshot-1",
        content_sha256="snapshot-1",
        candidate_count=1,
        eligible_blind_count=1,
        exploratory_count=0,
        items=(
            DatasetSnapshotItemV1(
                candidate_id="candidate-1",
                source_kind="blind_grading_label",
                payload_schema_version=sample.schema_version,
                payload_hash="payload-hash",
                payload=sample,
                release_gate_eligible=True,
                review_request_id="review-1",
                review_reason="privacy checked",
                reviewed_at=1.0,
            ),
        ),
        created_at=1.0,
    )
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

    report = await run_snapshot_grading_calibration(
        snapshot,
        provider=provider,
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert report.status == "passed"
    assert report.sample_count == snapshot.eligible_blind_count
    assert report.run_manifest.dataset_snapshot_id == "snapshot-1"


async def test_snapshot_adapter_can_run_a_named_pilot_subset() -> None:
    first = _sample()
    second = _sample().model_copy(update={"sample_id": "second"})
    items = tuple(
        DatasetSnapshotItemV1(
            candidate_id=f"candidate-{index}",
            source_kind="blind_grading_label",
            payload_schema_version=sample.schema_version,
            payload_hash=f"payload-{index}",
            payload=sample,
            release_gate_eligible=True,
            review_request_id=f"review-{index}",
            review_reason="privacy checked",
            reviewed_at=1.0,
        )
        for index, sample in enumerate((first, second), start=1)
    )
    snapshot = DatasetSnapshotV1(
        snapshot_id="snapshot-pilot",
        content_sha256="snapshot-pilot",
        candidate_count=2,
        eligible_blind_count=2,
        exploratory_count=0,
        items=items,
        created_at=1.0,
    )
    provider = _SequenceProvider(
        [_verdict("对", matched=["default_close", "keep_alive"], missing=[], diagnosis="complete")]
    )

    report = await run_snapshot_grading_calibration(
        snapshot,
        provider=provider,
        sample_ids=["second"],
        policy=GradingCalibrationPolicy(min_eligible_samples=1),
    )

    assert [result.sample_id for result in report.results] == ["second"]
    assert report.run_manifest.sample_ids == ("second",)

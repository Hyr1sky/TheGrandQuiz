"""Explicit privacy review is required before local Eval dataset promotion."""

from pathlib import Path

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.question import ExpectedPoint, QuestionSpec
from grandquiz.domain.learning.eval_candidates import GradingEvalCandidateV1
from grandquiz.domain.learning.eval_inbox import EvalInboxConflict, eligible_grading_samples
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.evals.grading_calibration import GradingCalibrationSample
from grandquiz.kernel.clock import ManualClock


def _correction(candidate_id: str, *, verdict: VerdictLabel = "对") -> GradingEvalCandidateV1:
    return GradingEvalCandidateV1(
        candidate_id=candidate_id,
        attempt_id="attempt-1",
        item_id="item-1",
        source_trace_id="trace-assessment",
        correction_trace_id=f"trace-{candidate_id}",
        question_text="HTTP/1.0 默认如何处理连接？",
        answer_text="响应后关闭，也可协商 Keep-Alive。",
        question_format="open_response",
        grading_version="answer-grade.v2",
        model_verdict="错",
        human_verdict=verdict,
        correction_reason="覆盖了两个评分点",
        label_kind="overturned",
    )


def _blind(sample_id: str = "blind-1") -> GradingCalibrationSample:
    question = QuestionSpec(
        question="HTTP/1.0 默认如何处理连接？",
        expected_points=[
            ExpectedPoint(
                point_id="close",
                description="默认关闭",
                cited_evidence="HTTP/1.0 默认在响应后关闭连接。",
            )
        ],
        reference_answer="默认关闭连接。",
        cited_evidence=["HTTP/1.0 默认在响应后关闭连接。"],
    )
    return GradingCalibrationSample(
        sample_id=sample_id,
        annotator="owner",
        blind_to_model_output=True,
        question=question,
        learner_answer="响应后默认关闭。",
        human_verdict="对",
        human_matched_points=["close"],
        human_missing_points=[],
    )


def test_eval_inbox_supersedes_corrections_and_persists_reviews(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"
    clock = ManualClock(start=100.0)

    with LearningPersistence(db, clock=clock) as persistence:
        inbox = persistence.eval_inbox
        first = inbox.sync_corrections([_correction("correction-1")], now=clock.now())[0]
        second = inbox.sync_corrections(
            [_correction("correction-2", verdict="勉强")], now=clock.now()
        )[0]

        assert inbox.require(first.candidate_id).lifecycle_status == "superseded"
        assert second.lifecycle_status == "active"
        assert second.review_status == "pending"

        approved = inbox.review(
            second.candidate_id,
            request_id="review-correction",
            decision="approved",
            reason="已检查无个人信息",
            now=clock.now(),
        )
        replayed = inbox.review(
            second.candidate_id,
            request_id="review-correction",
            decision="approved",
            reason="已检查无个人信息",
            now=clock.now(),
        )
        assert approved == replayed

    with LearningPersistence(db) as reopened:
        restored = reopened.eval_inbox.require(second.candidate_id)

    assert restored.review_status == "approved"
    assert restored.release_gate_eligible is False


def test_dataset_snapshot_requires_approved_active_candidates_and_is_immutable(
    tmp_path: Path,
) -> None:
    clock = ManualClock(start=200.0)
    with LearningPersistence(tmp_path / "learning.db", clock=clock) as persistence:
        inbox = persistence.eval_inbox
        correction = inbox.sync_corrections([_correction("correction-1")], now=clock.now())[0]
        blind = inbox.import_blind_labels([_blind()], request_id="import-blind-1", now=clock.now())[
            0
        ]
        assert "blind-1" not in blind.candidate_id

        try:
            inbox.build_snapshot([correction.candidate_id, blind.candidate_id], now=clock.now())
        except EvalInboxConflict as exc:
            assert "approved" in str(exc)
        else:
            raise AssertionError("pending candidates must not enter a snapshot")

        for candidate in (correction, blind):
            inbox.review(
                candidate.candidate_id,
                request_id=f"review-{candidate.candidate_id}",
                decision="approved",
                reason="local privacy review complete",
                now=clock.now(),
            )

        snapshot = inbox.build_snapshot(
            [blind.candidate_id, correction.candidate_id],
            now=clock.now(),
        )
        replayed = inbox.build_snapshot(
            [correction.candidate_id, blind.candidate_id],
            now=clock.now(),
        )

        assert snapshot == replayed
        assert snapshot.candidate_count == 2
        assert snapshot.eligible_blind_count == 1
        assert snapshot.exploratory_count == 1
        assert eligible_grading_samples(snapshot) == [_blind()]

        changed_blind = _blind().model_copy(update={"learner_answer": "后来修订的盲标答案。"})
        inbox.import_blind_labels(
            [changed_blind],
            request_id="import-blind-2",
            now=clock.now(),
        )

        assert inbox.require_snapshot(snapshot.snapshot_id) == snapshot


def test_eval_review_request_id_conflict_fails_closed(tmp_path: Path) -> None:
    clock = ManualClock()
    with LearningPersistence(tmp_path / "learning.db", clock=clock) as persistence:
        candidate = persistence.eval_inbox.import_blind_labels(
            [_blind()], request_id="import-review-candidate", now=clock.now()
        )[0]
        persistence.eval_inbox.review(
            candidate.candidate_id,
            request_id="review-1",
            decision="approved",
            reason="checked",
            now=clock.now(),
        )

        try:
            persistence.eval_inbox.review(
                candidate.candidate_id,
                request_id="review-1",
                decision="rejected",
                reason="changed",
                now=clock.now(),
            )
        except EvalInboxConflict:
            pass
        else:
            raise AssertionError("conflicting idempotency command must fail")


def test_blind_import_requires_a_new_request_to_change_payload(tmp_path: Path) -> None:
    clock = ManualClock()
    with LearningPersistence(tmp_path / "learning.db", clock=clock) as persistence:
        inbox = persistence.eval_inbox
        first = inbox.import_blind_labels([_blind()], request_id="import-1", now=clock.now())[0]
        changed = _blind().model_copy(update={"learner_answer": "changed answer"})

        try:
            inbox.import_blind_labels([changed], request_id="import-1", now=clock.now())
        except EvalInboxConflict:
            pass
        else:
            raise AssertionError("one import request must not authorize two payloads")

        imported = inbox.import_blind_labels([changed], request_id="import-2", now=clock.now())[0]
        assert imported.source_request_id == "import-2"

        restored = inbox.import_blind_labels([_blind()], request_id="import-3", now=clock.now())[0]
        assert restored.candidate_id != first.candidate_id

        same_payload = inbox.import_blind_labels(
            [_blind()], request_id="import-4", now=clock.now()
        )[0]
        assert same_payload.candidate_id == restored.candidate_id

        try:
            inbox.import_blind_labels(
                [_blind(), _blind("blind-2")],
                request_id="import-4",
                now=clock.now(),
            )
        except EvalInboxConflict:
            pass
        else:
            raise AssertionError("one request id must identify one complete import manifest")

        try:
            inbox.import_blind_labels(
                [_blind(), _blind()], request_id="import-duplicates", now=clock.now()
            )
        except EvalInboxConflict:
            pass
        else:
            raise AssertionError("one command cannot contain duplicate sample ids")


def test_snapshot_atomically_rejects_rejected_and_superseded_candidates(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    with LearningPersistence(tmp_path / "learning.db", clock=clock) as persistence:
        inbox = persistence.eval_inbox
        rejected = inbox.import_blind_labels(
            [_blind("rejected")], request_id="import-rejected", now=clock.now()
        )[0]
        inbox.review(
            rejected.candidate_id,
            request_id="reject-review",
            decision="rejected",
            reason="contains private material",
            now=clock.now(),
        )
        superseded = inbox.import_blind_labels(
            [_blind("superseded")], request_id="import-old", now=clock.now()
        )[0]
        changed = _blind("superseded").model_copy(update={"learner_answer": "new"})
        inbox.import_blind_labels([changed], request_id="import-new", now=clock.now())

        for candidate_id in (rejected.candidate_id, superseded.candidate_id):
            try:
                inbox.build_snapshot([candidate_id], now=clock.now())
            except EvalInboxConflict:
                pass
            else:
                raise AssertionError("unauthorized candidate must fail the whole snapshot")

        assert inbox.recent_snapshots() == []

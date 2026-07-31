"""Web Acquisition 持久管理态：候选审批必须可跨进程恢复。"""

from pathlib import Path

import pytest

from grandquiz.domain.learning.acquisition import (
    AcquisitionLedger,
    AcquisitionTransitionError,
)
from grandquiz.domain.learning.ingest import PreparedIngest
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence


def _prepared() -> PreparedIngest:
    resource = LearningResource.create(url="file://local/upload/demo.md").model_copy(
        update={
            "raw_content": "# Demo\n\n事件是系统脊柱。",
            "content_hash": "abc",
            "status": "read",
            "topic": "Agent Runtime",
        }
    )
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="事件脊柱",
        summary="事件统一承载 trace、SSE 与回放。",
        evidence=[Evidence(quote="事件是系统脊柱。")],
        confidence=0.92,
    )
    return PreparedIngest(
        resource=resource,
        candidates=[item],
        revision_id="revision-1",
        node_count=1,
        ingest_span_id="trace-1:s0",
    )


def test_needs_input_snapshot_survives_reopening_learning_database(tmp_path: Path) -> None:
    db_path = tmp_path / "learning.db"
    with LearningPersistence(db_path) as persistence:
        ledger = persistence.acquisitions
        created = ledger.create(
            run_id="run-1",
            trace_id="trace-1",
            kind="upload",
            locator="file://local/upload/demo.md",
            display_name="demo.md",
            request_payload={"content": "# Demo"},
            token_hash="token-hash",
            token_expires_at=200.0,
            now=100.0,
        )
        ledger.mark_running(created.run_id, now=101.0)
        ledger.mark_needs_input(created.run_id, prepared=_prepared(), now=102.0)

    with LearningPersistence(db_path) as persistence:
        recovered = persistence.acquisitions.require("run-1")

    assert recovered.status == "needs_input"
    assert recovered.prepared is not None
    assert recovered.prepared.resource.topic == "Agent Runtime"
    assert recovered.prepared.candidates[0].concept == "事件脊柱"


def test_approval_token_is_single_use_and_transition_is_atomic(tmp_path: Path) -> None:
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        ledger: AcquisitionLedger = persistence.acquisitions
        ledger.create(
            run_id="run-1",
            trace_id="trace-1",
            kind="url",
            locator="https://example.com/article",
            display_name="example.com/article",
            request_payload={},
            token_hash="token-hash",
            token_expires_at=200.0,
            now=100.0,
        )
        ledger.mark_running("run-1", now=101.0)
        ledger.mark_needs_input("run-1", prepared=_prepared(), now=102.0)

        consumed = ledger.consume_approval_token(
            "run-1",
            token_hash="token-hash",
            now=103.0,
        )

        assert consumed.token_used_at == 103.0
        with pytest.raises(AcquisitionTransitionError, match="已经使用"):
            ledger.consume_approval_token(
                "run-1",
                token_hash="token-hash",
                now=104.0,
            )


def test_failed_transition_rolls_back_when_failure_detail_write_fails(
    tmp_path: Path,
) -> None:
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        ledger = persistence.acquisitions
        ledger.create(
            run_id="run-atomic-failure",
            trace_id="trace-atomic-failure",
            kind="url",
            locator="https://example.com/article",
            display_name="example.com/article",
            request_payload={},
            token_hash="token-hash",
            token_expires_at=200.0,
            now=100.0,
        )
        ledger.mark_running("run-atomic-failure", now=101.0)
        persistence.transaction_owner.connection.execute(
            """
            CREATE TRIGGER reject_failure_detail
            BEFORE INSERT ON acquisition_run_failures
            BEGIN
              SELECT RAISE(ABORT, 'injected failure detail write error');
            END
            """
        )
        persistence.transaction_owner.connection.commit()

        with pytest.raises(Exception, match="injected failure detail write error"):
            ledger.mark_failed(
                "run-atomic-failure",
                code="processing_failed",
                stage="processing",
                message="材料处理失败",
                now=102.0,
            )

        recovered = ledger.require("run-atomic-failure")
        assert recovered.status == "running"
        assert recovered.error_code is None
        assert recovered.error_stage is None


def test_legacy_acquisition_failure_is_projected_to_current_finite_contract(
    tmp_path: Path,
) -> None:
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        ledger = persistence.acquisitions
        ledger.create(
            run_id="legacy-failure",
            trace_id="legacy-trace",
            kind="url",
            locator="https://example.com/article",
            display_name="example.com/article",
            request_payload={},
            token_hash="token-hash",
            token_expires_at=200.0,
            now=100.0,
        )
        persistence.transaction_owner.connection.execute(
            """
            UPDATE acquisition_runs
            SET status = 'failed', error_code = 'acquisition_failed',
                error_message = '材料读取或深读失败，请检查内容后重试'
            WHERE run_id = 'legacy-failure'
            """
        )
        persistence.transaction_owner.connection.commit()

        recovered = ledger.require("legacy-failure")

    assert recovered.error_code == "ingest_failed"
    assert recovered.error_stage == "reader"

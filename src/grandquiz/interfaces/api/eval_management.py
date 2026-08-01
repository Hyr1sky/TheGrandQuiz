"""Application service for privacy-reviewed Eval promotion commands."""

import uuid

from grandquiz.domain.learning.eval_candidates import project_grading_eval_candidates
from grandquiz.domain.learning.eval_inbox import (
    DatasetSnapshotV1,
    EvalInboxCandidateV1,
    EvalReviewStatus,
)
from grandquiz.domain.learning.grading_samples import GradingCalibrationSample
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.kernel.clock import Clock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.trace import TraceStore


class EvalManagementService:
    """Owns the command boundary; payload content never enters operational traces."""

    def __init__(
        self,
        *,
        persistence: LearningPersistence,
        trace_store: TraceStore,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._trace_store = trace_store
        self._clock = clock

    def candidates(self) -> list[EvalInboxCandidateV1]:
        return self._persistence.eval_inbox.active()

    def sync_corrections(self) -> list[EvalInboxCandidateV1]:
        facts = self._persistence.learning_facts.facts()
        synced = self._persistence.eval_inbox.sync_corrections(
            project_grading_eval_candidates(facts),
            now=self._clock.now(),
        )
        self._emitter().emit(
            "learning.eval_candidates.synchronized",
            payload={"source_kind": "verdict_correction", "candidate_count": len(synced)},
        )
        return self._persistence.eval_inbox.active()

    def import_blind_labels(
        self,
        samples: list[GradingCalibrationSample],
        *,
        request_id: str,
    ) -> list[EvalInboxCandidateV1]:
        imported = self._persistence.eval_inbox.import_blind_labels(
            samples,
            request_id=request_id,
            now=self._clock.now(),
        )
        self._emitter().emit(
            "learning.eval_candidates.imported",
            payload={
                "source_kind": "blind_grading_label",
                "candidate_count": len(imported),
                "eligible_count": sum(item.release_gate_eligible for item in imported),
            },
        )
        return imported

    def review(
        self,
        candidate_id: str,
        *,
        request_id: str,
        decision: EvalReviewStatus,
        reason: str,
    ) -> EvalInboxCandidateV1:
        before = self._persistence.eval_inbox.require(candidate_id)
        reviewed = self._persistence.eval_inbox.review(
            candidate_id,
            request_id=request_id,
            decision=decision,
            reason=reason,
            now=self._clock.now(),
        )
        if before.review_request_id != request_id.strip():
            self._emitter().emit(
                "learning.eval_candidate.reviewed",
                payload={"candidate_id": candidate_id, "decision": decision},
            )
        return reviewed

    def snapshot(self, candidate_ids: list[str]) -> DatasetSnapshotV1:
        snapshot = self._persistence.eval_inbox.build_snapshot(
            candidate_ids,
            now=self._clock.now(),
        )
        self._emitter().emit(
            "learning.eval_dataset.snapshot_resolved",
            payload={
                "snapshot_id": snapshot.snapshot_id,
                "candidate_count": snapshot.candidate_count,
                "eligible_blind_count": snapshot.eligible_blind_count,
                "exploratory_count": snapshot.exploratory_count,
            },
        )
        return snapshot

    def get_snapshot(self, snapshot_id: str) -> DatasetSnapshotV1:
        return self._persistence.eval_inbox.require_snapshot(snapshot_id)

    def snapshots(self, *, limit: int = 20) -> list[DatasetSnapshotV1]:
        return self._persistence.eval_inbox.recent_snapshots(limit=limit)

    def _emitter(self) -> EventEmitter:
        sink = EventSink()
        sink.register_durable(self._trace_store)
        return EventEmitter(sink, self._clock, trace_id=uuid.uuid4().hex)

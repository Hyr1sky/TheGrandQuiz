"""Application service for material discovery and its explicit Acquisition bridge."""

import uuid

from pydantic import BaseModel

from grandquiz.domain.learning.acquisition import AcquisitionTransitionError
from grandquiz.domain.learning.discovery import (
    MaterialCandidateV1,
    MaterialDiscoveryBatchV1,
    MaterialDiscoveryConflict,
    MaterialDiscoveryService,
    MaterialReviewStatus,
    MaterialSourcePolicyV1,
)
from grandquiz.domain.learning.ingest.web_search import SearchProvider
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.acquisitions import AcquisitionCreated, AcquisitionManager
from grandquiz.kernel.clock import Clock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.trace import TraceStore


class MaterialReviewResult(BaseModel):
    candidate: MaterialCandidateV1
    acquisition: AcquisitionCreated | None = None


class DiscoveryManager:
    def __init__(
        self,
        *,
        persistence: LearningPersistence,
        acquisitions: AcquisitionManager,
        search_provider: SearchProvider | None,
        trace_store: TraceStore,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._acquisitions = acquisitions
        self._trace_store = trace_store
        self._clock = clock
        self._service = MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=search_provider,
            clock=clock,
        )

    async def discover(
        self,
        topic: str,
        *,
        source_policy: MaterialSourcePolicyV1,
    ) -> MaterialDiscoveryBatchV1:
        trace_id = uuid.uuid4().hex
        emitter = EventEmitter(self._event_sink(), self._clock, trace_id=trace_id)
        return await self._service.discover(
            topic,
            source_policy=source_policy,
            emitter=emitter,
        )

    def get(self, batch_id: str) -> MaterialDiscoveryBatchV1 | None:
        try:
            return self._persistence.material_discoveries.require_batch(batch_id)
        except KeyError:
            return None

    def recent(self, *, limit: int = 20) -> list[MaterialDiscoveryBatchV1]:
        return self._persistence.material_discoveries.recent_batches(limit=limit)

    def review(
        self,
        candidate_id: str,
        *,
        request_id: str,
        decision: MaterialReviewStatus,
        reason: str | None,
        control_token: str | None,
    ) -> MaterialReviewResult:
        created: AcquisitionCreated | None = None
        before = self._persistence.material_discoveries.require_candidate(candidate_id)

        def launch(url: str) -> str:
            nonlocal created
            if control_token is None:
                raise MaterialDiscoveryConflict("approval requires a control token")
            created = self._acquisitions.reserve_url(url=url, control_token=control_token)
            return created.run_id

        candidate = self._service.review_material(
            candidate_id,
            request_id=request_id,
            decision=decision,
            reason=reason,
            launch_url=launch,
        )
        if decision == "approved" and created is None:
            if control_token is None or candidate.acquisition_run_id is None:
                raise MaterialDiscoveryConflict("approved candidate has no acquisition control")
            try:
                created = self._acquisitions.created_with_token(
                    candidate.acquisition_run_id,
                    control_token=control_token,
                )
            except AcquisitionTransitionError as exc:
                raise MaterialDiscoveryConflict("acquisition control token is invalid") from exc
        if created is not None:
            self._acquisitions.activate_reserved(created.run_id)
        if before.review_request_id != request_id.strip():
            emitter = self._resume_emitter(self._batch_trace_id(candidate.batch_id))
            emitter.emit(
                "learning.material_candidate.reviewed",
                payload={
                    "candidate_id": candidate.candidate_id,
                    "decision": decision,
                    "acquisition_run_id": candidate.acquisition_run_id,
                },
            )
        return MaterialReviewResult(candidate=candidate, acquisition=created)

    def _batch_trace_id(self, batch_id: str) -> str:
        return self._persistence.material_discoveries.require_batch(batch_id).trace_id

    def _event_sink(self) -> EventSink:
        sink = EventSink()
        sink.register_durable(self._trace_store)
        return sink

    def _resume_emitter(self, trace_id: str) -> EventEmitter:
        events = self._trace_store.events(trace_id)
        next_seq = max((event.seq for event in events), default=-1) + 1
        return EventEmitter(
            self._event_sink(),
            self._clock,
            trace_id=trace_id,
            initial_seq=next_seq,
        )

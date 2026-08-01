"""Human-approved material discovery stays read-only until Acquisition is authorized."""

from pathlib import Path

from grandquiz.domain.learning.discovery import (
    MaterialDiscoveryConflict,
    MaterialDiscoveryService,
    MaterialSourcePolicyV1,
)
from grandquiz.domain.learning.ingest.web_search import SearchError, SearchResult
from grandquiz.domain.learning.models import LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink


class _SearchProvider:
    adapter_name = "scripted"

    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    async def search(
        self,
        query: str,
        *,
        limit: int,
        domains: tuple[str, ...] = (),
    ) -> list[SearchResult]:
        self.calls.append((query, limit, domains))
        return self.results[:limit]


class _FailingSearchProvider:
    adapter_name = "scripted"

    async def search(
        self,
        query: str,
        *,
        limit: int,
        domains: tuple[str, ...] = (),
    ) -> list[SearchResult]:
        raise SearchError("timeout", "scripted timeout")


class _CrashingSearchProvider:
    adapter_name = "scripted"

    async def search(
        self,
        query: str,
        *,
        limit: int,
        domains: tuple[str, ...] = (),
    ) -> list[SearchResult]:
        raise RuntimeError("provider internals must not enter the public envelope")


def _result(
    rank: int,
    url: str,
    *,
    snippet: str = "足够长的候选摘要，用于在抓取正文前进行人工判断。",
) -> SearchResult:
    return SearchResult(
        title=f"Agent material {rank}",
        url=url,
        snippet=snippet,
        adapter="scripted",
        rank=rank,
    )


def _emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="trace-discovery"), events


async def test_discovery_persists_ranked_candidates_without_writing_the_library(
    tmp_path: Path,
) -> None:
    provider = _SearchProvider(
        [
            _result(1, "https://example.com/new#overview"),
            _result(2, "https://example.com/new"),
            _result(3, "https://example.com/agent"),
            _result(4, "https://example.com/thin", snippet="short"),
        ]
    )
    emitter, events = _emitter()
    db = tmp_path / "learning.db"

    with LearningPersistence(db, clock=ManualClock()) as persistence:
        persistence.store.add_resource(
            LearningResource.create(url="https://example.com/agent").model_copy(
                update={"raw_content": "already approved material", "status": "read"}
            )
        )
        service = MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=provider,
            clock=ManualClock(start=10.0),
        )
        batch = await service.discover(
            "  Agent   Memory  ",
            source_policy=MaterialSourcePolicyV1(limit=4, domains=("example.com",)),
            emitter=emitter,
        )

        assert persistence.store.all_items() == []
        assert len(persistence.store.all_resources()) == 1

    assert provider.calls == [("Agent Memory", 4, ("example.com",))]
    assert [candidate.provider_rank for candidate in batch.candidates] == [1, 2, 3, 4]
    assert batch.candidates[0].eligibility == "eligible"
    assert batch.candidates[1].eligibility == "duplicate_batch"
    assert batch.candidates[2].eligibility == "existing_resource"
    assert batch.candidates[2].duplicate_resource_id is not None
    assert batch.candidates[3].eligibility == "insufficient_preview"
    assert [event.type for event in events] == [
        "learning.material_discovery.started",
        "learning.material_discovery.ended",
    ]
    assert "Agent Memory" not in str([event.payload for event in events])
    assert "https://example.com" not in str([event.payload for event in events])

    with LearningPersistence(db) as reopened:
        restored = reopened.material_discoveries.require_batch(batch.batch_id)

    assert restored == batch


async def test_discovery_orders_eligible_results_by_provider_rank(tmp_path: Path) -> None:
    provider = _SearchProvider(
        [
            _result(4, "https://docs.example.com/four"),
            _result(2, "https://docs.example.com/two"),
        ]
    )
    emitter, _ = _emitter()

    with LearningPersistence(tmp_path / "learning.db") as persistence:
        batch = await MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=provider,
            clock=ManualClock(),
        ).discover(
            "RAG",
            source_policy=MaterialSourcePolicyV1(limit=5),
            emitter=emitter,
        )

    assert [candidate.provider_rank for candidate in batch.candidates] == [2, 4]
    assert all(candidate.eligibility == "eligible" for candidate in batch.candidates)


async def test_discovery_failure_is_persisted_and_trace_safe(tmp_path: Path) -> None:
    emitter, events = _emitter()
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        service = MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=_FailingSearchProvider(),
            clock=ManualClock(start=30.0),
        )
        try:
            await service.discover(
                "private query words",
                source_policy=MaterialSourcePolicyV1(),
                emitter=emitter,
            )
        except SearchError:
            pass
        else:
            raise AssertionError("provider failure must remain visible to the caller")

        restored = persistence.material_discoveries.recent_batches(limit=1)[0]

    assert restored.status == "failed"
    assert restored.error_code == "search_timeout"
    assert restored.candidates == ()
    assert [event.type for event in events] == [
        "learning.material_discovery.started",
        "error",
        "learning.material_discovery.ended",
    ]
    assert "private query words" not in str([event.payload for event in events])


async def test_unclassified_provider_failure_has_a_safe_terminal_batch(tmp_path: Path) -> None:
    emitter, events = _emitter()
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        service = MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=_CrashingSearchProvider(),
            clock=ManualClock(),
        )
        try:
            await service.discover(
                "Agent",
                source_policy=MaterialSourcePolicyV1(),
                emitter=emitter,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("provider bug must remain visible internally")
        failed = persistence.material_discoveries.recent_batches(limit=1)[0]

    assert failed.status == "failed"
    assert failed.error_code == "search_source_failure"
    assert "provider internals" not in (failed.error_message or "")
    assert events[-1].type == "learning.material_discovery.ended"


async def test_domain_policy_is_verified_independently_of_provider(tmp_path: Path) -> None:
    provider = _SearchProvider([_result(1, "https://outside.invalid/agent")])
    emitter, _ = _emitter()
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        batch = await MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=provider,
            clock=ManualClock(),
        ).discover(
            "Agent",
            source_policy=MaterialSourcePolicyV1(domains=("example.com",)),
            emitter=emitter,
        )

    assert batch.candidates == ()
    assert batch.candidate_count == 0


async def test_material_review_is_idempotent_and_approval_only_launches_acquisition_once(
    tmp_path: Path,
) -> None:
    provider = _SearchProvider([_result(1, "https://example.com/approved")])
    emitter, _ = _emitter()
    launched: list[str] = []

    with LearningPersistence(tmp_path / "learning.db") as persistence:
        service = MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=provider,
            clock=ManualClock(start=20.0),
        )
        batch = await service.discover(
            "Agent",
            source_policy=MaterialSourcePolicyV1(),
            emitter=emitter,
        )
        candidate_id = batch.candidates[0].candidate_id

        def launch(url: str) -> str:
            launched.append(url)
            return "acquisition-run-1"

        approved = service.review_material(
            candidate_id,
            request_id="review-1",
            decision="approved",
            reason="官方文档",
            launch_url=launch,
        )
        replayed = service.review_material(
            candidate_id,
            request_id="review-1",
            decision="approved",
            reason="官方文档",
            launch_url=launch,
        )

        assert approved == replayed
        assert approved.acquisition_run_id == "acquisition-run-1"
        assert persistence.store.all_resources() == []

        try:
            service.review_material(
                candidate_id,
                request_id="review-1",
                decision="rejected",
                reason="changed",
                launch_url=launch,
            )
        except MaterialDiscoveryConflict:
            pass
        else:
            raise AssertionError("same request id with a different decision must conflict")

    assert launched == ["https://example.com/approved"]


async def test_ineligible_candidate_cannot_be_approved(tmp_path: Path) -> None:
    provider = _SearchProvider([_result(1, "https://example.com/thin", snippet="short")])
    emitter, _ = _emitter()

    with LearningPersistence(tmp_path / "learning.db") as persistence:
        service = MaterialDiscoveryService(
            ledger=persistence.material_discoveries,
            store=persistence.store,
            provider=provider,
            clock=ManualClock(),
        )
        batch = await service.discover(
            "Agent",
            source_policy=MaterialSourcePolicyV1(),
            emitter=emitter,
        )

        try:
            service.review_material(
                batch.candidates[0].candidate_id,
                request_id="review-thin",
                decision="approved",
                reason=None,
                launch_url=lambda _url: "must-not-launch",
            )
        except MaterialDiscoveryConflict as exc:
            assert "ineligible" in str(exc)
        else:
            raise AssertionError("ineligible candidates must fail closed")

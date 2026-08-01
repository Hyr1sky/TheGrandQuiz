"""Persistent, human-approved material discovery without fetch or KB writes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from grandquiz.domain.learning.ingest.web_search import SearchError, SearchProvider
from grandquiz.domain.learning.models import derive_id
from grandquiz.domain.learning.persistence import DatabaseSource, LearningDatabase, database_from
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.clock import Clock
from grandquiz.kernel.events import EventEmitter, EventType

MaterialEligibility = Literal[
    "eligible",
    "duplicate_batch",
    "existing_resource",
    "insufficient_preview",
]
MaterialReviewStatus = Literal["pending", "approved", "rejected"]
MaterialQualityFlag = Literal[
    "has_title",
    "has_preview",
    "https",
    "domain_constrained",
    "new_to_library",
]


class MaterialSourcePolicyV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["material-source-policy.v1"] = "material-source-policy.v1"
    limit: int = Field(default=5, ge=1, le=10)
    domains: tuple[str, ...] = Field(default=(), max_length=10)

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            host = value.strip().casefold().rstrip(".")
            if not host or "/" in host or ":" in host:
                raise ValueError("domains must contain host names only")
            if host not in normalized:
                normalized.append(host)
        return tuple(normalized)


class MaterialCandidateV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["material-candidate.v1"] = "material-candidate.v1"
    candidate_id: str
    batch_id: str
    title: str
    url: str
    canonical_url: str
    snippet: str
    provider_adapter: str
    provider_rank: int = Field(ge=1)
    quality_flags: tuple[MaterialQualityFlag, ...]
    eligibility: MaterialEligibility
    duplicate_resource_id: str | None = None
    why: str
    review_status: MaterialReviewStatus = "pending"
    review_request_id: str | None = None
    review_reason: str | None = None
    reviewed_at: float | None = None
    acquisition_run_id: str | None = None


class MaterialDiscoveryBatchV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["material-discovery-batch.v1"] = "material-discovery-batch.v1"
    batch_id: str
    trace_id: str
    topic: str
    source_policy: MaterialSourcePolicyV1
    provider_adapter: str
    status: Literal["ready", "failed"]
    candidates: tuple[MaterialCandidateV1, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    created_at: float

    @computed_field
    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


class MaterialDiscoveryReader(Protocol):
    def require_batch(self, batch_id: str) -> MaterialDiscoveryBatchV1: ...


class MaterialDiscoveryConflict(RuntimeError):
    """A review command conflicts with eligibility, prior state, or idempotency."""


class MaterialDiscoveryLedger:
    """SQLite adapter for discovery batches and their reviewable candidates."""

    def __init__(self, db: DatabaseSource) -> None:
        self._db: LearningDatabase = database_from(db)
        self._owns_db = not isinstance(db, LearningDatabase)

    @property
    def transaction_owner(self) -> LearningDatabase:
        return self._db

    def save(self, batch: MaterialDiscoveryBatchV1) -> MaterialDiscoveryBatchV1:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO material_discovery_batches "
                "(batch_id, trace_id, topic, source_policy, provider_adapter, status, "
                "error_code, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch.batch_id,
                    batch.trace_id,
                    batch.topic,
                    batch.source_policy.model_dump_json(),
                    batch.provider_adapter,
                    batch.status,
                    batch.error_code,
                    batch.error_message,
                    batch.created_at,
                ),
            )
            conn.executemany(
                "INSERT INTO material_candidates "
                "(candidate_id, batch_id, title, url, canonical_url, snippet, provider_adapter, "
                "provider_rank, quality_flags, eligibility, duplicate_resource_id, why, "
                "review_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        candidate.candidate_id,
                        candidate.batch_id,
                        candidate.title,
                        candidate.url,
                        candidate.canonical_url,
                        candidate.snippet,
                        candidate.provider_adapter,
                        candidate.provider_rank,
                        json.dumps(candidate.quality_flags),
                        candidate.eligibility,
                        candidate.duplicate_resource_id,
                        candidate.why,
                        candidate.review_status,
                    )
                    for candidate in batch.candidates
                ],
            )
        return self.require_batch(batch.batch_id)

    def require_batch(self, batch_id: str) -> MaterialDiscoveryBatchV1:
        row = self._db.connection.execute(
            "SELECT batch_id, trace_id, topic, source_policy, provider_adapter, status, "
            "error_code, error_message, created_at FROM material_discovery_batches "
            "WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if row is None:
            raise KeyError(batch_id)
        candidate_rows = self._db.connection.execute(
            "SELECT candidate_id, batch_id, title, url, canonical_url, snippet, "
            "provider_adapter, provider_rank, quality_flags, eligibility, "
            "duplicate_resource_id, why, review_status, review_request_id, review_reason, "
            "reviewed_at, acquisition_run_id FROM material_candidates "
            "WHERE batch_id = ? ORDER BY provider_rank, candidate_id",
            (batch_id,),
        ).fetchall()
        return MaterialDiscoveryBatchV1.model_validate(
            {
                "batch_id": row[0],
                "trace_id": row[1],
                "topic": row[2],
                "source_policy": json.loads(str(row[3])),
                "provider_adapter": row[4],
                "status": row[5],
                "candidates": [_candidate_from_row(candidate) for candidate in candidate_rows],
                "error_code": row[6],
                "error_message": row[7],
                "created_at": row[8],
            }
        )

    def require_candidate(self, candidate_id: str) -> MaterialCandidateV1:
        row = self._db.connection.execute(
            "SELECT candidate_id, batch_id, title, url, canonical_url, snippet, "
            "provider_adapter, provider_rank, quality_flags, eligibility, "
            "duplicate_resource_id, why, review_status, review_request_id, review_reason, "
            "reviewed_at, acquisition_run_id FROM material_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return _candidate_from_row(row)

    def recent_batches(self, *, limit: int = 20) -> list[MaterialDiscoveryBatchV1]:
        rows = self._db.connection.execute(
            "SELECT batch_id FROM material_discovery_batches ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self.require_batch(str(row[0])) for row in rows]

    def mark_reviewed(
        self,
        candidate_id: str,
        *,
        request_id: str,
        decision: MaterialReviewStatus,
        reason: str | None,
        reviewed_at: float,
        acquisition_run_id: str | None,
    ) -> MaterialCandidateV1:
        self._db.connection.execute(
            "UPDATE material_candidates SET review_status = ?, review_request_id = ?, "
            "review_reason = ?, reviewed_at = ?, acquisition_run_id = ? WHERE candidate_id = ?",
            (
                decision,
                request_id,
                reason,
                reviewed_at,
                acquisition_run_id,
                candidate_id,
            ),
        )
        self._db.commit()
        return self.require_candidate(candidate_id)

    def close(self) -> None:
        if self._owns_db:
            self._db.close()


def canonicalize_public_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    port = parsed.port
    default_port = (parsed.scheme.casefold() == "https" and port == 443) or (
        parsed.scheme.casefold() == "http" and port == 80
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.casefold(), netloc, path, parsed.query, ""))


class MaterialDiscoveryService:
    """Deep module: Search -> deterministic shortlist; no fetch, Reader, or KB commit."""

    def __init__(
        self,
        *,
        ledger: MaterialDiscoveryLedger,
        store: Store,
        provider: SearchProvider | None,
        clock: Clock,
    ) -> None:
        self._ledger = ledger
        self._store = store
        self._provider = provider
        self._clock = clock

    async def discover(
        self,
        topic: str,
        *,
        source_policy: MaterialSourcePolicyV1,
        emitter: EventEmitter,
    ) -> MaterialDiscoveryBatchV1:
        normalized_topic = " ".join(topic.split())
        if not normalized_topic:
            raise ValueError("discovery topic must not be blank")
        batch_id = derive_id("material-discovery", emitter.trace_id)
        fingerprint = hashlib.sha256(normalized_topic.encode("utf-8")).hexdigest()
        adapter = self._provider.adapter_name if self._provider is not None else "unconfigured"
        emitter.emit(
            "learning.material_discovery.started",
            payload={
                "batch_id": batch_id,
                "topic_fingerprint": fingerprint,
                "limit": source_policy.limit,
                "domain_count": len(source_policy.domains),
                "adapter": adapter,
            },
        )
        if self._provider is None:
            self._save_failure(
                batch_id=batch_id,
                topic=normalized_topic,
                source_policy=source_policy,
                provider_adapter=adapter,
                error_code="provider_unavailable",
                error_message="search provider is not configured",
                emitter=emitter,
            )
            raise MaterialDiscoveryConflict("search provider is not configured")
        try:
            results = await self._provider.search(
                normalized_topic,
                limit=source_policy.limit,
                domains=source_policy.domains,
            )
        except SearchError as exc:
            self._save_failure(
                batch_id=batch_id,
                topic=normalized_topic,
                source_policy=source_policy,
                provider_adapter=adapter,
                error_code=f"search_{exc.reason}",
                error_message=str(exc),
                emitter=emitter,
            )
            raise
        except Exception:
            self._save_failure(
                batch_id=batch_id,
                topic=normalized_topic,
                source_policy=source_policy,
                provider_adapter=adapter,
                error_code="search_source_failure",
                error_message="search provider failed",
                emitter=emitter,
            )
            raise
        try:
            existing = {
                canonicalize_public_url(resource.url): resource.resource_id
                for resource in self._store.all_resources()
                if resource.url.startswith(("http://", "https://"))
            }
            seen: set[str] = set()
            candidates: list[MaterialCandidateV1] = []
            for result in results:
                if source_policy.domains and not _domain_allowed(result.url, source_policy.domains):
                    continue
                canonical = canonicalize_public_url(result.url)
                duplicate_resource_id = existing.get(canonical)
                if duplicate_resource_id is not None:
                    eligibility: MaterialEligibility = "existing_resource"
                    why = "知识库已包含同一 URL"
                elif canonical in seen:
                    eligibility = "duplicate_batch"
                    why = "本次搜索结果中已有同一 URL"
                elif len(result.snippet.strip()) < 20:
                    eligibility = "insufficient_preview"
                    why = "摘要信息不足，暂不建议抓取"
                else:
                    eligibility = "eligible"
                    why = f"搜索结果第 {result.rank} 位，尚未进入知识库"
                flags: list[MaterialQualityFlag] = []
                if result.title.strip():
                    flags.append("has_title")
                if len(result.snippet.strip()) >= 20:
                    flags.append("has_preview")
                if canonical.startswith("https://"):
                    flags.append("https")
                if source_policy.domains:
                    flags.append("domain_constrained")
                if duplicate_resource_id is None and canonical not in seen:
                    flags.append("new_to_library")
                candidates.append(
                    MaterialCandidateV1(
                        candidate_id=derive_id(batch_id, canonical, str(result.rank)),
                        batch_id=batch_id,
                        title=result.title,
                        url=result.url,
                        canonical_url=canonical,
                        snippet=result.snippet,
                        provider_adapter=result.adapter,
                        provider_rank=result.rank,
                        quality_flags=tuple(flags),
                        eligibility=eligibility,
                        duplicate_resource_id=duplicate_resource_id,
                        why=why,
                    )
                )
                seen.add(canonical)
            batch = self._ledger.save(
                MaterialDiscoveryBatchV1(
                    batch_id=batch_id,
                    trace_id=emitter.trace_id,
                    topic=normalized_topic,
                    source_policy=source_policy,
                    provider_adapter=self._provider.adapter_name,
                    status="ready",
                    candidates=tuple(candidates),
                    created_at=self._clock.now(),
                )
            )
        except Exception:
            self._save_failure(
                batch_id=batch_id,
                topic=normalized_topic,
                source_policy=source_policy,
                provider_adapter=adapter,
                error_code="search_source_failure",
                error_message="search provider returned unusable candidates",
                emitter=emitter,
            )
            raise
        emitter.emit(
            "learning.material_discovery.ended",
            payload={
                "batch_id": batch_id,
                "status": "ready",
                "candidate_count": len(candidates),
                "eligible_count": sum(c.eligibility == "eligible" for c in candidates),
            },
        )
        return batch

    def _save_failure(
        self,
        *,
        batch_id: str,
        topic: str,
        source_policy: MaterialSourcePolicyV1,
        provider_adapter: str,
        error_code: str,
        error_message: str,
        emitter: EventEmitter,
    ) -> None:
        self._ledger.save(
            MaterialDiscoveryBatchV1(
                batch_id=batch_id,
                trace_id=emitter.trace_id,
                topic=topic,
                source_policy=source_policy,
                provider_adapter=provider_adapter,
                status="failed",
                error_code=error_code,
                error_message=error_message,
                created_at=self._clock.now(),
            )
        )
        emitter.emit(
            EventType.ERROR,
            payload={
                "code": error_code,
                "operation": "material_discovery",
                "batch_id": batch_id,
            },
        )
        emitter.emit(
            "learning.material_discovery.ended",
            payload={"batch_id": batch_id, "status": "failed", "error_code": error_code},
        )

    def review_material(
        self,
        candidate_id: str,
        *,
        request_id: str,
        decision: MaterialReviewStatus,
        reason: str | None,
        launch_url: Callable[[str], str],
    ) -> MaterialCandidateV1:
        """Apply one human decision; approval authorizes, but does not replace, Acquisition."""

        if decision == "pending":
            raise ValueError("review decision must be approved or rejected")
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("request_id must not be blank")
        normalized_reason = None if reason is None else reason.strip() or None
        with self._ledger.transaction_owner.transaction():
            candidate = self._ledger.require_candidate(candidate_id)
            if candidate.review_request_id == normalized_request_id:
                if (
                    candidate.review_status == decision
                    and candidate.review_reason == normalized_reason
                ):
                    return candidate
                raise MaterialDiscoveryConflict("idempotency conflict")
            if candidate.review_status != "pending":
                raise MaterialDiscoveryConflict("candidate was already reviewed")
            if decision == "approved" and candidate.eligibility != "eligible":
                raise MaterialDiscoveryConflict("ineligible candidate cannot be approved")
            acquisition_run_id = launch_url(candidate.url) if decision == "approved" else None
            reviewed = self._ledger.mark_reviewed(
                candidate_id,
                request_id=normalized_request_id,
                decision=decision,
                reason=normalized_reason,
                reviewed_at=self._clock.now(),
                acquisition_run_id=acquisition_run_id,
            )
        return reviewed


def _domain_allowed(url: str, domains: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _candidate_from_row(row: tuple[object, ...]) -> MaterialCandidateV1:
    return MaterialCandidateV1.model_validate(
        {
            "candidate_id": row[0],
            "batch_id": row[1],
            "title": row[2],
            "url": row[3],
            "canonical_url": row[4],
            "snippet": row[5],
            "provider_adapter": row[6],
            "provider_rank": row[7],
            "quality_flags": json.loads(str(row[8])),
            "eligibility": row[9],
            "duplicate_resource_id": row[10],
            "why": row[11],
            "review_status": row[12],
            "review_request_id": row[13],
            "review_reason": row[14],
            "reviewed_at": row[15],
            "acquisition_run_id": row[16],
        }
    )

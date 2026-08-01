"""Local privacy review and immutable promotion of Eval candidates."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.eval_candidates import GradingEvalCandidateV1
from grandquiz.domain.learning.grading_samples import GradingCalibrationSample
from grandquiz.domain.learning.learning_facts import DEFAULT_REDACTION_PROFILE
from grandquiz.domain.learning.models import derive_id
from grandquiz.domain.learning.persistence import DatabaseSource, LearningDatabase, database_from

EvalSourceKind = Literal["verdict_correction", "blind_grading_label"]
EvalLifecycleStatus = Literal["active", "superseded"]
EvalReviewStatus = Literal["pending", "approved", "rejected"]
EvalPayload = GradingEvalCandidateV1 | GradingCalibrationSample


class EvalInboxConflict(RuntimeError):
    """A review or snapshot command violates the explicit authorization boundary."""


class EvalInboxCandidateV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["eval-inbox-candidate.v1"] = "eval-inbox-candidate.v1"
    candidate_id: str
    source_kind: EvalSourceKind
    dedupe_key: str
    source_request_id: str
    payload_schema_version: str
    payload_hash: str
    payload: EvalPayload
    lifecycle_status: EvalLifecycleStatus
    review_status: EvalReviewStatus
    release_gate_eligible: bool
    privacy_review_required: Literal[True] = True
    review_request_id: str | None = None
    review_reason: str | None = None
    reviewed_at: float | None = None
    created_at: float


class DatasetSnapshotItemV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    source_kind: EvalSourceKind
    payload_schema_version: str
    payload_hash: str
    payload: EvalPayload
    release_gate_eligible: bool
    review_request_id: str = Field(min_length=1)
    review_reason: str = Field(min_length=1)
    reviewed_at: float


class DatasetSnapshotV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["eval-dataset-snapshot.v1"] = "eval-dataset-snapshot.v1"
    snapshot_id: str
    content_sha256: str
    redaction_profile: str = DEFAULT_REDACTION_PROFILE
    candidate_count: int = Field(ge=0)
    eligible_blind_count: int = Field(ge=0)
    exploratory_count: int = Field(ge=0)
    items: tuple[DatasetSnapshotItemV1, ...]
    created_at: float


class EvalInboxLedger:
    """SQLite inbox; source facts remain authoritative and historical snapshots immutable."""

    def __init__(self, db: DatabaseSource) -> None:
        self._db: LearningDatabase = database_from(db)
        self._owns_db = not isinstance(db, LearningDatabase)

    def sync_corrections(
        self,
        candidates: list[GradingEvalCandidateV1],
        *,
        now: float,
    ) -> list[EvalInboxCandidateV1]:
        with self._db.transaction():
            return [
                self._upsert(
                    source_kind="verdict_correction",
                    dedupe_key=candidate.attempt_id,
                    source_request_id=f"correction:{candidate.candidate_id}",
                    payload=candidate,
                    release_gate_eligible=False,
                    now=now,
                )
                for candidate in sorted(candidates, key=lambda item: item.candidate_id)
            ]

    def import_blind_labels(
        self,
        samples: list[GradingCalibrationSample],
        *,
        request_id: str,
        now: float,
    ) -> list[EvalInboxCandidateV1]:
        normalized_request_id = request_id.strip()
        if not normalized_request_id:
            raise ValueError("request_id must not be blank")
        sample_ids = [sample.sample_id for sample in samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise EvalInboxConflict("one import command cannot repeat a sample_id")
        imported: list[EvalInboxCandidateV1] = []
        with self._db.transaction():
            incoming_manifest = sorted(
                (sample.sample_id, _payload_hash(sample)) for sample in samples
            )
            manifest_hash = hashlib.sha256(
                _canonical_json(incoming_manifest).encode("utf-8")
            ).hexdigest()
            receipt = self._db.connection.execute(
                "SELECT manifest_hash, candidate_ids FROM eval_import_commands "
                "WHERE request_id = ?",
                (normalized_request_id,),
            ).fetchone()
            if receipt is not None:
                if str(receipt[0]) != manifest_hash:
                    raise EvalInboxConflict("idempotency conflict")
                return [self.require(candidate_id) for candidate_id in json.loads(str(receipt[1]))]
            for sample in sorted(samples, key=lambda item: item.sample_id):
                imported.append(
                    self._upsert(
                        source_kind="blind_grading_label",
                        dedupe_key=sample.sample_id,
                        source_request_id=normalized_request_id,
                        payload=sample,
                        release_gate_eligible=sample.eligible,
                        now=now,
                    )
                )
            self._db.connection.execute(
                "INSERT INTO eval_import_commands "
                "(request_id, manifest_hash, candidate_ids, created_at) VALUES (?, ?, ?, ?)",
                (
                    normalized_request_id,
                    manifest_hash,
                    _canonical_json([candidate.candidate_id for candidate in imported]),
                    now,
                ),
            )
        return imported

    def _upsert(
        self,
        *,
        source_kind: EvalSourceKind,
        dedupe_key: str,
        source_request_id: str,
        payload: EvalPayload,
        release_gate_eligible: bool,
        now: float,
    ) -> EvalInboxCandidateV1:
        payload_hash = _payload_hash(payload)
        candidate_id = derive_id(
            "eval-inbox",
            source_kind,
            dedupe_key,
            payload_hash,
            source_request_id,
        )
        current = self._active(source_kind, dedupe_key)
        if current is not None and current.payload_hash == payload_hash:
            return current
        if current is not None:
            self._db.connection.execute(
                "UPDATE eval_inbox_candidates SET lifecycle_status = 'superseded' "
                "WHERE candidate_id = ?",
                (current.candidate_id,),
            )
        self._db.connection.execute(
            "INSERT INTO eval_inbox_candidates "
            "(candidate_id, source_kind, dedupe_key, source_request_id, "
            "payload_schema_version, payload_hash, payload, lifecycle_status, "
            "review_status, release_gate_eligible, "
            "privacy_review_required, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', "
            "'pending', ?, 1, ?)",
            (
                candidate_id,
                source_kind,
                dedupe_key,
                source_request_id,
                payload.schema_version,
                payload_hash,
                _canonical_json(payload.model_dump(mode="json")),
                int(release_gate_eligible),
                now,
            ),
        )
        return self.require(candidate_id)

    def _active(self, source_kind: EvalSourceKind, dedupe_key: str) -> EvalInboxCandidateV1 | None:
        row = self._db.connection.execute(
            "SELECT candidate_id, source_kind, dedupe_key, source_request_id, "
            "payload_schema_version, payload_hash, payload, lifecycle_status, "
            "review_status, release_gate_eligible, "
            "privacy_review_required, review_request_id, review_reason, reviewed_at, created_at "
            "FROM eval_inbox_candidates WHERE source_kind = ? AND dedupe_key = ? "
            "AND lifecycle_status = 'active'",
            (source_kind, dedupe_key),
        ).fetchone()
        return None if row is None else _candidate_from_row(row)

    def require(self, candidate_id: str) -> EvalInboxCandidateV1:
        row = self._db.connection.execute(
            "SELECT candidate_id, source_kind, dedupe_key, source_request_id, "
            "payload_schema_version, payload_hash, payload, lifecycle_status, "
            "review_status, release_gate_eligible, "
            "privacy_review_required, review_request_id, review_reason, reviewed_at, created_at "
            "FROM eval_inbox_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return _candidate_from_row(row)

    def active(self) -> list[EvalInboxCandidateV1]:
        rows = self._db.connection.execute(
            "SELECT candidate_id, source_kind, dedupe_key, source_request_id, "
            "payload_schema_version, payload_hash, payload, lifecycle_status, "
            "review_status, release_gate_eligible, "
            "privacy_review_required, review_request_id, review_reason, reviewed_at, created_at "
            "FROM eval_inbox_candidates WHERE lifecycle_status = 'active' "
            "ORDER BY source_kind, candidate_id"
        ).fetchall()
        return [_candidate_from_row(row) for row in rows]

    def review(
        self,
        candidate_id: str,
        *,
        request_id: str,
        decision: EvalReviewStatus,
        reason: str,
        now: float,
    ) -> EvalInboxCandidateV1:
        if decision == "pending":
            raise ValueError("review decision must be approved or rejected")
        normalized_request_id = request_id.strip()
        normalized_reason = reason.strip()
        if not normalized_request_id or not normalized_reason:
            raise ValueError("request_id and review reason must not be blank")
        with self._db.transaction() as conn:
            candidate = self.require(candidate_id)
            if candidate.review_request_id == normalized_request_id:
                if (
                    candidate.review_status == decision
                    and candidate.review_reason == normalized_reason
                ):
                    return candidate
                raise EvalInboxConflict("idempotency conflict")
            if candidate.lifecycle_status != "active" or candidate.review_status != "pending":
                raise EvalInboxConflict("only active pending candidates can be reviewed")
            updated = conn.execute(
                "UPDATE eval_inbox_candidates SET review_status = ?, review_request_id = ?, "
                "review_reason = ?, reviewed_at = ? WHERE candidate_id = ? "
                "AND lifecycle_status = 'active' AND review_status = 'pending'",
                (decision, normalized_request_id, normalized_reason, now, candidate_id),
            )
            if updated.rowcount != 1:
                raise EvalInboxConflict("candidate changed during review")
        return self.require(candidate_id)

    def build_snapshot(self, candidate_ids: list[str], *, now: float) -> DatasetSnapshotV1:
        with self._db.transaction():
            return self._build_snapshot_locked(candidate_ids, now=now)

    def _build_snapshot_locked(self, candidate_ids: list[str], *, now: float) -> DatasetSnapshotV1:
        selected = [self.require(candidate_id) for candidate_id in sorted(set(candidate_ids))]
        if not selected:
            raise EvalInboxConflict("snapshot requires at least one approved candidate")
        if any(
            candidate.lifecycle_status != "active" or candidate.review_status != "approved"
            for candidate in selected
        ):
            raise EvalInboxConflict("snapshot requires active approved candidates")
        snapshot_items: list[DatasetSnapshotItemV1] = []
        for candidate in sorted(
            selected,
            key=lambda item: (item.source_kind, item.candidate_id),
        ):
            if (
                candidate.review_request_id is None
                or candidate.review_reason is None
                or candidate.reviewed_at is None
            ):
                raise EvalInboxConflict("approved candidate is missing review provenance")
            snapshot_items.append(
                DatasetSnapshotItemV1(
                    candidate_id=candidate.candidate_id,
                    source_kind=candidate.source_kind,
                    payload_schema_version=candidate.payload_schema_version,
                    payload_hash=candidate.payload_hash,
                    payload=candidate.payload,
                    release_gate_eligible=candidate.release_gate_eligible,
                    review_request_id=candidate.review_request_id,
                    review_reason=candidate.review_reason,
                    reviewed_at=candidate.reviewed_at,
                )
            )
        items = tuple(snapshot_items)
        canonical_items = [item.model_dump(mode="json") for item in items]
        manifest_body = {
            "schema_version": "eval-dataset-snapshot.v1",
            "redaction_profile": DEFAULT_REDACTION_PROFILE,
            "items": canonical_items,
        }
        content_sha256 = hashlib.sha256(_canonical_json(manifest_body).encode("utf-8")).hexdigest()
        existing = self.get_snapshot(content_sha256)
        if existing is not None:
            return existing
        eligible_blind = sum(
            item.source_kind == "blind_grading_label" and item.release_gate_eligible
            for item in items
        )
        snapshot = DatasetSnapshotV1(
            snapshot_id=content_sha256,
            content_sha256=content_sha256,
            candidate_count=len(items),
            eligible_blind_count=eligible_blind,
            exploratory_count=len(items) - eligible_blind,
            items=items,
            created_at=now,
        )
        self._db.connection.execute(
            "INSERT INTO eval_dataset_snapshots "
            "(snapshot_id, content_sha256, redaction_profile, candidate_count, "
            "eligible_blind_count, exploratory_count, items, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.snapshot_id,
                snapshot.content_sha256,
                snapshot.redaction_profile,
                snapshot.candidate_count,
                snapshot.eligible_blind_count,
                snapshot.exploratory_count,
                _canonical_json(canonical_items),
                snapshot.created_at,
            ),
        )
        self._db.commit()
        return snapshot

    def recent_snapshots(self, *, limit: int = 20) -> list[DatasetSnapshotV1]:
        rows = self._db.connection.execute(
            "SELECT snapshot_id FROM eval_dataset_snapshots "
            "ORDER BY created_at DESC, snapshot_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [self.require_snapshot(str(row[0])) for row in rows]

    def get_snapshot(self, snapshot_id: str) -> DatasetSnapshotV1 | None:
        row = self._db.connection.execute(
            "SELECT snapshot_id, content_sha256, redaction_profile, candidate_count, "
            "eligible_blind_count, exploratory_count, items, created_at "
            "FROM eval_dataset_snapshots WHERE snapshot_id = ? OR content_sha256 = ?",
            (snapshot_id, snapshot_id),
        ).fetchone()
        if row is None:
            return None
        return DatasetSnapshotV1.model_validate(
            {
                "snapshot_id": row[0],
                "content_sha256": row[1],
                "redaction_profile": row[2],
                "candidate_count": row[3],
                "eligible_blind_count": row[4],
                "exploratory_count": row[5],
                "items": json.loads(str(row[6])),
                "created_at": row[7],
            }
        )

    def require_snapshot(self, snapshot_id: str) -> DatasetSnapshotV1:
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)
        return snapshot

    def close(self) -> None:
        if self._owns_db:
            self._db.close()


def eligible_grading_samples(snapshot: DatasetSnapshotV1) -> list[GradingCalibrationSample]:
    """Calibration adapter deliberately excludes corrections and non-blind labels."""

    return [
        item.payload
        for item in snapshot.items
        if item.source_kind == "blind_grading_label"
        and item.release_gate_eligible
        and isinstance(item.payload, GradingCalibrationSample)
    ]


def _candidate_from_row(row: tuple[object, ...]) -> EvalInboxCandidateV1:
    source_kind = str(row[1])
    payload_data = json.loads(str(row[6]))
    payload: EvalPayload
    if source_kind == "verdict_correction":
        payload = GradingEvalCandidateV1.model_validate(payload_data)
    else:
        payload = GradingCalibrationSample.model_validate(payload_data)
    return EvalInboxCandidateV1.model_validate(
        {
            "candidate_id": row[0],
            "source_kind": source_kind,
            "dedupe_key": row[2],
            "source_request_id": row[3],
            "payload_schema_version": row[4],
            "payload_hash": row[5],
            "payload": payload,
            "lifecycle_status": row[7],
            "review_status": row[8],
            "release_gate_eligible": bool(row[9]),
            "privacy_review_required": bool(row[10]),
            "review_request_id": row[11],
            "review_reason": row[12],
            "reviewed_at": row[13],
            "created_at": row[14],
        }
    )


def _payload_hash(payload: EvalPayload) -> str:
    return hashlib.sha256(
        _canonical_json(payload.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

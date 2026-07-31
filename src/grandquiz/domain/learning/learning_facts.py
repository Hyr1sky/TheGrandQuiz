"""Durable, redacted learning facts and their transactional outbox."""

import json
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict

from grandquiz.domain.learning.persistence import (
    DatabaseSource,
    LearningDatabase,
    database_from,
)

LEARNING_FACT_SCHEMA_VERSION = "learning-fact-envelope.v1"
TAXONOMY_VERSION = "vocabulary.v1"
DEFAULT_REDACTION_PROFILE = "learning-facts.v1"


class LearningFactEnvelope(BaseModel):
    """Versioned, redacted envelope persisted in ``learning.db``."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["learning-fact-envelope.v1"] = LEARNING_FACT_SCHEMA_VERSION
    event_id: str
    event_type: str
    entity_id: str
    trace_id: str
    source_event_seq: int
    source_event_ts: float
    payload_schema_version: str
    taxonomy_version: str = TAXONOMY_VERSION
    redaction_profile: str = DEFAULT_REDACTION_PROFILE
    payload: Mapping[str, Any]


@runtime_checkable
class LearningFactJournal(Protocol):
    """Append/query Interface shared by workflows and deterministic projectors."""

    @property
    def transaction_owner(self) -> LearningDatabase: ...

    def append(self, fact: LearningFactEnvelope) -> None: ...

    def mark_published(self, event_id: str) -> None: ...

    def facts(
        self,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
    ) -> list[LearningFactEnvelope]: ...

    def pending(self) -> list[LearningFactEnvelope]: ...


class SqliteLearningFactJournal:
    """SQLite Adapter that atomically appends facts and outbox entries."""

    def __init__(self, db_path: DatabaseSource) -> None:
        self._db = database_from(db_path)
        self._conn = self._db.connection

    @property
    def transaction_owner(self) -> LearningDatabase:
        return self._db

    def append(self, fact: LearningFactEnvelope) -> None:
        payload = json.dumps(
            dict(fact.payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO learning_facts "
            "(event_id, event_type, entity_id, trace_id, source_event_seq, source_event_ts, "
            "payload_schema_version, taxonomy_version, redaction_profile, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact.event_id,
                fact.event_type,
                fact.entity_id,
                fact.trace_id,
                fact.source_event_seq,
                fact.source_event_ts,
                fact.payload_schema_version,
                fact.taxonomy_version,
                fact.redaction_profile,
                payload,
            ),
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO learning_fact_outbox (event_id, published) VALUES (?, 0)",
            (fact.event_id,),
        )
        self._db.commit()

    def mark_published(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE learning_fact_outbox SET published = 1 WHERE event_id = ?",
            (event_id,),
        )
        self._db.commit()

    def facts(
        self,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
    ) -> list[LearningFactEnvelope]:
        clauses: list[str] = []
        parameters: list[str] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(event_type)
        if trace_id is not None:
            clauses.append("trace_id = ?")
            parameters.append(trace_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            "SELECT event_id, event_type, entity_id, trace_id, source_event_seq, "
            "source_event_ts, payload_schema_version, taxonomy_version, "
            f"redaction_profile, payload FROM learning_facts{where} "
            "ORDER BY source_event_ts, trace_id, source_event_seq, event_id",
            tuple(parameters),
        ).fetchall()
        return [self._row_to_fact(row) for row in rows]

    def pending(self) -> list[LearningFactEnvelope]:
        rows = self._conn.execute(
            "SELECT f.event_id, f.event_type, f.entity_id, f.trace_id, "
            "f.source_event_seq, f.source_event_ts, f.payload_schema_version, "
            "f.taxonomy_version, f.redaction_profile, f.payload "
            "FROM learning_facts AS f JOIN learning_fact_outbox AS o "
            "ON o.event_id = f.event_id WHERE o.published = 0 "
            "ORDER BY f.source_event_ts, f.trace_id, f.source_event_seq, f.event_id"
        ).fetchall()
        return [self._row_to_fact(row) for row in rows]

    @staticmethod
    def _row_to_fact(row: tuple[object, ...]) -> LearningFactEnvelope:
        return LearningFactEnvelope(
            event_id=str(row[0]),
            event_type=str(row[1]),
            entity_id=str(row[2]),
            trace_id=str(row[3]),
            source_event_seq=int(str(row[4])),
            source_event_ts=float(str(row[5])),
            payload_schema_version=str(row[6]),
            taxonomy_version=str(row[7]),
            redaction_profile=str(row[8]),
            payload=cast("dict[str, Any]", json.loads(str(row[9]))),
        )

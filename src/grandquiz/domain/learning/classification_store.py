"""SQLite implementation for reviewed, revisioned knowledge classification."""

import json
from typing import Literal, cast

from grandquiz.domain.learning.classification import (
    ClassificationProposal,
    ClassificationSource,
    KnowledgeClassificationV1,
    KnowledgeKind,
    KnowledgeOrientation,
    LifecycleStatus,
    ResourceRevisionClassificationV1,
    ReviewStatus,
    SourceGenre,
    TagAssignmentV1,
    TagCandidateV1,
    VocabularyTermView,
    propose_item_classification,
)
from grandquiz.domain.learning.learning_facts import (
    LearningFactEnvelope,
    LearningFactJournal,
)
from grandquiz.domain.learning.models import KnowledgeItem, derive_id
from grandquiz.domain.learning.persistence import (
    DatabaseSource,
    LearningDatabase,
    database_from,
)
from grandquiz.domain.learning.vocabulary import VocabularyCatalog
from grandquiz.kernel.clock import Clock, SystemClock


class ClassificationIdempotencyConflict(ValueError):
    """同一幂等键被用于不同的分类命令。"""


class SqliteClassificationRepository:
    """Append revisions and maintain one explicit active classification per entity."""

    def __init__(
        self,
        db_path: DatabaseSource,
        *,
        vocabulary: VocabularyCatalog,
        learning_facts: LearningFactJournal | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._db = database_from(db_path)
        self._conn = self._db.connection
        self._vocabulary = vocabulary
        self._learning_facts = learning_facts
        self._clock = clock or SystemClock()
        self._sync_seed_terms()

    @property
    def transaction_owner(self) -> LearningDatabase:
        return self._db

    def propose_item(
        self,
        item: KnowledgeItem,
        *,
        tag_namespace: str = "topic",
    ) -> ClassificationProposal:
        """Classify through the merged seed + approved local vocabulary view."""

        proposal = propose_item_classification(item, vocabulary=self._vocabulary)
        unresolved = tuple(
            value
            for value in proposal.tag_candidates
            if self._managed_term_for_value(tag_namespace, value) is None
        )
        return proposal.model_copy(update={"tag_candidates": unresolved})

    def record_ingest_proposal(
        self,
        item: KnowledgeItem,
        *,
        ingest_id: str,
        trace_id: str,
    ) -> None:
        """把一个获批 item 的规则分类与未知标签作为同一 ingest 的待审核事实写入。"""

        proposal = self.propose_item(item)
        self.classify_item(
            item_id=item.item_id,
            request_id=f"{ingest_id}:{item.item_id}:{proposal.classifier_version}",
            primary_kind=proposal.primary_kind,
            orientations=set(proposal.orientations),
            trace_id=trace_id,
            classified_by="rule",
            review_status="proposed",
        )
        for raw_tag in proposal.tag_candidates:
            self.propose_tag_candidate(
                request_id=f"{ingest_id}:{item.item_id}:tag:{raw_tag}",
                namespace="topic",
                raw_value=raw_tag,
                trace_id=trace_id,
            )

    def classify_item(
        self,
        *,
        item_id: str,
        request_id: str,
        primary_kind: KnowledgeKind,
        orientations: set[KnowledgeOrientation],
        trace_id: str,
        classified_by: ClassificationSource = "user",
        review_status: ReviewStatus = "approved",
    ) -> KnowledgeClassificationV1:
        if primary_kind not in self._vocabulary.keys("knowledge_kind"):
            raise ValueError(f"unknown knowledge_kind: {primary_kind}")
        if not orientations or not orientations <= self._vocabulary.keys("knowledge_orientation"):
            raise ValueError("orientations must be a non-empty controlled set")
        classification_id = derive_id("knowledge-classification", item_id, request_id)
        existing = self._classification_by_id(classification_id)
        if existing is not None:
            original_fact = self._fact(classification_id)
            original_payload = (
                existing.model_dump(mode="json") if original_fact is None else original_fact.payload
            )
            if (
                original_payload.get("primary_kind") != primary_kind
                or tuple(original_payload.get("orientations", ())) != tuple(sorted(orientations))
                or original_payload.get("classified_by") != classified_by
                or original_payload.get("review_status") != review_status
            ):
                raise ClassificationIdempotencyConflict("相同 request_id 已用于不同的知识分类")
            return existing
        with self._db.transaction():
            current = self.active_for_item(item_id)
            history = self.history_for_item(item_id)
            revision = 1 if not history else max(item.revision for item in history) + 1
            if current is not None and review_status == "approved":
                self._conn.execute(
                    "UPDATE knowledge_classifications SET lifecycle_status = 'superseded' "
                    "WHERE classification_id = ?",
                    (current.classification_id,),
                )
            classification = KnowledgeClassificationV1(
                taxonomy_version=self._vocabulary.schema_version,
                classification_id=classification_id,
                item_id=item_id,
                revision=revision,
                supersedes_id=None if current is None else current.classification_id,
                primary_kind=primary_kind,
                orientations=tuple(sorted(orientations)),
                classified_by=classified_by,
                review_status=review_status,
                lifecycle_status="active",
                trace_id=trace_id,
            )
            self._insert_item_classification(classification)
            self._record_fact(
                event_type="learning.knowledge_classified",
                entity_id=classification.item_id,
                event_id=classification.classification_id,
                trace_id=classification.trace_id,
                revision=classification.revision,
                payload=classification.model_dump(mode="json"),
            )
        return classification

    def review_item_classification(
        self,
        classification_id: str,
        status: ReviewStatus,
        *,
        request_id: str,
    ) -> KnowledgeClassificationV1 | None:
        current = self._classification_by_id(classification_id)
        if current is None:
            return None
        event_id = derive_id(classification_id, "review", request_id)
        existing_fact = self._fact(event_id)
        if existing_fact is not None:
            if existing_fact.payload.get("review_status") != status:
                raise ValueError("相同 request_id 已用于不同的分类审核")
            return current
        with self._db.transaction():
            if status == "approved":
                approved = self.active_for_item(current.item_id)
                if approved is not None and approved.classification_id != classification_id:
                    self._conn.execute(
                        "UPDATE knowledge_classifications SET lifecycle_status = 'superseded' "
                        "WHERE classification_id = ?",
                        (approved.classification_id,),
                    )
            self._conn.execute(
                "UPDATE knowledge_classifications SET review_status = ?, lifecycle_status = ? "
                "WHERE classification_id = ?",
                (
                    status,
                    "retracted" if status == "rejected" else "active",
                    classification_id,
                ),
            )
            reviewed = self._classification_by_id(classification_id)
            assert reviewed is not None
            self._record_fact(
                event_type="learning.knowledge_classification_reviewed",
                entity_id=reviewed.item_id,
                event_id=event_id,
                trace_id=reviewed.trace_id,
                revision=self._next_fact_revision(
                    "learning.knowledge_classification_reviewed",
                    reviewed.item_id,
                ),
                payload={
                    "classification_id": classification_id,
                    "review_status": status,
                    "request_id": request_id,
                },
            )
        return reviewed

    def active_for_item(self, item_id: str) -> KnowledgeClassificationV1 | None:
        row = self._conn.execute(
            "SELECT classification_id, item_id, revision, supersedes_id, primary_kind, "
            "orientations, classified_by, review_status, "
            "lifecycle_status, trace_id, taxonomy_version FROM knowledge_classifications "
            "WHERE item_id = ? AND lifecycle_status = 'active' "
            "AND review_status = 'approved'",
            (item_id,),
        ).fetchone()
        return None if row is None else _row_to_knowledge_classification(row)

    def history_for_item(self, item_id: str) -> list[KnowledgeClassificationV1]:
        rows = self._conn.execute(
            "SELECT classification_id, item_id, revision, supersedes_id, primary_kind, "
            "orientations, classified_by, review_status, "
            "lifecycle_status, trace_id, taxonomy_version FROM knowledge_classifications "
            "WHERE item_id = ? ORDER BY revision",
            (item_id,),
        ).fetchall()
        return [_row_to_knowledge_classification(row) for row in rows]

    def classify_revision(
        self,
        *,
        revision_id: str,
        request_id: str,
        source_genre: SourceGenre,
        trace_id: str,
    ) -> ResourceRevisionClassificationV1:
        if source_genre not in self._vocabulary.keys("source_genre"):
            raise ValueError(f"unknown source_genre: {source_genre}")
        classification_id = derive_id("resource-revision-classification", revision_id, request_id)
        existing = self._revision_classification_by_id(classification_id)
        if existing is not None:
            if existing.primary_source_genre != source_genre:
                raise ClassificationIdempotencyConflict("相同 request_id 已用于不同的资源分类")
            return existing
        with self._db.transaction():
            current = self.active_for_revision(revision_id)
            revision = 1 if current is None else current.revision + 1
            if current is not None:
                self._conn.execute(
                    "UPDATE resource_revision_classifications "
                    "SET lifecycle_status = 'superseded' WHERE classification_id = ?",
                    (current.classification_id,),
                )
            result = ResourceRevisionClassificationV1(
                taxonomy_version=self._vocabulary.schema_version,
                classification_id=classification_id,
                revision_id=revision_id,
                revision=revision,
                supersedes_id=None if current is None else current.classification_id,
                primary_source_genre=source_genre,
                classified_by="user",
                review_status="approved",
                lifecycle_status="active",
                trace_id=trace_id,
            )
            self._conn.execute(
                "INSERT INTO resource_revision_classifications "
                "(classification_id, revision_id, revision, supersedes_id, "
                "primary_source_genre, classified_by, "
                "review_status, lifecycle_status, trace_id, taxonomy_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result.classification_id,
                    result.revision_id,
                    result.revision,
                    result.supersedes_id,
                    result.primary_source_genre,
                    result.classified_by,
                    result.review_status,
                    result.lifecycle_status,
                    result.trace_id,
                    result.taxonomy_version,
                ),
            )
            self._record_fact(
                event_type="learning.resource_revision_classified",
                entity_id=result.revision_id,
                event_id=result.classification_id,
                trace_id=result.trace_id,
                revision=result.revision,
                payload=result.model_dump(mode="json"),
            )
        return result

    def active_for_revision(self, revision_id: str) -> ResourceRevisionClassificationV1 | None:
        row = self._conn.execute(
            "SELECT classification_id, revision_id, revision, supersedes_id, "
            "primary_source_genre, classified_by, "
            "review_status, lifecycle_status, trace_id, taxonomy_version "
            "FROM resource_revision_classifications WHERE revision_id = ? "
            "AND lifecycle_status = 'active'",
            (revision_id,),
        ).fetchone()
        return None if row is None else _row_to_revision_classification(row)

    def review_term(
        self,
        term_id: str,
        status: Literal["proposed", "approved", "deprecated"],
        *,
        request_id: str,
        trace_id: str,
        replacement_term_id: str | None = None,
    ) -> VocabularyTermView | None:
        event_id = derive_id(term_id, "review", request_id)
        existing_fact = self._fact(event_id)
        if existing_fact is not None:
            if (
                existing_fact.payload.get("review_status") != status
                or existing_fact.payload.get("replacement_term_id") != replacement_term_id
            ):
                raise ValueError("相同 request_id 已用于不同的词表审核")
            return self.term(term_id)
        if replacement_term_id is not None and self.term(replacement_term_id) is None:
            raise KeyError(replacement_term_id)
        with self._db.transaction():
            self._conn.execute(
                "UPDATE vocabulary_terms SET status = ?, replacement_term_id = ? WHERE term_id = ?",
                (status, replacement_term_id, term_id),
            )
            term = self.term(term_id)
            if term is None:
                return None
            self._record_fact(
                event_type="learning.vocabulary_term_reviewed",
                entity_id=term_id,
                event_id=event_id,
                trace_id=trace_id,
                revision=self._next_fact_revision(
                    "learning.vocabulary_term_reviewed",
                    term_id,
                ),
                payload={
                    "term_id": term_id,
                    "review_status": status,
                    "replacement_term_id": replacement_term_id,
                    "request_id": request_id,
                },
            )
        return term

    def propose_tag_candidate(
        self,
        *,
        request_id: str,
        namespace: str,
        raw_value: str,
        trace_id: str,
    ) -> TagCandidateV1:
        resolved_term_id = self._managed_term_for_value(namespace, raw_value)
        if resolved_term_id is not None:
            raise ValueError(f"managed term already exists: {resolved_term_id}")
        normalized = "_".join(raw_value.casefold().split())
        candidate_id = derive_id("tag-candidate", request_id)
        existing = self._candidate(candidate_id)
        if existing is not None:
            if (
                existing.namespace != namespace
                or existing.raw_value != raw_value
                or existing.normalized_value != normalized
            ):
                raise ClassificationIdempotencyConflict("相同 request_id 已用于不同的标签候选")
            return existing
        candidate = TagCandidateV1(
            candidate_id=candidate_id,
            raw_value=raw_value,
            namespace=namespace,
            normalized_value=normalized,
            review_status="proposed",
            trace_id=trace_id,
            taxonomy_version=self._vocabulary.schema_version,
        )
        with self._db.transaction():
            self._conn.execute(
                "INSERT INTO tag_candidates "
                "(candidate_id, raw_value, namespace, normalized_value, review_status, "
                "trace_id, taxonomy_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.candidate_id,
                    candidate.raw_value,
                    candidate.namespace,
                    candidate.normalized_value,
                    candidate.review_status,
                    candidate.trace_id,
                    candidate.taxonomy_version,
                ),
            )
            self._record_fact(
                event_type="learning.tag_candidate_proposed",
                entity_id=candidate.candidate_id,
                event_id=candidate.candidate_id,
                trace_id=candidate.trace_id,
                revision=1,
                payload=candidate.model_dump(mode="json"),
            )
        return candidate

    def review_tag_candidate(
        self,
        candidate_id: str,
        status: ReviewStatus,
        *,
        request_id: str,
    ) -> TagCandidateV1 | None:
        current = self._candidate(candidate_id)
        if current is None:
            return None
        event_id = derive_id(candidate_id, "review", request_id)
        existing_fact = self._fact(event_id)
        if existing_fact is not None:
            if existing_fact.payload.get("review_status") != status:
                raise ValueError("相同 request_id 已用于不同的标签候选审核")
            return current
        with self._db.transaction():
            self._conn.execute(
                "UPDATE tag_candidates SET review_status = ? WHERE candidate_id = ?",
                (status, candidate_id),
            )
            if status == "approved":
                candidate = self._candidate(candidate_id)
                assert candidate is not None
                term_id = f"{candidate.namespace}:{candidate.normalized_value}"
                self._conn.execute(
                    "INSERT INTO vocabulary_terms "
                    "(term_id, namespace, term_key, label_zh, aliases, status, "
                    "replacement_term_id, taxonomy_version) "
                    "VALUES (?, ?, ?, ?, '[]', 'approved', NULL, ?) "
                    "ON CONFLICT(term_id) DO NOTHING",
                    (
                        term_id,
                        candidate.namespace,
                        candidate.normalized_value,
                        candidate.raw_value,
                        self._vocabulary.schema_version,
                    ),
                )
            reviewed = self._candidate(candidate_id)
            assert reviewed is not None
            self._record_fact(
                event_type="learning.tag_candidate_reviewed",
                entity_id=candidate_id,
                event_id=event_id,
                trace_id=reviewed.trace_id,
                revision=self._next_fact_revision(
                    "learning.tag_candidate_reviewed",
                    candidate_id,
                ),
                payload={
                    "candidate_id": candidate_id,
                    "review_status": status,
                    "request_id": request_id,
                },
            )
        return reviewed

    def term(self, term_id: str) -> VocabularyTermView | None:
        row = self._conn.execute(
            "SELECT term_id, namespace, term_key, label_zh, aliases, status, "
            "replacement_term_id, taxonomy_version FROM vocabulary_terms WHERE term_id = ?",
            (term_id,),
        ).fetchone()
        return None if row is None else _row_to_vocabulary_term(row)

    def assign_tag(
        self,
        *,
        item_id: str,
        term_id: str,
        request_id: str,
        trace_id: str,
    ) -> TagAssignmentV1:
        term = self.term(term_id)
        if term is None:
            raise KeyError(term_id)
        if term.status == "deprecated" and term.replacement_term_id is not None:
            term_id = term.replacement_term_id
            term = self.term(term_id)
            if term is None:
                raise KeyError(term_id)
        if term.status != "approved":
            raise ValueError(f"managed term is not approved: {term_id}")
        assignment_id = derive_id("tag-assignment", item_id, request_id)
        existing = self._assignment_by_id(assignment_id)
        if existing is not None:
            if existing.term_id != term_id:
                raise ClassificationIdempotencyConflict("相同 request_id 已用于不同的标签分配")
            return existing
        with self._db.transaction():
            current = self._active_assignment(item_id, term_id)
            revision = 1 if current is None else current.revision + 1
            if current is not None:
                self._conn.execute(
                    "UPDATE tag_assignments SET lifecycle_status = 'superseded' "
                    "WHERE assignment_id = ?",
                    (current.assignment_id,),
                )
            assignment = TagAssignmentV1(
                assignment_id=assignment_id,
                item_id=item_id,
                term_id=term_id,
                revision=revision,
                supersedes_id=None if current is None else current.assignment_id,
                assigned_by="user",
                review_status="approved",
                lifecycle_status="active",
                trace_id=trace_id,
                taxonomy_version=self._vocabulary.schema_version,
            )
            self._conn.execute(
                "INSERT INTO tag_assignments "
                "(assignment_id, item_id, term_id, revision, supersedes_id, assigned_by, "
                "review_status, lifecycle_status, trace_id, taxonomy_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment.assignment_id,
                    assignment.item_id,
                    assignment.term_id,
                    assignment.revision,
                    assignment.supersedes_id,
                    assignment.assigned_by,
                    assignment.review_status,
                    assignment.lifecycle_status,
                    assignment.trace_id,
                    assignment.taxonomy_version,
                ),
            )
            self._record_fact(
                event_type="learning.managed_tag_assigned",
                entity_id=item_id,
                event_id=assignment.assignment_id,
                trace_id=trace_id,
                revision=revision,
                payload=assignment.model_dump(mode="json"),
            )
        return assignment

    def tags_for_item(self, item_id: str) -> list[TagAssignmentV1]:
        rows = self._conn.execute(
            "SELECT assignment_id, item_id, term_id, revision, supersedes_id, assigned_by, "
            "review_status, lifecycle_status, trace_id, taxonomy_version FROM tag_assignments "
            "WHERE item_id = ? AND lifecycle_status = 'active' ORDER BY term_id",
            (item_id,),
        ).fetchall()
        return [_row_to_tag_assignment(row) for row in rows]

    def _classification_by_id(self, classification_id: str) -> KnowledgeClassificationV1 | None:
        row = self._conn.execute(
            "SELECT classification_id, item_id, revision, supersedes_id, primary_kind, "
            "orientations, classified_by, review_status, "
            "lifecycle_status, trace_id, taxonomy_version FROM knowledge_classifications "
            "WHERE classification_id = ?",
            (classification_id,),
        ).fetchone()
        return None if row is None else _row_to_knowledge_classification(row)

    def _revision_classification_by_id(
        self, classification_id: str
    ) -> ResourceRevisionClassificationV1 | None:
        row = self._conn.execute(
            "SELECT classification_id, revision_id, revision, supersedes_id, "
            "primary_source_genre, classified_by, "
            "review_status, lifecycle_status, trace_id, taxonomy_version "
            "FROM resource_revision_classifications WHERE classification_id = ?",
            (classification_id,),
        ).fetchone()
        return None if row is None else _row_to_revision_classification(row)

    def _assignment_by_id(self, assignment_id: str) -> TagAssignmentV1 | None:
        row = self._conn.execute(
            "SELECT assignment_id, item_id, term_id, revision, supersedes_id, assigned_by, "
            "review_status, lifecycle_status, trace_id, taxonomy_version FROM tag_assignments "
            "WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        return None if row is None else _row_to_tag_assignment(row)

    def _active_assignment(self, item_id: str, term_id: str) -> TagAssignmentV1 | None:
        row = self._conn.execute(
            "SELECT assignment_id, item_id, term_id, revision, supersedes_id, assigned_by, "
            "review_status, lifecycle_status, trace_id, taxonomy_version FROM tag_assignments "
            "WHERE item_id = ? AND term_id = ? AND lifecycle_status = 'active'",
            (item_id, term_id),
        ).fetchone()
        return None if row is None else _row_to_tag_assignment(row)

    def _candidate(self, candidate_id: str) -> TagCandidateV1 | None:
        row = self._conn.execute(
            "SELECT candidate_id, raw_value, namespace, normalized_value, review_status, "
            "trace_id, taxonomy_version FROM tag_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        namespace = str(row[2])
        normalized_value = str(row[3])
        review_status = cast("ReviewStatus", row[4])
        promoted_term_id = (
            f"{namespace}:{normalized_value}" if review_status == "approved" else None
        )
        return TagCandidateV1(
            candidate_id=str(row[0]),
            raw_value=str(row[1]),
            namespace=namespace,
            normalized_value=normalized_value,
            review_status=review_status,
            promoted_term_id=promoted_term_id,
            trace_id=str(row[5]),
            taxonomy_version=str(row[6]),
        )

    def _managed_term_for_value(self, namespace: str, value: str) -> str | None:
        resolved = self._vocabulary.resolve_managed_term(value)
        if resolved is not None and resolved.namespace == namespace:
            return resolved.term_id
        normalized = "_".join(value.casefold().split())
        row = self._conn.execute(
            "SELECT term_id FROM vocabulary_terms "
            "WHERE namespace = ? AND term_key = ? AND status = 'approved'",
            (namespace, normalized),
        ).fetchone()
        return None if row is None else str(row[0])

    def _record_fact(
        self,
        *,
        event_type: str,
        entity_id: str,
        event_id: str,
        trace_id: str,
        revision: int,
        payload: dict[str, object],
    ) -> None:
        if self._learning_facts is None:
            return
        self._learning_facts.append(
            LearningFactEnvelope(
                event_id=event_id,
                event_type=event_type,
                entity_id=entity_id,
                trace_id=trace_id,
                source_event_seq=revision,
                source_event_ts=self._clock.now(),
                payload_schema_version=f"{event_type}.v1",
                taxonomy_version=self._vocabulary.schema_version,
                payload=payload,
            )
        )

    def _next_fact_revision(self, event_type: str, entity_id: str) -> int:
        if self._learning_facts is None:
            return 1
        revisions = [
            fact.source_event_seq
            for fact in self._learning_facts.facts(event_type=event_type)
            if fact.entity_id == entity_id
        ]
        return max(revisions, default=0) + 1

    def _fact(self, event_id: str) -> LearningFactEnvelope | None:
        if self._learning_facts is None:
            return None
        return next(
            (fact for fact in self._learning_facts.facts() if fact.event_id == event_id),
            None,
        )

    def _insert_item_classification(self, item: KnowledgeClassificationV1) -> None:
        self._conn.execute(
            "INSERT INTO knowledge_classifications "
            "(classification_id, item_id, revision, supersedes_id, primary_kind, "
            "orientations, classified_by, review_status, "
            "lifecycle_status, trace_id, taxonomy_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.classification_id,
                item.item_id,
                item.revision,
                item.supersedes_id,
                item.primary_kind,
                json.dumps(item.orientations, ensure_ascii=False),
                item.classified_by,
                item.review_status,
                item.lifecycle_status,
                item.trace_id,
                item.taxonomy_version,
            ),
        )

    def _sync_seed_terms(self) -> None:
        for term in self._vocabulary.managed_seed_terms:
            self._conn.execute(
                "INSERT INTO vocabulary_terms "
                "(term_id, namespace, term_key, label_zh, aliases, status, "
                "replacement_term_id, taxonomy_version) VALUES (?, ?, ?, ?, ?, ?, NULL, ?) "
                "ON CONFLICT(term_id) DO NOTHING",
                (
                    term.term_id,
                    term.namespace,
                    term.key,
                    term.label_zh,
                    json.dumps(term.aliases, ensure_ascii=False),
                    term.status,
                    self._vocabulary.schema_version,
                ),
            )
        self._db.commit()


def _row_to_knowledge_classification(row: tuple[object, ...]) -> KnowledgeClassificationV1:
    return KnowledgeClassificationV1(
        classification_id=str(row[0]),
        item_id=str(row[1]),
        revision=int(str(row[2])),
        supersedes_id=None if row[3] is None else str(row[3]),
        primary_kind=cast("KnowledgeKind", row[4]),
        orientations=tuple(cast("list[KnowledgeOrientation]", json.loads(str(row[5])))),
        classified_by=cast("ClassificationSource", row[6]),
        review_status=cast("ReviewStatus", row[7]),
        lifecycle_status=cast("LifecycleStatus", row[8]),
        trace_id=str(row[9]),
        taxonomy_version=str(row[10]),
    )


def _row_to_revision_classification(
    row: tuple[object, ...],
) -> ResourceRevisionClassificationV1:
    return ResourceRevisionClassificationV1(
        classification_id=str(row[0]),
        revision_id=str(row[1]),
        revision=int(str(row[2])),
        supersedes_id=None if row[3] is None else str(row[3]),
        primary_source_genre=cast("SourceGenre", row[4]),
        classified_by=cast("ClassificationSource", row[5]),
        review_status=cast("ReviewStatus", row[6]),
        lifecycle_status=cast("LifecycleStatus", row[7]),
        trace_id=str(row[8]),
        taxonomy_version=str(row[9]),
    )


def _row_to_vocabulary_term(row: tuple[object, ...]) -> VocabularyTermView:
    return VocabularyTermView(
        term_id=str(row[0]),
        namespace=str(row[1]),
        key=str(row[2]),
        label_zh=str(row[3]),
        aliases=tuple(cast("list[str]", json.loads(str(row[4])))),
        status=cast("Literal['proposed', 'approved', 'deprecated']", row[5]),
        replacement_term_id=None if row[6] is None else str(row[6]),
        taxonomy_version=str(row[7]),
    )


def _row_to_tag_assignment(row: tuple[object, ...]) -> TagAssignmentV1:
    return TagAssignmentV1(
        assignment_id=str(row[0]),
        item_id=str(row[1]),
        term_id=str(row[2]),
        revision=int(str(row[3])),
        supersedes_id=None if row[4] is None else str(row[4]),
        assigned_by=cast("ClassificationSource", row[5]),
        review_status=cast("ReviewStatus", row[6]),
        lifecycle_status=cast("LifecycleStatus", row[7]),
        trace_id=str(row[8]),
        taxonomy_version=str(row[9]),
    )

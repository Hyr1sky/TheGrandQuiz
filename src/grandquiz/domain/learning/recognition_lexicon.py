"""Revision-scoped speech-recognition lexicon and exact-item hint selection."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from grandquiz.domain.learning.classification import TagAssignmentV1, VocabularyTermView
from grandquiz.domain.learning.models import (
    DocumentNode,
    EvidenceLocator,
    KnowledgeItem,
    ResourceRevision,
    derive_id,
)
from grandquiz.domain.learning.persistence import (
    DatabaseSource,
    LearningDatabase,
    database_from,
)
from grandquiz.domain.learning.store import Store

LEXICON_BUILDER_VERSION = "recognition-lexicon-builder.v1"
HINT_SELECTOR_VERSION = "transcription-hint-selector.v1"
MAX_TRANSCRIPTION_HINTS = 50

LexiconSourceKind = Literal["knowledge_item", "heading", "code_identifier", "approved_tag"]

_IDENTIFIER_CANDIDATE = re.compile(r"(?<![\w])([A-Za-z][A-Za-z0-9_.-]{1,63})(?![\w])")
_COMMON_CODE_WORDS = frozenset(
    {
        "and",
        "as",
        "async",
        "await",
        "class",
        "def",
        "else",
        "false",
        "for",
        "from",
        "if",
        "import",
        "in",
        "none",
        "not",
        "or",
        "pass",
        "return",
        "true",
        "while",
        "with",
        "yield",
    }
)


class RecognitionLexiconEntry(BaseModel):
    """One provider-neutral term and its source evidence inside a revision projection."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["recognition-lexicon-entry.v1"] = "recognition-lexicon-entry.v1"
    entry_id: str
    term: str = Field(min_length=2, max_length=64)
    normalized_term: str = Field(min_length=2, max_length=64)
    source_kind: LexiconSourceKind
    source_refs: tuple[str, ...] = Field(min_length=1)
    priority: int = Field(ge=1, le=5)


class RecognitionLexicon(BaseModel):
    """Immutable content-addressed speech-recognition projection for one revision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["recognition-lexicon.v1"] = "recognition-lexicon.v1"
    lexicon_id: str
    revision_id: str
    builder_version: str
    entries: tuple[RecognitionLexiconEntry, ...]


class ApprovedRecognitionTerm(BaseModel):
    """A reviewed vocabulary term supplied as an authoritative lexicon input."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    assignment_id: str
    term: str = Field(min_length=2, max_length=64)


class ApprovedTagReader(Protocol):
    def tags_for_item(self, item_id: str) -> list[TagAssignmentV1]: ...

    def term(self, term_id: str) -> VocabularyTermView | None: ...


class TranscriptionHintEntry(BaseModel):
    """One lexicon entry frozen for a single VoiceRun."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    term: str
    priority: int = Field(ge=1, le=5)


class TranscriptionHints(BaseModel):
    """Content-addressed, exact-item term selection for one transcription request."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["transcription-hints.v1"] = "transcription-hints.v1"
    hint_set_id: str
    lexicon_ids: tuple[str, ...] = Field(min_length=1)
    item_ids: tuple[str, ...] = Field(min_length=1)
    selector_version: str
    entries: tuple[TranscriptionHintEntry, ...]


class _Candidate:
    def __init__(
        self,
        *,
        term: str,
        normalized_term: str,
        source_kind: LexiconSourceKind,
        source_ref: str,
        priority: int,
    ) -> None:
        self.term = term
        self.normalized_term = normalized_term
        self.source_kind: LexiconSourceKind = source_kind
        self.source_refs: set[str] = {source_ref}
        self.priority = priority


def _normalize_term(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _clean_term(value: str) -> str | None:
    term = " ".join(unicodedata.normalize("NFC", value).split()).strip("`'\".,;:，。；：")
    if not 2 <= len(term) <= 64 or term.isdecimal():
        return None
    if len(term.split()) > 6:
        return None
    normalized = _normalize_term(term)
    if len(normalized) < 2 or normalized in _COMMON_CODE_WORDS:
        return None
    return term


def _looks_like_identifier(value: str) -> bool:
    if value.casefold() in _COMMON_CODE_WORDS:
        return False
    return (
        value.isupper()
        or any(character in value for character in "_.-")
        or any(character.isupper() for character in value[1:])
    )


def _technical_identifiers(value: str) -> Iterable[str]:
    for match in _IDENTIFIER_CANDIDATE.finditer(value):
        candidate = match.group(1)
        if _looks_like_identifier(candidate):
            yield candidate


def build_recognition_lexicon(
    *,
    revision: ResourceRevision,
    nodes: Sequence[DocumentNode],
    items: Sequence[KnowledgeItem],
    approved_terms: Sequence[ApprovedRecognitionTerm] = (),
    builder_version: str = LEXICON_BUILDER_VERSION,
) -> RecognitionLexicon:
    """Build a deterministic revision projection from approved material facts."""

    candidates: dict[str, _Candidate] = {}

    def add(
        value: str,
        *,
        source_kind: LexiconSourceKind,
        source_ref: str,
        priority: int,
    ) -> None:
        term = _clean_term(value)
        if term is None:
            return
        normalized = _normalize_term(term)
        current = candidates.get(normalized)
        if current is None:
            candidates[normalized] = _Candidate(
                term=term,
                normalized_term=normalized,
                source_kind=source_kind,
                source_ref=source_ref,
                priority=priority,
            )
            return
        current.source_refs.add(source_ref)
        if priority > current.priority:
            current.term = term
            current.source_kind = source_kind
            current.priority = priority

    item_ids = {item.item_id for item in items}
    for item in sorted(items, key=lambda value: value.item_id):
        if item.resource_id != revision.resource_id:
            raise ValueError(f"KnowledgeItem 不属于 revision 资源：{item.item_id}")
        source_ref = f"item:{item.item_id}"
        add(item.concept, source_kind="knowledge_item", source_ref=source_ref, priority=5)
        for identifier in _technical_identifiers(item.concept):
            add(identifier, source_kind="knowledge_item", source_ref=source_ref, priority=5)

    for approved in sorted(
        approved_terms,
        key=lambda value: (value.item_id, value.assignment_id, _normalize_term(value.term)),
    ):
        if approved.item_id not in item_ids:
            raise ValueError(
                f"approved term 不属于本 revision 的 KnowledgeItem：{approved.item_id}"
            )
        add(
            approved.term,
            source_kind="approved_tag",
            source_ref=f"item:{approved.item_id}",
            priority=5,
        )
        add(
            approved.term,
            source_kind="approved_tag",
            source_ref=f"tag-assignment:{approved.assignment_id}",
            priority=5,
        )

    for node in sorted(nodes, key=lambda value: (value.ordinal, value.node_id)):
        if node.revision_id != revision.revision_id:
            raise ValueError(f"DocumentNode 不属于 revision：{node.node_id}")
        source_ref = f"node:{node.node_id}"
        if node.title:
            add(node.title, source_kind="heading", source_ref=source_ref, priority=4)
            for identifier in _technical_identifiers(node.title):
                add(identifier, source_kind="heading", source_ref=source_ref, priority=4)
        if node.kind == "code":
            body = revision.raw_content[node.start_offset : node.end_offset]
            for identifier in _technical_identifiers(body):
                add(
                    identifier,
                    source_kind="code_identifier",
                    source_ref=source_ref,
                    priority=5,
                )

    entries: list[RecognitionLexiconEntry] = []
    for normalized, candidate in sorted(candidates.items()):
        refs = tuple(sorted(candidate.source_refs))
        entries.append(
            RecognitionLexiconEntry(
                entry_id=derive_id(
                    "recognition-lexicon-entry.v1",
                    revision.revision_id,
                    normalized,
                    candidate.source_kind,
                    *refs,
                ),
                term=candidate.term,
                normalized_term=normalized,
                source_kind=candidate.source_kind,
                source_refs=refs,
                priority=candidate.priority,
            )
        )
    canonical_entries = json.dumps(
        [entry.model_dump(mode="json") for entry in entries],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return RecognitionLexicon(
        lexicon_id=derive_id(
            "recognition-lexicon.v1",
            revision.revision_id,
            builder_version,
            canonical_entries,
        ),
        revision_id=revision.revision_id,
        builder_version=builder_version,
        entries=tuple(entries),
    )


def select_transcription_hints(
    *,
    lexicons: Sequence[RecognitionLexicon],
    items: Sequence[KnowledgeItem],
    limit: int = MAX_TRANSCRIPTION_HINTS,
    selector_version: str = HINT_SELECTOR_VERSION,
) -> TranscriptionHints:
    """Select terms whose source is the current item or one of its exact Evidence nodes."""

    if not lexicons:
        raise ValueError("至少需要一个 RecognitionLexicon")
    if not items:
        raise ValueError("至少需要一个当前 KnowledgeItem")
    if not 1 <= limit <= MAX_TRANSCRIPTION_HINTS:
        raise ValueError(f"TranscriptionHints limit 必须在 1..{MAX_TRANSCRIPTION_HINTS}")

    lexicon_ids = tuple(sorted({lexicon.lexicon_id for lexicon in lexicons}))
    revision_ids = {lexicon.revision_id for lexicon in lexicons}
    item_ids = tuple(sorted({item.item_id for item in items}))
    allowed_refs = {f"item:{item_id}" for item_id in item_ids}
    item_revision_ids: set[str] = set()
    for item in items:
        for evidence in item.evidence:
            if isinstance(evidence.locator, EvidenceLocator):
                item_revision_ids.add(evidence.locator.revision_id)
                allowed_refs.add(f"node:{evidence.locator.node_id}")
    if not item_revision_ids:
        raise ValueError("当前 KnowledgeItem 缺少 exact revision Evidence")
    if not item_revision_ids <= revision_ids:
        raise ValueError("RecognitionLexicon 与当前 KnowledgeItem revision 不匹配")

    selected_by_id: dict[str, RecognitionLexiconEntry] = {}
    for lexicon in lexicons:
        for entry in lexicon.entries:
            if allowed_refs.intersection(entry.source_refs):
                selected_by_id[entry.entry_id] = entry
    selected = sorted(
        selected_by_id.values(),
        key=lambda entry: (-entry.priority, entry.normalized_term, entry.entry_id),
    )[:limit]
    entries = tuple(
        TranscriptionHintEntry(
            entry_id=entry.entry_id,
            term=entry.term,
            priority=entry.priority,
        )
        for entry in selected
    )
    canonical = json.dumps(
        {
            "lexicon_ids": lexicon_ids,
            "item_ids": item_ids,
            "selector_version": selector_version,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return TranscriptionHints(
        hint_set_id=derive_id("transcription-hints.v1", canonical),
        lexicon_ids=lexicon_ids,
        item_ids=item_ids,
        selector_version=selector_version,
        entries=entries,
    )


class SqliteRecognitionLexiconProjection:
    """Persist immutable lexicon snapshots and select hints from current exact items."""

    def __init__(
        self,
        db_path: DatabaseSource,
        *,
        store: Store,
        tags: ApprovedTagReader | None = None,
        builder_version: str = LEXICON_BUILDER_VERSION,
    ) -> None:
        self._db = database_from(db_path)
        self._conn = self._db.connection
        self._store = store
        self._tags = tags
        self._builder_version = builder_version

    @property
    def transaction_owner(self) -> LearningDatabase:
        return self._db

    def rebuild_revision(self, revision_id: str) -> RecognitionLexicon:
        revision = self._store.get_revision(revision_id)
        if revision is None:
            raise KeyError(f"ResourceRevision 不存在：{revision_id}")
        current = self._store.current_revision(revision.resource_id)
        if current is None or current.revision_id != revision_id:
            raise ValueError("只允许从当前 ResourceRevision 重建词表")
        items = self._store.items_for_resource(revision.resource_id)
        approved_terms: list[ApprovedRecognitionTerm] = []
        if self._tags is not None:
            for item in items:
                for assignment in self._tags.tags_for_item(item.item_id):
                    if (
                        assignment.review_status != "approved"
                        or assignment.lifecycle_status != "active"
                    ):
                        continue
                    term = self._tags.term(assignment.term_id)
                    if term is None or term.status != "approved":
                        continue
                    for surface in (term.label_zh, term.key, *term.aliases):
                        if _clean_term(surface) is not None:
                            approved_terms.append(
                                ApprovedRecognitionTerm(
                                    item_id=item.item_id,
                                    assignment_id=assignment.assignment_id,
                                    term=surface,
                                )
                            )
        lexicon = build_recognition_lexicon(
            revision=revision,
            nodes=self._store.document_nodes(
                revision.resource_id,
                revision_id=revision.revision_id,
            ),
            items=items,
            approved_terms=approved_terms,
            builder_version=self._builder_version,
        )
        payload = json.dumps(
            lexicon.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._conn.execute(
            "SELECT payload FROM recognition_lexicons WHERE lexicon_id = ?",
            (lexicon.lexicon_id,),
        ).fetchone()
        if existing is not None and str(existing[0]) != payload:
            raise RuntimeError("相同 lexicon_id 对应了不同的不可变投影")
        try:
            self._conn.execute(
                "INSERT INTO recognition_lexicons "
                "(lexicon_id, revision_id, builder_version, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(lexicon_id) DO NOTHING",
                (
                    lexicon.lexicon_id,
                    lexicon.revision_id,
                    lexicon.builder_version,
                    payload,
                ),
            )
            self._conn.execute(
                "INSERT INTO recognition_lexicon_current (revision_id, lexicon_id) VALUES (?, ?) "
                "ON CONFLICT(revision_id) DO UPDATE SET lexicon_id=excluded.lexicon_id",
                (lexicon.revision_id, lexicon.lexicon_id),
            )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - defensive schema mapping
            raise RuntimeError("RecognitionLexicon 持久化违反引用完整性") from exc
        self._db.commit()
        return lexicon

    def current_for_revision(self, revision_id: str) -> RecognitionLexicon | None:
        row = self._conn.execute(
            "SELECT l.payload FROM recognition_lexicon_current AS c "
            "JOIN recognition_lexicons AS l ON l.lexicon_id = c.lexicon_id "
            "WHERE c.revision_id = ?",
            (revision_id,),
        ).fetchone()
        return None if row is None else RecognitionLexicon.model_validate_json(str(row[0]))

    def get(self, lexicon_id: str) -> RecognitionLexicon | None:
        row = self._conn.execute(
            "SELECT payload FROM recognition_lexicons WHERE lexicon_id = ?",
            (lexicon_id,),
        ).fetchone()
        return None if row is None else RecognitionLexicon.model_validate_json(str(row[0]))

    def select_for_items(
        self,
        item_ids: Sequence[str],
        *,
        limit: int = MAX_TRANSCRIPTION_HINTS,
    ) -> TranscriptionHints:
        items: list[KnowledgeItem] = []
        revision_ids: set[str] = set()
        for item_id in item_ids:
            item = self._store.get_item(item_id)
            if item is None:
                raise KeyError(f"KnowledgeItem 不存在：{item_id}")
            items.append(item)
            revisions = {
                evidence.locator.revision_id
                for evidence in item.evidence
                if isinstance(evidence.locator, EvidenceLocator)
            }
            if len(revisions) != 1:
                raise ValueError(f"KnowledgeItem 必须精确绑定一个 revision：{item_id}")
            revision_ids.update(revisions)
        lexicons = [self.rebuild_revision(revision_id) for revision_id in sorted(revision_ids)]
        return select_transcription_hints(lexicons=lexicons, items=items, limit=limit)

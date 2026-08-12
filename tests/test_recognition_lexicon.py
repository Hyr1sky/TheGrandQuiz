"""RecognitionLexicon / TranscriptionHints public seam tests."""

import hashlib
from pathlib import Path

import pytest

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.classification import TagAssignmentV1, VocabularyTermView
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.ingest.pipeline import PreparedIngest, persist_prepared_ingest
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningDatabase
from grandquiz.domain.learning.recognition_lexicon import (
    ApprovedRecognitionTerm,
    SqliteRecognitionLexiconProjection,
    build_recognition_lexicon,
    select_transcription_hints,
)
from grandquiz.domain.learning.store import SqliteLearningStore


def _document(content: str):
    resource = LearningResource.create(url="https://example.com/agent").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    return snapshot


def test_build_recognition_lexicon_is_stable_and_preserves_technical_spelling() -> None:
    document = _document(
        "# ReAct\n\n推理与工具调用交替进行。\n\n```python\nclass AgentEvent:\n    pass\n```\n"
    )
    item = KnowledgeItem.create(
        resource_id=document.revision.resource_id,
        concept="ReAct 推理循环",
        summary="AgentEvent 封装每一步运行事件。",
        evidence=[Evidence(quote="推理与工具调用交替进行。")],
        confidence=0.9,
    )

    first = build_recognition_lexicon(
        revision=document.revision,
        nodes=document.nodes,
        items=[item],
    )
    second = build_recognition_lexicon(
        revision=document.revision,
        nodes=document.nodes,
        items=[item],
    )

    assert first == second
    assert first.lexicon_id == second.lexicon_id
    terms = {entry.term for entry in first.entries}
    assert {"ReAct", "AgentEvent", "ReAct 推理循环"} <= terms
    assert len({entry.normalized_term for entry in first.entries}) == len(first.entries)


def test_select_transcription_hints_uses_only_current_item_sources_and_is_bounded() -> None:
    document = _document("# Agent\n\nReAct 与 React 是不同的术语。\n")
    items = ground_items(
        document,
        [
            KnowledgeItem.create(
                resource_id=document.revision.resource_id,
                concept="ReAct",
                summary="推理与行动循环。",
                evidence=[Evidence(quote="ReAct 与 React 是不同的术语。")],
                confidence=0.9,
            ),
            KnowledgeItem.create(
                resource_id=document.revision.resource_id,
                concept="React",
                summary="前端界面库。",
                evidence=[Evidence(quote="ReAct 与 React 是不同的术语。")],
                confidence=0.9,
            ),
        ],
    )
    lexicon = build_recognition_lexicon(
        revision=document.revision,
        nodes=document.nodes,
        items=items,
    )

    hints = select_transcription_hints(
        lexicons=[lexicon],
        items=[items[0]],
        limit=1,
    )

    assert hints.item_ids == (items[0].item_id,)
    assert hints.lexicon_ids == (lexicon.lexicon_id,)
    assert [entry.term for entry in hints.entries] == ["ReAct"]
    assert hints == select_transcription_hints(
        lexicons=[lexicon],
        items=[items[0]],
        limit=1,
    )


def test_sqlite_projection_reopens_and_selects_by_exact_item(tmp_path: Path) -> None:
    content = "# ReAct\n\nReAct 让推理与行动交替进行。\n"
    resource = LearningResource.create(url="https://example.com/reopen").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    document = build_document_snapshot(resource)
    assert document is not None
    item = ground_items(
        document,
        [
            KnowledgeItem.create(
                resource_id=resource.resource_id,
                concept="ReAct",
                summary="推理与行动循环。",
                evidence=[Evidence(quote="ReAct 让推理与行动交替进行。")],
                confidence=0.9,
            )
        ],
    )[0]
    database_path = tmp_path / "learning.db"
    database = LearningDatabase(database_path)
    store = SqliteLearningStore(database)
    store.replace_snapshot(resource, [item])
    projection = SqliteRecognitionLexiconProjection(database, store=store)

    lexicon = projection.rebuild_revision(document.revision.revision_id)
    hints = projection.select_for_items([item.item_id])
    database.close()

    reopened_database = LearningDatabase(database_path)
    reopened_store = SqliteLearningStore(reopened_database)
    reopened = SqliteRecognitionLexiconProjection(reopened_database, store=reopened_store)
    assert reopened.current_for_revision(document.revision.revision_id) == lexicon
    assert reopened.select_for_items([item.item_id]) == hints
    with pytest.raises(KeyError, match="KnowledgeItem"):
        reopened.select_for_items(["missing-item"])
    reopened_database.close()


def test_build_recognition_lexicon_includes_only_explicit_approved_terms() -> None:
    document = _document("# 检索方法\n\n按树结构检索。\n")
    item = KnowledgeItem.create(
        resource_id=document.revision.resource_id,
        concept="结构检索",
        summary="逐层定位相关内容。",
        evidence=[Evidence(quote="按树结构检索。")],
        confidence=0.9,
    )

    lexicon = build_recognition_lexicon(
        revision=document.revision,
        nodes=document.nodes,
        items=[item],
        approved_terms=[
            ApprovedRecognitionTerm(
                item_id=item.item_id,
                assignment_id="assignment-1",
                term="PageIndex",
            )
        ],
    )

    entry = next(entry for entry in lexicon.entries if entry.term == "PageIndex")
    assert entry.source_kind == "approved_tag"
    assert entry.source_refs == (f"item:{item.item_id}", "tag-assignment:assignment-1")


class _ApprovedTagReader:
    def __init__(self, item_id: str) -> None:
        self._item_id = item_id

    def tags_for_item(self, item_id: str) -> list[TagAssignmentV1]:
        assert item_id == self._item_id
        return [
            TagAssignmentV1(
                assignment_id="assignment-1",
                item_id=item_id,
                term_id="topic:pageindex",
                revision=1,
                assigned_by="user",
                review_status="approved",
                lifecycle_status="active",
                trace_id="trace-1",
                taxonomy_version="learning-vocabulary.v1",
            )
        ]

    def term(self, term_id: str) -> VocabularyTermView | None:
        assert term_id == "topic:pageindex"
        return VocabularyTermView(
            term_id=term_id,
            namespace="topic",
            key="pageindex",
            label_zh="PageIndex",
            aliases=("Page Index",),
            status="approved",
            taxonomy_version="learning-vocabulary.v1",
        )


def test_projection_rebuild_reads_approved_tag_surfaces(tmp_path: Path) -> None:
    content = "# 检索方法\n\n按树结构检索。\n"
    resource = LearningResource.create(url="https://example.com/tags").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    document = build_document_snapshot(resource)
    assert document is not None
    item = ground_items(
        document,
        [
            KnowledgeItem.create(
                resource_id=resource.resource_id,
                concept="结构检索",
                summary="逐层定位。",
                evidence=[Evidence(quote="按树结构检索。")],
                confidence=0.9,
            )
        ],
    )[0]
    database = LearningDatabase(tmp_path / "learning.db")
    store = SqliteLearningStore(database)
    store.replace_snapshot(resource, [item])
    projection = SqliteRecognitionLexiconProjection(
        database,
        store=store,
        tags=_ApprovedTagReader(item.item_id),
    )

    lexicon = projection.rebuild_revision(document.revision.revision_id)

    assert {entry.term for entry in lexicon.entries} >= {"PageIndex", "Page Index"}
    database.close()


def test_approved_ingest_builds_revision_lexicon_in_the_same_commit(tmp_path: Path) -> None:
    content = "# AgentEvent\n\n事件使用信封封装。\n"
    resource = LearningResource.create(url="https://example.com/ingest-lexicon").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
        }
    )
    document = build_document_snapshot(resource)
    assert document is not None
    item = ground_items(
        document,
        [
            KnowledgeItem.create(
                resource_id=resource.resource_id,
                concept="AgentEvent",
                summary="事件信封。",
                evidence=[Evidence(quote="事件使用信封封装。")],
                confidence=0.9,
            )
        ],
    )[0]
    prepared = PreparedIngest(
        resource=resource,
        candidates=[item],
        revision_id=document.revision.revision_id,
        node_count=len(document.nodes),
        ingest_span_id="ingest-span",
    )
    database = LearningDatabase(tmp_path / "learning.db")
    store = SqliteLearningStore(database)
    projection = SqliteRecognitionLexiconProjection(database, store=store)

    persist_prepared_ingest(
        prepared,
        approved=[item],
        store=store,
        lexicons=projection,
    )

    lexicon = projection.current_for_revision(document.revision.revision_id)
    assert lexicon is not None
    assert "AgentEvent" in {entry.term for entry in lexicon.entries}
    database.close()

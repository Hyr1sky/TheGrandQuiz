"""自然材料问答的有界 grounding workflow（ADR-0008 / GAS-S2）。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from grandquiz.domain.learning.citations import CitationResolutionError, ResolvedCitation
from grandquiz.domain.learning.document_search import (
    DocumentSearch,
    DocumentSearchHit,
    EvidenceNotReadError,
    NodeCitationValidationError,
    NodeReadResult,
    ScopeResolutionError,
    SearchScope,
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.context import HeuristicTokenCounter, TokenCounter
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Provider

GroundedAnswerStatus = Literal[
    "answered",
    "no_evidence",
    "invalid_scope",
    "budget_exhausted",
    "citation_rejected",
]


class GroundedAnswerRequest(BaseModel):
    """一次材料问答的 exact scope 与全部有界预算。"""

    query: str = Field(
        min_length=1,
        description="从用户问题提取的 1–3 个高信息量检索词或短语，不要重复整句问题",
    )
    resource_ids: list[str] = Field(min_length=1)
    max_candidates: int = Field(default=5, ge=1, le=10)
    max_read_chars: int = Field(default=6_000, ge=1, le=12_000)
    max_chars_per_node: int = Field(default=2_000, ge=1, le=4_000)
    max_prompt_tokens: int = Field(default=16_000, ge=256, le=32_000)
    max_attempts: int = Field(default=2, ge=1, le=2)
    context_chars: int = Field(default=240, ge=0, le=2_000)

    @field_validator("query")
    @classmethod
    def _query_is_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query 不能为空")
        return stripped

    @field_validator("resource_ids")
    @classmethod
    def _resource_ids_are_exact(cls, value: list[str]) -> list[str]:
        if any(not resource_id.strip() for resource_id in value):
            raise ValueError("resource_ids 不能包含空值")
        if len(set(value)) != len(value):
            raise ValueError("resource_ids 不能重复")
        return value


class GroundedAnswerMetrics(BaseModel):
    candidate_nodes: int = Field(ge=0)
    read_nodes: int = Field(ge=0)
    read_chars: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    max_prompt_tokens: int = Field(ge=0)


class GroundedAnswerResult(BaseModel):
    status: GroundedAnswerStatus
    answer: str | None = None
    citations: list[ResolvedCitation]
    searched_node_ids: list[str]
    read_node_ids: list[str]
    resource_ids: list[str]
    metrics: GroundedAnswerMetrics
    detail: str | None = None


class _CitationCandidate(BaseModel):
    node_key: str = Field(min_length=1)
    quote: str = Field(min_length=1)

    @field_validator("node_key", "quote")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped


class _AnswerCandidate(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[_CitationCandidate] = Field(max_length=3)

    @field_validator("answer")
    @classmethod
    def _answer_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("answer 不能为空")
        return stripped

    @model_validator(mode="after")
    def _citations_are_distinct(self) -> _AnswerCandidate:
        keys = [(citation.node_key, citation.quote) for citation in self.citations]
        if len(keys) != len(set(keys)):
            raise ValueError("citations 不能重复")
        return self


@dataclass(frozen=True)
class _ReadWindow:
    key: str
    result: NodeReadResult


class GroundedDocumentAnswer:
    """把搜索、读取、结构化回答与逐字 citation 隐藏在一个公共入口后。"""

    def __init__(
        self,
        *,
        store: Store,
        provider: Provider,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._token_counter = token_counter or HeuristicTokenCounter()
        self._prompt = load_prompt("grounded_document_answer")

    async def answer(
        self,
        request: GroundedAnswerRequest,
        *,
        emitter: EventEmitter,
        parent_span_id: str | None = None,
    ) -> GroundedAnswerResult:
        workflow_span = emitter.new_span_id()
        emitter.emit(
            LearningEvent.GROUNDED_ANSWER_STARTED,
            span_id=workflow_span,
            parent_span_id=parent_span_id,
            payload={"query": request.query, "resource_ids": request.resource_ids},
        )
        search = DocumentSearch(self._store, turn_read_budget=request.max_read_chars)
        scope = SearchScope(mode="selected", resource_ids=request.resource_ids)
        try:
            hits, queries_attempted = self._search_candidates(search, request, scope)
        except ScopeResolutionError as exc:
            emitter.emit(
                LearningEvent.DOCUMENT_SEARCH_REJECTED,
                parent_span_id=workflow_span,
                payload={
                    "query": request.query,
                    "scope": scope.model_dump(),
                    "unresolved_resource_ids": exc.unresolved_resource_ids,
                },
            )
            return self._finish(
                emitter,
                workflow_span,
                request,
                workflow_parent_span_id=parent_span_id,
                status="invalid_scope",
                detail=str(exc),
            )
        emitter.emit(
            LearningEvent.DOCUMENT_NODES_SEARCHED,
            parent_span_id=workflow_span,
            payload={
                "query": request.query,
                "scope": scope.model_dump(),
                "limit": request.max_candidates,
                "queries_attempted": queries_attempted,
                "candidate_node_ids": [hit.node_id for hit in hits],
            },
        )
        leaf_hits = [hit for hit in hits if hit.kind not in {"document", "section"}]
        readable_hits = leaf_hits or hits
        windows = self._read_candidates(
            search,
            hits=[(hit.resource_id, hit.node_id) for hit in readable_hits],
            request=request,
            emitter=emitter,
            parent_span_id=workflow_span,
        )
        if not windows:
            return self._finish(
                emitter,
                workflow_span,
                request,
                workflow_parent_span_id=parent_span_id,
                status="no_evidence",
                searched_node_ids=[hit.node_id for hit in hits],
                detail="稀疏搜索没有返回可读原文证据",
            )

        base_messages = self._messages(request, windows)
        estimated_prompt_tokens = self._estimate_messages(base_messages)
        if estimated_prompt_tokens > request.max_prompt_tokens:
            return self._finish(
                emitter,
                workflow_span,
                request,
                workflow_parent_span_id=parent_span_id,
                status="budget_exhausted",
                searched_node_ids=[hit.node_id for hit in hits],
                windows=windows,
                max_prompt_tokens=estimated_prompt_tokens,
                detail=(
                    f"问答 prompt 估算 {estimated_prompt_tokens} tokens，"
                    f"超过上限 {request.max_prompt_tokens}"
                ),
            )

        retry_note: str | None = None
        model_calls = 0
        prompt_tokens = 0
        completion_tokens = 0
        max_prompt_tokens = 0
        last_error = ""
        for _ in range(request.max_attempts):
            messages = list(base_messages)
            if retry_note is not None:
                messages.append(Message(role="user", content=retry_note))
            completion = await self._call_model(
                messages,
                emitter=emitter,
                parent_span_id=workflow_span,
            )
            model_calls += 1
            prompt_tokens += completion.usage.prompt_tokens
            completion_tokens += completion.usage.completion_tokens
            max_prompt_tokens = max(max_prompt_tokens, completion.usage.prompt_tokens)
            try:
                candidate = self._parse(completion.text)
                if not candidate.citations:
                    return self._finish(
                        emitter,
                        workflow_span,
                        request,
                        workflow_parent_span_id=parent_span_id,
                        status="no_evidence",
                        answer=candidate.answer,
                        searched_node_ids=[hit.node_id for hit in hits],
                        windows=windows,
                        model_calls=model_calls,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        max_prompt_tokens=max_prompt_tokens,
                    )
                citations = self._resolve_citations(
                    candidate,
                    windows=windows,
                    search=search,
                    budget_key=workflow_span,
                    context_chars=request.context_chars,
                )
            except (ValueError, ValidationError) as exc:
                last_error = self._stable_error(exc)
                retry_note = (
                    f"上一次输出无法采用：{last_error}。"
                    "请只引用对应 node_key 已给出的逐字且唯一原文，并返回合法 JSON。"
                )
                continue
            for citation in citations:
                emitter.emit(
                    LearningEvent.CITATION_RESOLVED,
                    parent_span_id=workflow_span,
                    payload={
                        "source": "node_read",
                        "revision_id": citation.revision_id,
                        "node_id": citation.node_id,
                        "start_offset": citation.start_offset,
                        "end_offset": citation.end_offset,
                    },
                )
            return self._finish(
                emitter,
                workflow_span,
                request,
                workflow_parent_span_id=parent_span_id,
                status="answered",
                answer=candidate.answer,
                citations=citations,
                searched_node_ids=[hit.node_id for hit in hits],
                windows=windows,
                model_calls=model_calls,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                max_prompt_tokens=max_prompt_tokens,
            )

        emitter.emit(
            LearningEvent.CITATION_REJECTED,
            parent_span_id=workflow_span,
            payload={
                "source": "grounded_answer",
                "classification": "structured_answer_invalid",
                "error_fingerprint": hashlib.sha256(last_error.encode()).hexdigest(),
            },
        )
        return self._finish(
            emitter,
            workflow_span,
            request,
            workflow_parent_span_id=parent_span_id,
            status="citation_rejected",
            searched_node_ids=[hit.node_id for hit in hits],
            windows=windows,
            model_calls=model_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            max_prompt_tokens=max_prompt_tokens,
            detail=last_error,
        )

    def _read_candidates(
        self,
        search: DocumentSearch,
        *,
        hits: list[tuple[str, str]],
        request: GroundedAnswerRequest,
        emitter: EventEmitter,
        parent_span_id: str,
    ) -> list[_ReadWindow]:
        windows: list[_ReadWindow] = []
        seen: set[str] = set()
        remaining = request.max_read_chars
        for resource_id, node_id in hits:
            if node_id in seen or remaining < 1:
                continue
            seen.add(node_id)
            read = search.read_node(
                resource_id,
                node_id,
                max_chars=min(request.max_chars_per_node, remaining),
                budget_key=parent_span_id,
            )
            remaining -= len(read.content)
            windows.append(_ReadWindow(key=f"n{len(windows)}", result=read))
            emitter.emit(
                LearningEvent.DOCUMENT_NODE_READ,
                parent_span_id=parent_span_id,
                payload={
                    "resource_id": read.resource_id,
                    "revision_id": read.revision_id,
                    "node_id": read.node_id,
                    "start_offset": read.start_offset,
                    "end_offset": read.end_offset,
                    "chars": len(read.content),
                    "budget_used": read.budget_used,
                    "budget_limit": read.budget_limit,
                    "ok": True,
                },
            )
        return windows

    @staticmethod
    def _search_candidates(
        search: DocumentSearch,
        request: GroundedAnswerRequest,
        scope: SearchScope,
    ) -> tuple[list[DocumentSearchHit], list[str]]:
        """全短语无命中时，在同一 exact scope 内按调用者给出的短语确定性放宽。"""
        queries = [request.query]
        hits = search.search(request.query, scope=scope, limit=request.max_candidates)
        if hits:
            return hits, queries
        phrases = [
            phrase for phrase in re.findall(r"[^\s,，。；;：:！？!?]+", request.query) if phrase
        ]
        if len(phrases) < 2:
            return [], queries
        merged: list[DocumentSearchHit] = []
        seen: set[tuple[str, str]] = set()
        for phrase in phrases:
            queries.append(phrase)
            for hit in search.search(phrase, scope=scope, limit=request.max_candidates):
                key = (hit.resource_id, hit.node_id)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(hit)
                if len(merged) == request.max_candidates:
                    return merged, queries
        return merged, queries

    def _messages(
        self, request: GroundedAnswerRequest, windows: list[_ReadWindow]
    ) -> list[Message]:
        payload = {
            "query": request.query,
            "exact_resource_ids": request.resource_ids,
            "untrusted_evidence_windows": [
                {
                    "node_key": window.key,
                    "resource_id": window.result.resource_id,
                    "section_path": window.result.section_path,
                    "content": window.result.content,
                }
                for window in windows
            ],
        }
        return [
            Message(role="system", content=self._prompt.text),
            Message(
                role="user",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        ]

    def _estimate_messages(self, messages: list[Message]) -> int:
        serialized = json.dumps(
            [message.model_dump(exclude_none=True) for message in messages],
            ensure_ascii=False,
            sort_keys=True,
        )
        return self._token_counter.count(serialized)

    async def _call_model(
        self,
        messages: list[Message],
        *,
        emitter: EventEmitter,
        parent_span_id: str,
    ) -> Completion:
        span_id = emitter.new_span_id()
        emitter.emit(
            EventType.MODEL_STARTED,
            span_id=span_id,
            parent_span_id=parent_span_id,
            payload={
                "messages": [message.model_dump() for message in messages],
                "prompt_version": self._prompt.version,
                "role": "basic",
            },
        )
        try:
            completion = await self._provider.complete(messages, role="basic")
        except Exception as exc:
            emitter.emit(
                EventType.MODEL_ENDED,
                span_id=span_id,
                parent_span_id=parent_span_id,
                payload={"ok": False, "error": repr(exc)},
            )
            raise
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=span_id,
            parent_span_id=parent_span_id,
            payload={
                "ok": True,
                "output": completion.text,
                "usage": completion.usage.model_dump(),
            },
        )
        return completion

    @staticmethod
    def _parse(text: str) -> _AnswerCandidate:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("输出不是合法 JSON") from exc
        return _AnswerCandidate.model_validate(value)

    @staticmethod
    def _resolve_citations(
        candidate: _AnswerCandidate,
        *,
        windows: list[_ReadWindow],
        search: DocumentSearch,
        budget_key: str,
        context_chars: int,
    ) -> list[ResolvedCitation]:
        by_key = {window.key: window for window in windows}
        resolved: list[ResolvedCitation] = []
        for proposed in candidate.citations:
            window = by_key.get(proposed.node_key)
            if window is None:
                raise ValueError(f"未知 node_key：{proposed.node_key}")
            if window.result.content.count(proposed.quote) != 1:
                raise ValueError("quote 必须在对应已读窗口中逐字且唯一出现")
            start = window.result.content.index(proposed.quote)
            try:
                citation = search.cite_node(
                    window.result.resource_id,
                    window.result.node_id,
                    start=start,
                    end=start + len(proposed.quote),
                    quote=proposed.quote,
                    budget_key=budget_key,
                    context_chars=context_chars,
                )
            except (
                EvidenceNotReadError,
                NodeCitationValidationError,
                CitationResolutionError,
            ) as exc:
                raise ValueError(str(exc)) from exc
            resolved.append(citation)
        return resolved

    @staticmethod
    def _stable_error(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
                for error in exc.errors()
            )
        return str(exc)

    @staticmethod
    def _finish(
        emitter: EventEmitter,
        workflow_span: str,
        request: GroundedAnswerRequest,
        *,
        workflow_parent_span_id: str | None,
        status: GroundedAnswerStatus,
        answer: str | None = None,
        citations: list[ResolvedCitation] | None = None,
        searched_node_ids: list[str] | None = None,
        windows: list[_ReadWindow] | None = None,
        model_calls: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        max_prompt_tokens: int = 0,
        detail: str | None = None,
    ) -> GroundedAnswerResult:
        read_windows = windows or []
        metrics = GroundedAnswerMetrics(
            candidate_nodes=len(searched_node_ids or []),
            read_nodes=len(read_windows),
            read_chars=sum(len(window.result.content) for window in read_windows),
            model_calls=model_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            max_prompt_tokens=max_prompt_tokens,
        )
        result = GroundedAnswerResult(
            status=status,
            answer=answer,
            citations=citations or [],
            searched_node_ids=searched_node_ids or [],
            read_node_ids=[window.result.node_id for window in read_windows],
            resource_ids=request.resource_ids,
            metrics=metrics,
            detail=detail,
        )
        emitter.emit(
            LearningEvent.GROUNDED_ANSWER_ENDED,
            span_id=workflow_span,
            parent_span_id=workflow_parent_span_id,
            payload={
                "ok": status == "answered",
                "status": status,
                "resource_ids": request.resource_ids,
                "citation_count": len(result.citations),
                "metrics": metrics.model_dump(),
            },
        )
        return result

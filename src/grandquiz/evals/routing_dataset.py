"""Read public LLMRouterBench result documents into routing evidence.

This is an offline dataset reader, not a Provider protocol adapter.  It imports only
the precomputed score and resource facts needed by the generic routing evaluator.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from grandquiz.evals.routing import (
    RoutingCandidateOutcome,
    RoutingCase,
    RoutingDataset,
    RoutingPartition,
    RoutingRequest,
)


class RoutingDatasetReadError(ValueError):
    """External routing evidence is malformed, drifted, or not fully paired."""


class LLMRouterBenchResultDocument(BaseModel):
    """One result JSON plus path metadata from the public benchmark layout."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["llmrouterbench-result-document.v1"] = (
        "llmrouterbench-result-document.v1"
    )
    candidate_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    source_split: str = Field(min_length=1, max_length=128)
    source_revision: str = Field(min_length=1, max_length=256)
    partition: RoutingPartition
    json_text: str = Field(min_length=2, repr=False)


class _PublishedRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    index: int | str
    prompt: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)


class _PublishedResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    counts: int = Field(ge=0)
    records: tuple[_PublishedRecord, ...]

    @model_validator(mode="after")
    def _count_and_identity_match(self) -> Self:
        if self.counts != len(self.records):
            raise ValueError("published result count does not match records")
        identities = [str(record.index) for record in self.records]
        if len(identities) != len(set(identities)):
            raise ValueError("published record indexes must be unique")
        return self


def _parse(document: LLMRouterBenchResultDocument) -> _PublishedResult:
    try:
        return _PublishedResult.model_validate_json(document.json_text)
    except ValidationError as exc:
        raise RoutingDatasetReadError("invalid LLMRouterBench result document") from exc


def read_llmrouterbench_results(
    documents: Sequence[LLMRouterBenchResultDocument],
) -> RoutingDataset:
    """Pair public per-model files without importing predictions or ground truth."""

    if len(documents) < 2:
        raise RoutingDatasetReadError("paired candidate records require at least two documents")

    document_keys = [
        (document.dataset_id, document.source_split, document.candidate_id)
        for document in documents
    ]
    if len(document_keys) != len(set(document_keys)):
        raise RoutingDatasetReadError("duplicate candidate result document")

    parsed = [(document, _parse(document)) for document in documents]
    candidate_ids = tuple(sorted({document.candidate_id for document in documents}))
    grouped: dict[
        tuple[str, str],
        list[tuple[LLMRouterBenchResultDocument, _PublishedResult]],
    ] = defaultdict(list)
    for document, result in parsed:
        grouped[(document.dataset_id, document.source_split)].append((document, result))

    cases: list[RoutingCase] = []
    for (dataset_id, source_split), candidate_documents in sorted(grouped.items()):
        observed_candidates = {document.candidate_id for document, _result in candidate_documents}
        if observed_candidates != set(candidate_ids):
            raise RoutingDatasetReadError("paired candidate records must cover every candidate")
        partitions: set[RoutingPartition] = {
            document.partition for document, _result in candidate_documents
        }
        if len(partitions) != 1:
            raise RoutingDatasetReadError("paired candidate records must share one partition")
        partition = next(iter(partitions))

        records_by_candidate = {
            document.candidate_id: {str(record.index): record for record in result.records}
            for document, result in candidate_documents
        }
        index_sets = [set(records) for records in records_by_candidate.values()]
        if not index_sets or any(indexes != index_sets[0] for indexes in index_sets[1:]):
            raise RoutingDatasetReadError("paired candidate records must use identical indexes")

        for record_index in sorted(index_sets[0]):
            records = {
                candidate_id: records_by_candidate[candidate_id][record_index]
                for candidate_id in candidate_ids
            }
            prompts = {record.prompt for record in records.values()}
            if len(prompts) != 1:
                raise RoutingDatasetReadError(
                    "paired candidate records require identical request text"
                )
            input_text = next(iter(prompts))
            outcomes = tuple(
                RoutingCandidateOutcome(
                    candidate_id=candidate_id,
                    execution_status="completed",
                    quality_score=records[candidate_id].score,
                    estimated_cost=records[candidate_id].cost,
                    prompt_tokens=records[candidate_id].prompt_tokens,
                    completion_tokens=records[candidate_id].completion_tokens,
                    # Only aggregate time_taken is published; per-case latency is unknown.
                    latency_ms=None,
                )
                for candidate_id in candidate_ids
            )
            cases.append(
                RoutingCase(
                    request=RoutingRequest(
                        case_id=f"{dataset_id}:{source_split}:{record_index}",
                        source_group_id=dataset_id,
                        partition=partition,
                        input_text=input_text,
                    ),
                    outcomes=outcomes,
                )
            )

    try:
        return RoutingDataset(
            source_kind="llmrouterbench",
            source_revisions=tuple(sorted({document.source_revision for document in documents})),
            cost_unit="USD",
            candidate_ids=candidate_ids,
            cases=tuple(sorted(cases, key=lambda case: case.request.case_id)),
        )
    except ValidationError as exc:
        raise RoutingDatasetReadError("invalid paired routing dataset") from exc

"""Provider-neutral offline routing evidence and public benchmark import."""

import json

import pytest
from pydantic import ValidationError

from grandquiz.evals.routing import (
    FixedCandidatePolicy,
    RoutingCandidateOutcome,
    RoutingCase,
    RoutingDataset,
    RoutingPartition,
    RoutingRequest,
    SeededRandomPolicy,
    evaluate_routing_policies,
)
from grandquiz.evals.routing_dataset import (
    LLMRouterBenchResultDocument,
    RoutingDatasetReadError,
    read_llmrouterbench_results,
)


def _result_document(
    candidate_id: str,
    records: list[dict[str, object]],
    *,
    dataset_id: str = "public-toy",
    partition: RoutingPartition = "holdout",
) -> LLMRouterBenchResultDocument:
    return LLMRouterBenchResultDocument(
        candidate_id=candidate_id,
        dataset_id=dataset_id,
        source_split="test",
        source_revision="llmrouterbench-fixture@v1",
        partition=partition,
        json_text=json.dumps(
            {
                "performance": 0.5,
                "time_taken": 9.0,
                "prompt_tokens": 30,
                "completion_tokens": 10,
                "cost": 0.3,
                "counts": len(records),
                "records": records,
            }
        ),
    )


def _record(
    index: int,
    prompt: str,
    *,
    score: float,
    cost: float | None,
    prompt_tokens: int | None = 10,
    completion_tokens: int | None = 2,
) -> dict[str, object]:
    return {
        "index": index,
        "origin_query": prompt,
        "prompt": f"Question: {prompt}\nAnswer:",
        "prediction": "fixture prediction",
        "ground_truth": "fixture answer",
        "score": score,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": cost,
        "raw_output": "fixture raw output",
    }


def test_public_results_are_paired_and_fixed_baselines_keep_dimensions_separate() -> None:
    dataset = read_llmrouterbench_results(
        (
            _result_document(
                "candidate-a",
                [
                    _record(1, "one", score=1.0, cost=0.10),
                    _record(2, "two", score=0.0, cost=0.20),
                ],
            ),
            _result_document(
                "candidate-b",
                [
                    _record(1, "one", score=0.0, cost=0.04),
                    _record(2, "two", score=1.0, cost=0.06),
                ],
            ),
        )
    )

    report = evaluate_routing_policies(
        dataset,
        partition="holdout",
        policies=(
            FixedCandidatePolicy(policy_id="always-a", candidate_id="candidate-a"),
            FixedCandidatePolicy(policy_id="always-b", candidate_id="candidate-b"),
        ),
        include_quality_oracle=True,
    )

    assert dataset.schema_version == "routing-dataset.v1"
    assert dataset.candidate_ids == ("candidate-a", "candidate-b")
    assert len(dataset.content_sha256) == 64
    assert tuple(case.request.case_id for case in dataset.cases) == (
        "public-toy:test:1",
        "public-toy:test:2",
    )
    assert report.schema_version == "routing-evaluation-report.v1"
    assert report.dataset_content_sha256 == dataset.content_sha256
    assert report.dataset_source_kind == "llmrouterbench"
    assert report.dataset_source_revisions == ("llmrouterbench-fixture@v1",)
    assert report.candidate_ids == ("candidate-a", "candidate-b")
    assert report.partition == "holdout"
    assert report.cost_unit == "USD"

    always_a, always_b, oracle = report.policy_summaries
    assert always_a.policy_id == "always-a"
    assert always_a.analysis_only is False
    assert always_a.quality_observation_count == 2
    assert always_a.mean_quality == 0.5
    assert always_a.known_cost_count == 2
    assert always_a.total_known_cost == 0.3
    assert always_a.mean_known_cost == 0.15
    assert always_a.known_prompt_token_count == 2
    assert always_a.total_known_prompt_tokens == 20
    assert always_a.known_completion_token_count == 2
    assert always_a.total_known_completion_tokens == 4
    assert always_a.latency_observation_count == 0
    assert always_a.p50_latency_ms is None
    assert always_a.failure_count == 0
    assert always_a.selection_counts == (("candidate-a", 2),)
    assert len(always_a.policy_fingerprint) == 64

    assert always_b.mean_quality == 0.5
    assert always_b.total_known_cost == 0.1
    assert oracle.policy_id == "analysis:quality-oracle"
    assert oracle.analysis_only is True
    assert oracle.mean_quality == 1.0
    assert oracle.total_known_cost == 0.16
    assert tuple(decision.candidate_id for decision in oracle.decisions) == (
        "candidate-a",
        "candidate-b",
    )


def test_unknown_failure_cost_is_not_rewritten_as_zero_or_semantic_loss() -> None:
    dataset = RoutingDataset(
        source_kind="projected-provider-evidence",
        source_revisions=("provider-evidence@v1",),
        cost_unit="USD",
        candidate_ids=("failed", "healthy"),
        cases=(
            RoutingCase(
                request=RoutingRequest(
                    case_id="case-1",
                    source_group_id="source-1",
                    partition="holdout",
                    input_text="opaque request",
                ),
                outcomes=(
                    RoutingCandidateOutcome(
                        candidate_id="healthy",
                        execution_status="completed",
                        quality_score=0.75,
                        estimated_cost=0.02,
                        latency_ms=20.0,
                    ),
                    RoutingCandidateOutcome(
                        candidate_id="failed",
                        execution_status="provider_error",
                        failure_category="rate_limit",
                    ),
                ),
            ),
        ),
    )

    report = evaluate_routing_policies(
        dataset,
        partition="holdout",
        policies=(FixedCandidatePolicy(policy_id="always-failed", candidate_id="failed"),),
    )
    summary = report.policy_summaries[0]

    assert summary.completed_count == 0
    assert summary.failure_count == 1
    assert summary.failure_counts == (("rate_limit", 1),)
    assert summary.quality_observation_count == 0
    assert summary.mean_quality is None
    assert summary.known_cost_count == 0
    assert summary.total_known_cost is None
    assert summary.mean_known_cost is None
    assert summary.decisions[0].quality_score is None
    assert summary.decisions[0].estimated_cost is None


def test_dataset_rejects_source_group_leakage_between_development_and_holdout() -> None:
    outcomes = (
        RoutingCandidateOutcome(
            candidate_id="a",
            execution_status="completed",
            quality_score=1.0,
        ),
        RoutingCandidateOutcome(
            candidate_id="b",
            execution_status="completed",
            quality_score=0.0,
        ),
    )

    with pytest.raises(ValidationError, match="source group cannot cross partitions"):
        RoutingDataset(
            source_kind="fixture",
            source_revisions=("fixture@v1",),
            cost_unit="USD",
            candidate_ids=("a", "b"),
            cases=(
                RoutingCase(
                    request=RoutingRequest(
                        case_id="dev",
                        source_group_id="same-source",
                        partition="development",
                        input_text="first",
                    ),
                    outcomes=outcomes,
                ),
                RoutingCase(
                    request=RoutingRequest(
                        case_id="holdout",
                        source_group_id="same-source",
                        partition="holdout",
                        input_text="second",
                    ),
                    outcomes=outcomes,
                ),
            ),
        )


def test_seeded_random_is_replayable_and_oracle_is_never_a_callable_policy() -> None:
    cases = tuple(
        RoutingCase(
            request=RoutingRequest(
                case_id=f"case-{index}",
                source_group_id=f"source-{index}",
                partition="development",
                input_text=f"request {index}",
            ),
            outcomes=(
                RoutingCandidateOutcome(
                    candidate_id="a",
                    execution_status="completed",
                    quality_score=float(index % 2),
                ),
                RoutingCandidateOutcome(
                    candidate_id="b",
                    execution_status="completed",
                    quality_score=float((index + 1) % 2),
                ),
            ),
        )
        for index in range(12)
    )
    dataset = RoutingDataset(
        source_kind="fixture",
        source_revisions=("fixture@v1",),
        cost_unit="USD",
        candidate_ids=("a", "b"),
        cases=cases,
    )
    policy = SeededRandomPolicy(policy_id="random-42", seed=42)

    first = evaluate_routing_policies(
        dataset,
        partition="development",
        policies=(policy,),
        include_quality_oracle=True,
    )
    second = evaluate_routing_policies(
        dataset,
        partition="development",
        policies=(policy,),
        include_quality_oracle=True,
    )

    assert first == second
    assert first.policy_summaries[0].policy_id == "random-42"
    assert len(first.policy_summaries[0].policy_fingerprint) == 64
    assert first.policy_summaries[0].analysis_only is False
    assert first.policy_summaries[1].policy_id == "analysis:quality-oracle"
    assert first.policy_summaries[1].analysis_only is True
    assert first.policy_summaries[1].mean_quality == 1.0


def test_policy_sees_only_pre_call_facts_and_dataset_hash_is_order_independent() -> None:
    class FeaturePolicy:
        policy_id = "feature-policy@v1"
        policy_fingerprint = "f" * 64

        def choose(
            self,
            request: RoutingRequest,
            candidate_ids: tuple[str, ...],
        ) -> str:
            assert not hasattr(request, "outcomes")
            assert not hasattr(request, "quality_score")
            return candidate_ids[1] if dict(request.features)["large"] else candidate_ids[0]

    first_case = RoutingCase(
        request=RoutingRequest(
            case_id="small",
            source_group_id="small-source",
            partition="development",
            input_text="small request",
            features=(("large", False),),
        ),
        outcomes=(
            RoutingCandidateOutcome(
                candidate_id="a",
                execution_status="completed",
                quality_score=1.0,
            ),
            RoutingCandidateOutcome(
                candidate_id="b",
                execution_status="completed",
                quality_score=0.0,
            ),
        ),
    )
    second_case = RoutingCase(
        request=RoutingRequest(
            case_id="large",
            source_group_id="large-source",
            partition="development",
            input_text="large request",
            features=(("large", True),),
        ),
        outcomes=tuple(reversed(first_case.outcomes)),
    )
    dataset = RoutingDataset(
        source_kind="fixture",
        source_revisions=("fixture@v1",),
        cost_unit="USD",
        candidate_ids=("a", "b"),
        cases=(first_case, second_case),
    )
    reordered = dataset.model_copy(update={"cases": (second_case, first_case)})

    report = evaluate_routing_policies(
        dataset,
        partition="development",
        policies=(FeaturePolicy(),),
    )

    assert dataset.content_sha256 == reordered.content_sha256
    assert tuple(decision.candidate_id for decision in report.policy_summaries[0].decisions) == (
        "b",
        "a",
    )


def test_public_reader_rejects_unpaired_or_drifted_requests() -> None:
    with pytest.raises(RoutingDatasetReadError, match="paired candidate records"):
        read_llmrouterbench_results(
            (
                _result_document("a", [_record(1, "same", score=1.0, cost=0.1)]),
                _result_document(
                    "b",
                    [
                        _record(1, "same", score=1.0, cost=0.1),
                        _record(2, "extra", score=1.0, cost=0.1),
                    ],
                ),
            )
        )

    with pytest.raises(RoutingDatasetReadError, match="identical request text"):
        read_llmrouterbench_results(
            (
                _result_document("a", [_record(1, "first", score=1.0, cost=0.1)]),
                _result_document("b", [_record(1, "changed", score=1.0, cost=0.1)]),
            )
        )

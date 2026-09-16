"""Bind one HITL Yes decision and materialize development routing baselines."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from grandquiz.evals.routing import (
    FixedCandidatePolicy,
    SeededRandomPolicy,
    evaluate_routing_policies,
)
from grandquiz.evals.summarization_pairwise import (
    SummarizationJudgementCollection,
    SummarizationJudgePlan,
    SummarizationReviewPack,
    approve_summarization_review_pack,
)
from grandquiz.evals.summarization_routing import (
    SummarizationPilotCollection,
    SummarizationPilotPlan,
)
from grandquiz.evals.summarization_routing_evidence import (
    materialize_summarization_routing_dataset,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, required=True)
    parser.add_argument("--judging-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--approved-review-pack-hash", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--random-seed", type=int, default=42)
    return parser


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = _parser().parse_args()
    pilot = SummarizationPilotPlan.model_validate_json(
        (args.pilot_dir / "plan.json").read_text(encoding="utf-8")
    )
    collection = SummarizationPilotCollection.model_validate_json(
        (args.pilot_dir / "collection.json").read_text(encoding="utf-8")
    )
    judge_plan = SummarizationJudgePlan.model_validate_json(
        (args.judging_dir / "judge-plan.json").read_text(encoding="utf-8")
    )
    judgements = SummarizationJudgementCollection.model_validate_json(
        (args.judging_dir / "judgements.json").read_text(encoding="utf-8")
    )
    review_pack = SummarizationReviewPack.model_validate_json(
        (args.judging_dir / "review-pack.json").read_text(encoding="utf-8")
    )
    if review_pack.content_sha256 != args.approved_review_pack_hash:
        raise RuntimeError("review pack does not match the explicitly approved hash")

    approval = approve_summarization_review_pack(
        review_pack,
        approved=True,
        approval_id=args.approval_id,
        decided_at=time.time(),
    )
    dataset = materialize_summarization_routing_dataset(
        pilot,
        collection,
        judge_plan,
        judgements,
        review_pack,
        approval=approval,
    )
    fixed_policies = tuple(
        FixedCandidatePolicy(
            policy_id=f"fixed:{candidate_id}",
            candidate_id=candidate_id,
        )
        for candidate_id in dataset.candidate_ids
    )
    report = evaluate_routing_policies(
        dataset,
        partition="development",
        policies=(
            *fixed_policies,
            SeededRandomPolicy(policy_id=f"random:{args.random_seed}", seed=args.random_seed),
        ),
        include_quality_oracle=True,
    )

    args.output_dir.mkdir(parents=True, exist_ok=False)
    _write(args.output_dir / "review-approval.json", approval.model_dump(mode="json"))
    _write(args.output_dir / "routing-dataset.json", dataset.model_dump(mode="json"))
    _write(args.output_dir / "routing-baselines.json", report.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "approval_id": approval.approval_id,
                "review_pack_content_sha256": review_pack.content_sha256,
                "dataset_content_sha256": dataset.content_sha256,
                "case_count": len(dataset.cases),
                "partition": report.partition,
                "policy_summaries": [
                    {
                        "policy_id": summary.policy_id,
                        "analysis_only": summary.analysis_only,
                        "mean_quality": summary.mean_quality,
                        "total_tokens": (
                            (summary.total_known_prompt_tokens or 0)
                            + (summary.total_known_completion_tokens or 0)
                        ),
                        "p50_latency_ms": summary.p50_latency_ms,
                        "p95_latency_ms": summary.p95_latency_ms,
                        "selection_counts": dict(summary.selection_counts),
                    }
                    for summary in report.policy_summaries
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

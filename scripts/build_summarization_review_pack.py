"""Build a local, provider-blind HITL review pack from completed judgements."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from grandquiz.evals.summarization_pairwise import (
    SummarizationJudgementCollection,
    SummarizationJudgePlan,
    build_summarization_review_pack,
    render_summarization_review_markdown,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--judging-dir", type=Path, required=True)
    parser.add_argument("--audit-sample-size", type=int, default=5)
    return parser


def main() -> None:
    args = _parser().parse_args()
    plan = SummarizationJudgePlan.model_validate_json(
        (args.judging_dir / "judge-plan.json").read_text(encoding="utf-8")
    )
    judgements = SummarizationJudgementCollection.model_validate_json(
        (args.judging_dir / "judgements.json").read_text(encoding="utf-8")
    )
    pack = build_summarization_review_pack(
        plan,
        judgements,
        audit_sample_size=args.audit_sample_size,
    )
    internal_path = args.judging_dir / "review-pack.json"
    human_path = args.judging_dir / "review-pack.md"
    if internal_path.exists() or human_path.exists():
        raise RuntimeError("review pack already exists")
    internal_path.write_text(
        pack.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    human_path.write_text(
        render_summarization_review_markdown(pack),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "review_pack_content_sha256": pack.content_sha256,
                "case_count": pack.case_count,
                "disagreement_count": pack.disagreement_count,
                "audit_count": pack.audit_count,
                "proposed_label_counts": dict(
                    sorted(Counter(case.proposed_label for case in pack.cases).items())
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

"""Run one explicitly approved blind dual-judge plan into private artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values

from grandquiz.evals.summarization_pairwise import (
    SummarizationJudgePolicy,
    approve_summarization_judge_plan,
    collect_summarization_judgements,
    compile_summarization_judge_plan,
    resolve_summarization_judge_candidates,
)
from grandquiz.evals.summarization_routing import (
    SummarizationPilotCollection,
    SummarizationPilotPlan,
)
from grandquiz.kernel.clock import SystemClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.models import ModelRuntime, select_model
from grandquiz.providers.profiles import ModelSelection, parse_model_config
from grandquiz.providers.retry import ProviderRetryPolicy, RetryRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot-dir", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--environment-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--approved-plan-hash", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--experiment-token-cap", type=int, default=600_000)
    return parser


def _environment(path: Path) -> dict[str, str]:
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _run(args: argparse.Namespace) -> None:
    pilot = SummarizationPilotPlan.model_validate_json(
        (args.pilot_dir / "plan.json").read_text(encoding="utf-8")
    )
    collection = SummarizationPilotCollection.model_validate_json(
        (args.pilot_dir / "collection.json").read_text(encoding="utf-8")
    )
    configuration = parse_model_config(
        args.model_config.read_text(encoding="utf-8"),
        purposes={"summarization", "eval_quality"},
    )
    execution_retry_policy = ProviderRetryPolicy(max_attempts=1)
    judges = resolve_summarization_judge_candidates(
        configuration,
        ("deepseek", "qwen_summary_candidate"),
        retry_policy=execution_retry_policy,
    )
    plan = compile_summarization_judge_plan(
        pilot,
        collection,
        policy=SummarizationJudgePolicy(
            judges=judges,
            experiment_token_cap=args.experiment_token_cap,
            prior_actual_tokens=collection.known_actual_tokens,
        ),
    )
    if plan.content_sha256 != args.approved_plan_hash:
        raise RuntimeError("compiled judge plan does not match the explicitly approved hash")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    approval = approve_summarization_judge_plan(
        plan,
        approved=True,
        approval_id=args.approval_id,
        decided_at=time.time(),
    )
    _write(args.output_dir / "judge-plan.json", plan.model_dump(mode="json"))
    _write(args.output_dir / "approval.json", approval.model_dump(mode="json"))

    runtime = ModelRuntime.from_configuration(
        configuration,
        environment=_environment(args.environment_file),
        retry_runtime=RetryRuntime.production(
            execution_retry_policy,
            seed=0,
        ),
    )
    event_store = TraceStore(args.output_dir / "judge-trace.db")
    sink = EventSink()
    sink.register_durable(event_store)
    emitter = EventEmitter(
        sink,
        SystemClock(),
        trace_id=f"summarization-judge-{plan.content_sha256[:16]}",
    )
    try:
        judge_models = {
            judge.profile_id: select_model(
                runtime.bindings,
                "eval_quality",
                ModelSelection(profile_id=judge.profile_id),
            )
            for judge in plan.judges
        }
        result = await collect_summarization_judgements(
            plan,
            approval=approval,
            judge_models=judge_models,
            emitter=emitter,
        )
        _write(args.output_dir / "judgements.json", result.model_dump(mode="json"))
        labels = Counter(case.suggested_label for case in result.cases)
        print(
            json.dumps(
                {
                    "plan_content_sha256": plan.content_sha256,
                    "judgements_content_sha256": result.content_sha256,
                    "case_count": len(result.cases),
                    "known_actual_tokens": result.known_actual_tokens,
                    "unknown_usage_count": result.unknown_usage_count,
                    "suggested_label_counts": dict(sorted(labels.items())),
                },
                sort_keys=True,
            )
        )
    finally:
        event_store.close()
        await runtime.aclose()


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()

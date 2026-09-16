"""Collect one explicitly approved summarization routing pilot into private artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import dotenv_values

from grandquiz.evals.summarization_routing import (
    SummarizationPilotPolicy,
    approve_summarization_pilot,
    collect_summarization_pilot,
    compile_summarization_pilot,
    resolve_summarization_pilot_candidates,
)
from grandquiz.kernel.clock import SystemClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.models import ModelRuntime, select_model
from grandquiz.providers.profiles import ModelSelection, parse_model_config
from grandquiz.providers.retry import ProviderRetryPolicy, RetryRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-db", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--environment-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--approved-plan-hash", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--max-total-tokens", type=int, default=600_000)
    return parser


def _environment(path: Path) -> dict[str, str]:
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def _write_new(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def _run(args: argparse.Namespace) -> None:
    environment = _environment(args.environment_file)
    configuration = parse_model_config(
        args.model_config.read_text(encoding="utf-8"),
        purposes={"summarization"},
    )
    candidates = resolve_summarization_pilot_candidates(
        configuration,
        ("deepseek", "qwen_summary_candidate"),
    )
    source_store = TraceStore(args.trace_db)
    try:
        trace_ids = source_store.recent_trace_ids(limit=1_000)
        traces = {trace_id: tuple(source_store.events(trace_id)) for trace_id in trace_ids}
    finally:
        source_store.close()
    plan = compile_summarization_pilot(
        traces,
        policy=SummarizationPilotPolicy(
            candidates=candidates,
            max_total_tokens=args.max_total_tokens,
            max_cases=40,
            max_turns_per_case=5,
            holdout_every=5,
        ),
    )
    if plan.content_sha256 != args.approved_plan_hash:
        raise RuntimeError("compiled plan does not match the explicitly approved hash")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    approval = approve_summarization_pilot(
        plan,
        approved=True,
        approval_id=args.approval_id,
        decided_at=time.time(),
    )
    _write_new(args.output_dir / "plan.json", plan.model_dump(mode="json"))
    _write_new(args.output_dir / "approval.json", approval.model_dump(mode="json"))

    retry_runtime = RetryRuntime.production(
        ProviderRetryPolicy(max_attempts=1),
        seed=0,
    )
    runtime = ModelRuntime.from_configuration(
        configuration,
        environment=environment,
        retry_runtime=retry_runtime,
    )
    event_store = TraceStore(args.output_dir / "collection-trace.db")
    sink = EventSink()
    sink.register_durable(event_store)
    emitter = EventEmitter(
        sink,
        SystemClock(),
        trace_id=f"summarization-pilot-{plan.content_sha256[:16]}",
    )
    try:
        models = {
            candidate.profile_id: select_model(
                runtime.bindings,
                "summarization",
                ModelSelection(profile_id=candidate.profile_id),
            )
            for candidate in plan.candidates
        }
        collection = await collect_summarization_pilot(
            plan,
            approval=approval,
            candidate_models=models,
            emitter=emitter,
        )
        _write_new(
            args.output_dir / "collection.json",
            collection.model_dump(mode="json"),
        )
        print(
            json.dumps(
                {
                    "plan_content_sha256": plan.content_sha256,
                    "collection_content_sha256": collection.content_sha256,
                    "case_count": len(collection.cases),
                    "known_actual_tokens": collection.known_actual_tokens,
                    "unknown_usage_count": collection.unknown_usage_count,
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

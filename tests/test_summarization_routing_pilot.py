"""Real-consumer evidence acquisition for the summarization routing pilot."""

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from grandquiz.evals.summarization_routing import (
    SummarizationPilotApprovalRequired,
    SummarizationPilotBudgetExceeded,
    SummarizationPilotPolicy,
    approve_summarization_pilot,
    collect_summarization_pilot,
    compile_summarization_pilot,
    require_approved_summarization_pilot,
    resolve_summarization_pilot_candidates,
)
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, ToolSpec, Usage
from grandquiz.providers.models import with_identity
from grandquiz.providers.profiles import ModelIdentity, parse_model_config

_MODEL_CONFIG = """
schema_version = "model-config.v1"
default_profile = "deepseek"
[connections.deepseek]
base_url = "https://api.deepseek.example/v1"
api_key_env = "DEEPSEEK_KEY"
[connections.dashscope]
base_url = "https://dashscope.example/compatible-mode/v1"
api_key_env = "DASHSCOPE_KEY"
[profiles.deepseek]
connection = "deepseek"
model = "deepseek-flash"
context_window_tokens = 32000
max_output_tokens = 4096
[profiles.qwen_summary_candidate]
connection = "dashscope"
model = "qwen-flash"
context_window_tokens = 256000
max_output_tokens = 4096
"""


def _policy(*, max_total_tokens: int = 600_000) -> SummarizationPilotPolicy:
    config = parse_model_config(_MODEL_CONFIG, purposes={"summarization"})
    return SummarizationPilotPolicy(
        candidates=resolve_summarization_pilot_candidates(
            config,
            ("deepseek", "qwen_summary_candidate"),
        ),
        max_total_tokens=max_total_tokens,
        max_cases=10,
        max_turns_per_case=5,
        holdout_every=5,
    )


def _turn(
    *,
    trace_id: str,
    span_id: str,
    seq: int,
    user: str,
    assistant: str,
) -> tuple[AgentEvent, AgentEvent]:
    return (
        AgentEvent(
            type=EventType.AGENT_TURN_STARTED,
            seq=seq,
            ts=float(seq),
            trace_id=trace_id,
            span_id=span_id,
            payload={"user_message": user},
        ),
        AgentEvent(
            type=EventType.AGENT_TURN_ENDED,
            seq=seq + 1,
            ts=float(seq + 1),
            trace_id=trace_id,
            span_id=span_id,
            payload={"ok": True, "output": assistant},
        ),
    )


class _SummaryModel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[Message, ...]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        assert tools is None
        self.calls.append(tuple(messages))
        return Completion(
            text=f"{self.name} 摘要",
            usage=Usage(prompt_tokens=10, completion_tokens=5),
        )


def test_trace_inputs_compile_to_a_frozen_approval_bound_pilot() -> None:
    traces = {
        "trace-b": (
            *_turn(
                trace_id="trace-b",
                span_id="b-1",
                seq=0,
                user="第二组问题一",
                assistant="第二组回答一",
            ),
            *_turn(
                trace_id="trace-b",
                span_id="b-2",
                seq=2,
                user="第二组问题二",
                assistant="第二组回答二",
            ),
        ),
        "trace-a": (
            *_turn(
                trace_id="trace-a",
                span_id="a-1",
                seq=0,
                user="第一组问题一",
                assistant="第一组回答一",
            ),
            *_turn(
                trace_id="trace-a",
                span_id="a-2",
                seq=2,
                user="第一组问题二",
                assistant="第一组回答二",
            ),
        ),
    }
    policy = _policy()

    plan = compile_summarization_pilot(traces, policy=policy)
    reordered = compile_summarization_pilot(
        {name: tuple(reversed(events)) for name, events in reversed(tuple(traces.items()))},
        policy=policy,
    )

    assert plan == reordered
    assert plan.schema_version == "summarization-routing-pilot-plan.v1"
    assert plan.consumer == "summarization"
    assert plan.data_scope == "successful_local_chat_turns"
    assert plan.candidate_profile_ids == ("deepseek", "qwen_summary_candidate")
    assert all(len(candidate.configuration_fingerprint) == 64 for candidate in plan.candidates)
    assert plan.prompt_version.startswith("summarize@")
    assert plan.rubric_version == "summarization_quality@v1"
    assert plan.max_attempts_per_candidate == 1
    assert 0 < plan.reserved_total_tokens <= plan.max_total_tokens
    assert plan.max_total_tokens == 600_000
    assert len(plan.cases) == 2
    assert len(plan.content_sha256) == 64
    assert all(len(case.case_id) == 64 for case in plan.cases)
    assert all(len(case.source_group_id) == 64 for case in plan.cases)
    assert all(case.prior_summary == "" for case in plan.cases)
    assert all(len(case.messages) == 4 for case in plan.cases)
    assert "第一组问题一" not in plan.approval_summary.model_dump_json()

    rejected = approve_summarization_pilot(
        plan,
        approved=False,
        approval_id="approval-1",
        decided_at=100.0,
    )
    approved = approve_summarization_pilot(
        plan,
        approved=True,
        approval_id="approval-2",
        decided_at=101.0,
    )
    assert rejected.approved is False
    assert approved.approved is True
    assert approved.plan_content_sha256 == plan.content_sha256


def test_pilot_policy_rejects_budget_above_the_owner_approved_cap() -> None:
    with pytest.raises(ValidationError):
        _policy(max_total_tokens=600_001)


def test_compilation_rejects_a_plan_whose_conservative_reservation_exceeds_budget() -> None:
    with pytest.raises(SummarizationPilotBudgetExceeded):
        compile_summarization_pilot(
            {
                "trace-a": _turn(
                    trace_id="trace-a",
                    span_id="a-1",
                    seq=0,
                    user="问题",
                    assistant="回答",
                )
            },
            policy=_policy(max_total_tokens=1),
        )


def test_only_matching_yes_approval_releases_the_frozen_plan() -> None:
    policy = _policy()
    plan = compile_summarization_pilot(
        {
            "trace-a": _turn(
                trace_id="trace-a",
                span_id="a-1",
                seq=0,
                user="问题",
                assistant="回答",
            )
        },
        policy=policy,
    )

    with pytest.raises(SummarizationPilotApprovalRequired):
        require_approved_summarization_pilot(plan, approval=None)
    with pytest.raises(SummarizationPilotApprovalRequired):
        require_approved_summarization_pilot(
            plan,
            approval=approve_summarization_pilot(
                plan,
                approved=False,
                approval_id="approval-no",
                decided_at=100.0,
            ),
        )

    stale = approve_summarization_pilot(
        plan,
        approved=True,
        approval_id="approval-stale",
        decided_at=101.0,
    ).model_copy(update={"plan_content_sha256": "0" * 64})
    with pytest.raises(SummarizationPilotApprovalRequired):
        require_approved_summarization_pilot(plan, approval=stale)

    approval = approve_summarization_pilot(
        plan,
        approved=True,
        approval_id="approval-yes",
        decided_at=102.0,
    )
    assert require_approved_summarization_pilot(plan, approval=approval) is plan


async def test_collection_reuses_the_real_summarizer_and_cannot_call_before_yes() -> None:
    plan = compile_summarization_pilot(
        {
            "trace-a": _turn(
                trace_id="trace-a",
                span_id="a-1",
                seq=0,
                user="问题",
                assistant="回答",
            )
        },
        policy=_policy(),
    )
    raw_models = {
        candidate.profile_id: _SummaryModel(candidate.profile_id) for candidate in plan.candidates
    }
    models = {
        candidate.profile_id: with_identity(
            raw_models[candidate.profile_id],
            ModelIdentity(
                purpose="summarization",
                selection_source="explicit_profile",
                configuration_fingerprint=candidate.configuration_fingerprint,
                policy_fingerprint="f" * 64,
            ),
        )
        for candidate in plan.candidates
    }
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="pilot-collection")

    with pytest.raises(SummarizationPilotApprovalRequired):
        await collect_summarization_pilot(
            plan,
            approval=None,
            candidate_models=models,
            emitter=emitter,
        )
    assert all(not model.calls for model in raw_models.values())

    approval = approve_summarization_pilot(
        plan,
        approved=True,
        approval_id="approval-yes",
        decided_at=102.0,
    )
    collection = await collect_summarization_pilot(
        plan,
        approval=approval,
        candidate_models=models,
        emitter=emitter,
    )

    first, second = (raw_models[candidate.profile_id] for candidate in plan.candidates)
    assert first.calls == second.calls
    assert collection.plan_content_sha256 == plan.content_sha256
    assert collection.reserved_tokens == plan.reserved_total_tokens
    assert collection.known_actual_tokens == 30
    assert collection.unknown_usage_count == 0
    assert [outcome.output for outcome in collection.cases[0].outcomes] == [
        "deepseek 摘要",
        "qwen_summary_candidate 摘要",
    ]
    assert events[0].type == "eval.summarization_pilot.started"
    assert events[-1].type == "eval.summarization_pilot.ended"

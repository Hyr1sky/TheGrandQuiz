"""Blind, dual-judge labels for the summarization routing pilot."""

import json
from collections.abc import Sequence

import pytest

from grandquiz.evals.summarization_pairwise import (
    SummarizationJudgeApprovalRequired,
    SummarizationJudgePlan,
    SummarizationJudgePolicy,
    approve_summarization_judge_plan,
    approve_summarization_review_pack,
    build_summarization_review_pack,
    collect_summarization_judgements,
    compile_summarization_judge_plan,
    render_summarization_review_markdown,
    resolve_summarization_judge_candidates,
)
from grandquiz.evals.summarization_routing import (
    SummarizationPilotCollection,
    SummarizationPilotPlan,
    SummarizationPilotPolicy,
    approve_summarization_pilot,
    collect_summarization_pilot,
    compile_summarization_pilot,
    resolve_summarization_pilot_candidates,
)
from grandquiz.evals.summarization_routing_evidence import (
    SummarizationRoutingEvidenceApprovalRequired,
    SummarizationRoutingEvidenceError,
    materialize_summarization_routing_dataset,
    snapshot_summarization_routing_subjects,
)
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.providers.base import Completion, Message, ToolSpec, Usage
from grandquiz.providers.models import with_identity
from grandquiz.providers.profiles import ModelConfiguration, ModelIdentity, parse_model_config

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


class _SummaryModel:
    def __init__(self, text: str) -> None:
        self.text = text

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, tools
        return Completion(text=self.text, usage=Usage(prompt_tokens=10, completion_tokens=5))


class _JudgeModel:
    def __init__(self, preferred: str, *, a_score: int = 4, b_score: int = 4) -> None:
        self.preferred = preferred
        self.a_score = a_score
        self.b_score = b_score
        self.calls: list[tuple[Message, ...]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        assert tools is None
        self.calls.append(tuple(messages))
        a_scores = {
            "factual_fidelity": self.a_score,
            "useful_retention": self.a_score,
            "compression_quality": self.a_score,
            "continuation_usefulness": self.a_score,
        }
        b_scores = {
            "factual_fidelity": self.b_score,
            "useful_retention": self.b_score,
            "compression_quality": self.b_score,
            "continuation_usefulness": self.b_score,
        }
        return Completion(
            text=json.dumps(
                {
                    "preferred": self.preferred,
                    "a_scores": a_scores,
                    "b_scores": b_scores,
                    "rationale": "A/B 的关键信息保留存在稳定差异。",
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=20, completion_tokens=10),
        )


def _turn(trace_id: str, index: int) -> tuple[AgentEvent, AgentEvent]:
    span_id = f"{trace_id}-turn"
    return (
        AgentEvent(
            type=EventType.AGENT_TURN_STARTED,
            seq=0,
            ts=0.0,
            trace_id=trace_id,
            span_id=span_id,
            payload={"user_message": f"问题 {index}"},
        ),
        AgentEvent(
            type=EventType.AGENT_TURN_ENDED,
            seq=1,
            ts=1.0,
            trace_id=trace_id,
            span_id=span_id,
            payload={"ok": True, "output": f"回答 {index}"},
        ),
    )


async def _prepare_judge_plan() -> tuple[
    ModelConfiguration,
    SummarizationPilotPlan,
    SummarizationPilotCollection,
    SummarizationJudgePlan,
]:
    config = parse_model_config(_MODEL_CONFIG, purposes={"summarization", "eval_quality"})
    pilot = compile_summarization_pilot(
        {f"trace-{index}": _turn(f"trace-{index}", index) for index in range(8)},
        policy=SummarizationPilotPolicy(
            candidates=resolve_summarization_pilot_candidates(
                config,
                ("deepseek", "qwen_summary_candidate"),
            ),
            max_total_tokens=600_000,
            holdout_every=2,
        ),
    )
    approval = approve_summarization_pilot(
        pilot,
        approved=True,
        approval_id="approved",
        decided_at=1.0,
    )
    generation_text = {
        "deepseek": "保留了决定与未决事项的摘要。",
        "qwen_summary_candidate": "另一份保留决定与未决事项的摘要。",
    }
    generation_models = {
        candidate.profile_id: with_identity(
            _SummaryModel(generation_text[candidate.profile_id]),
            ModelIdentity(
                purpose="summarization",
                selection_source="explicit_profile",
                configuration_fingerprint=candidate.configuration_fingerprint,
                policy_fingerprint="f" * 64,
            ),
        )
        for candidate in pilot.candidates
    }
    collection = await collect_summarization_pilot(
        pilot,
        approval=approval,
        candidate_models=generation_models,
        emitter=EventEmitter(EventSink(), ManualClock(), trace_id="collection"),
    )
    judge_plan = compile_summarization_judge_plan(
        pilot,
        collection,
        policy=SummarizationJudgePolicy(
            judges=resolve_summarization_judge_candidates(
                config,
                ("deepseek", "qwen_summary_candidate"),
            ),
            experiment_token_cap=600_000,
            prior_actual_tokens=collection.known_actual_tokens,
        ),
    )
    return config, pilot, collection, judge_plan


async def test_judge_plan_is_blind_paired_and_keeps_holdout_closed() -> None:
    _config, _pilot, _collection, judge_plan = await _prepare_judge_plan()

    assert judge_plan.cases
    assert judge_plan.partition == "development"
    assert judge_plan.rubric_version == "summarization_quality@v1"
    assert judge_plan.max_attempts_per_judge == 1
    assert judge_plan.prior_actual_tokens + judge_plan.reserved_tokens <= 600_000
    assert len(judge_plan.content_sha256) == 64
    assert all(len(case.assignments) == 2 for case in judge_plan.cases)
    for case in judge_plan.cases:
        first, second = case.assignments
        assert first.candidate_a_profile_id == second.candidate_b_profile_id
        assert first.candidate_b_profile_id == second.candidate_a_profile_id
    assert "问题 0" not in judge_plan.approval_summary.model_dump_json()


async def test_only_yes_runs_blind_judges_and_agreement_becomes_a_suggested_label() -> None:
    _config, _pilot, _collection, plan = await _prepare_judge_plan()
    raw_judges = {
        plan.judges[0].profile_id: _JudgeModel("A"),
        plan.judges[1].profile_id: _JudgeModel("B"),
    }
    judge_models = {
        judge.profile_id: with_identity(
            raw_judges[judge.profile_id],
            ModelIdentity(
                purpose="eval_quality",
                selection_source="explicit_profile",
                configuration_fingerprint=judge.configuration_fingerprint,
                policy_fingerprint="f" * 64,
            ),
        )
        for judge in plan.judges
    }
    emitter = EventEmitter(EventSink(), ManualClock(), trace_id="judge")

    with pytest.raises(SummarizationJudgeApprovalRequired):
        await collect_summarization_judgements(
            plan,
            approval=None,
            judge_models=judge_models,
            emitter=emitter,
        )
    assert all(not judge.calls for judge in raw_judges.values())

    approval = approve_summarization_judge_plan(
        plan,
        approved=True,
        approval_id="judge-yes",
        decided_at=2.0,
    )
    result = await collect_summarization_judgements(
        plan,
        approval=approval,
        judge_models=judge_models,
        emitter=emitter,
    )

    assert result.known_actual_tokens == len(plan.cases) * 2 * 30
    assert result.unknown_usage_count == 0
    assert all(case.suggested_label != "review_required" for case in result.cases)
    assert all(len(case.judgements) == 2 for case in result.cases)
    assert all(
        "deepseek" not in message.content and "qwen_summary_candidate" not in message.content
        for judge in raw_judges.values()
        for call in judge.calls
        for message in call
    )


async def test_hitl_pack_is_auto_built_blind_and_accepts_one_yes_no_decision() -> None:
    _config, _pilot, _collection, plan = await _prepare_judge_plan()
    raw_judges = {judge.profile_id: _JudgeModel("A") for judge in plan.judges}
    judge_models = {
        judge.profile_id: with_identity(
            raw_judges[judge.profile_id],
            ModelIdentity(
                purpose="eval_quality",
                selection_source="explicit_profile",
                configuration_fingerprint=judge.configuration_fingerprint,
                policy_fingerprint="f" * 64,
            ),
        )
        for judge in plan.judges
    }
    approval = approve_summarization_judge_plan(
        plan,
        approved=True,
        approval_id="judge-yes",
        decided_at=2.0,
    )
    judgements = await collect_summarization_judgements(
        plan,
        approval=approval,
        judge_models=judge_models,
        emitter=EventEmitter(EventSink(), ManualClock(), trace_id="judge"),
    )

    pack = build_summarization_review_pack(plan, judgements, audit_sample_size=2)
    rendered = render_summarization_review_markdown(pack)

    assert pack.disagreement_count == len(plan.cases)
    assert pack.case_count == len(plan.cases)
    assert "问题" in rendered
    assert "候选 A" in rendered
    assert "候选 B" in rendered
    assert "deepseek" not in rendered
    assert "qwen_summary_candidate" not in rendered
    assert pack.content_sha256 in rendered

    decision = approve_summarization_review_pack(
        pack,
        approved=True,
        approval_id="human-yes",
        decided_at=3.0,
    )
    assert decision.approved is True
    assert decision.review_pack_content_sha256 == pack.content_sha256


async def test_only_approved_review_pack_materializes_provider_neutral_routing_data() -> None:
    _config, pilot, collection, plan = await _prepare_judge_plan()
    raw_judges: dict[str, _JudgeModel] = {}
    for case_assignment in plan.cases[0].assignments:
        deepseek_is_a = case_assignment.candidate_a_profile_id == "deepseek"
        raw_judges[case_assignment.judge_profile_id] = _JudgeModel(
            "A" if deepseek_is_a else "B",
            a_score=4 if deepseek_is_a else 2,
            b_score=2 if deepseek_is_a else 4,
        )
    judge_models = {
        judge.profile_id: with_identity(
            raw_judges[judge.profile_id],
            ModelIdentity(
                purpose="eval_quality",
                selection_source="explicit_profile",
                configuration_fingerprint=judge.configuration_fingerprint,
                policy_fingerprint="f" * 64,
            ),
        )
        for judge in plan.judges
    }
    judge_approval = approve_summarization_judge_plan(
        plan,
        approved=True,
        approval_id="judge-yes",
        decided_at=2.0,
    )
    judgements = await collect_summarization_judgements(
        plan,
        approval=judge_approval,
        judge_models=judge_models,
        emitter=EventEmitter(EventSink(), ManualClock(), trace_id="judge"),
    )
    pack = build_summarization_review_pack(plan, judgements, audit_sample_size=2)

    rejected = approve_summarization_review_pack(
        pack,
        approved=False,
        approval_id="human-no",
        decided_at=3.0,
    )
    with pytest.raises(SummarizationRoutingEvidenceApprovalRequired):
        materialize_summarization_routing_dataset(
            pilot,
            collection,
            plan,
            judgements,
            pack,
            approval=rejected,
        )
    wrong_pack = rejected.model_copy(
        update={"approved": True, "review_pack_content_sha256": "0" * 64}
    )
    with pytest.raises(SummarizationRoutingEvidenceApprovalRequired):
        materialize_summarization_routing_dataset(
            pilot,
            collection,
            plan,
            judgements,
            pack,
            approval=wrong_pack,
        )

    approved = approve_summarization_review_pack(
        pack,
        approved=True,
        approval_id="human-yes",
        decided_at=4.0,
    )
    tampered_judgements = judgements.model_copy(
        update={"known_actual_tokens": judgements.known_actual_tokens + 1}
    )
    with pytest.raises(SummarizationRoutingEvidenceError):
        materialize_summarization_routing_dataset(
            pilot,
            collection,
            plan,
            tampered_judgements,
            pack,
            approval=approved,
        )
    dataset = materialize_summarization_routing_dataset(
        pilot,
        collection,
        plan,
        judgements,
        pack,
        approval=approved,
    )
    subjects = snapshot_summarization_routing_subjects(pilot, plan)

    assert dataset.source_kind == "summarization-paired-pilot.v1"
    assert len(subjects) == 4
    assert {subject.subject_id for subject in subjects} <= set(dataset.source_revisions)
    assert all(subject.schema_version == "eval-subject.v2" for subject in subjects)
    assert all(
        dict(subject.policies)["transport_attempts"] == "at-most-one" for subject in subjects
    )
    assert all(dict(subject.policies)["fallback"] == "disabled" for subject in subjects)
    assert {dict(subject.policies)["workflow"] for subject in subjects} == {
        "summarization-paired-pilot.v1",
        "summarization-blind-dual-judge.v1",
    }
    assert dataset.candidate_ids == ("deepseek", "qwen_summary_candidate")
    assert len(dataset.cases) == len(plan.cases)
    assert all(case.request.partition == "development" for case in dataset.cases)
    assert all(
        tuple(name for name, _value in case.request.features)
        == (
            "assistant_chars",
            "message_count",
            "source_utf8_bytes",
            "turn_count",
            "user_chars",
        )
        for case in dataset.cases
    )
    for case in dataset.cases:
        outcomes = {outcome.candidate_id: outcome for outcome in case.outcomes}
        deepseek_score = outcomes["deepseek"].quality_score
        qwen_score = outcomes["qwen_summary_candidate"].quality_score
        assert deepseek_score is not None
        assert qwen_score is not None
        observed_scores = sorted(
            (
                deepseek_score,
                qwen_score,
            )
        )
        assert observed_scores == [0.333333333333, 1.0]

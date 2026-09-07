"""Eval Subject Snapshot public contract."""

import pytest

from grandquiz.evals.subject import (
    ProviderIdentity,
    ReplayEvidence,
    snapshot_subject,
    snapshot_subject_v2,
)
from grandquiz.providers.profiles import ModelIdentity


def test_subject_identity_is_canonical_and_replay_evidence_is_distinct() -> None:
    providers = (
        ProviderIdentity(
            role="basic",
            provider="openai-compatible",
            model="deepseek-chat",
            thinking="disabled",
        ),
        ProviderIdentity(
            role="enrich",
            provider="openai-compatible",
            model="qwen-plus",
            thinking="disabled",
        ),
    )
    first = snapshot_subject(
        prompts={"grading_open": "grading-open-v3", "question_mc": "question-mc-v2"},
        providers=providers,
        tool_schemas={"start_quiz": "sha256:tool-a", "grounded_answer": "sha256:tool-b"},
        policies={"budget": "budget-v2", "workflow": "assessment-v5"},
        replay_evidence=(
            ReplayEvidence(
                owner="case14:llm",
                cassette="eval_case14.cassette.json",
                sha256="a" * 64,
            ),
        ),
    )
    reordered = snapshot_subject(
        prompts={"question_mc": "question-mc-v2", "grading_open": "grading-open-v3"},
        providers=tuple(reversed(providers)),
        tool_schemas={"grounded_answer": "sha256:tool-b", "start_quiz": "sha256:tool-a"},
        policies={"workflow": "assessment-v5", "budget": "budget-v2"},
        replay_evidence=(
            ReplayEvidence(
                owner="case14:llm",
                cassette="refreshed-case14.cassette.json",
                sha256="b" * 64,
            ),
        ),
    )
    changed = snapshot_subject(
        prompts={"grading_open": "grading-open-v4", "question_mc": "question-mc-v2"},
        providers=providers,
        tool_schemas={"start_quiz": "sha256:tool-a", "grounded_answer": "sha256:tool-b"},
        policies={"budget": "budget-v2", "workflow": "assessment-v5"},
    )

    assert first.schema_version == "eval-subject.v1"
    assert first.subject_id == "ab4a60c3836ee9a8aaa8d7c31db5405afa6d161aba623569c42b0024c3d00752"
    assert first.subject_id == reordered.subject_id
    assert first.replay_evidence != reordered.replay_evidence
    assert changed.subject_id != first.subject_id
    assert first.prompts == (
        ("grading_open", "grading-open-v3"),
        ("question_mc", "question-mc-v2"),
    )


def test_subject_snapshot_rejects_secret_shaped_facts() -> None:
    with pytest.raises(ValueError, match="secret-bearing subject fact"):
        snapshot_subject(
            prompts={"api_key": "sk-do-not-store"},
            providers=(
                ProviderIdentity(
                    role="basic",
                    provider="openai-compatible",
                    model="deepseek-chat",
                    thinking="disabled",
                ),
            ),
            tool_schemas={},
            policies={},
        )


def test_subject_v2_adds_bound_model_identity_without_rewriting_v1() -> None:
    generation = ModelIdentity(
        purpose="question_generation",
        selection_source="purpose_override",
        configuration_fingerprint="1" * 64,
        policy_fingerprint="2" * 64,
    )
    grading = generation.model_copy(
        update={
            "purpose": "answer_grading",
            "selection_source": "default",
            "configuration_fingerprint": "3" * 64,
        }
    )
    first = snapshot_subject_v2(
        prompts={"question_mc": "question-mc-v2"},
        model_identities=(generation, grading),
        tool_schemas={"start_quiz": "sha256:tool-a"},
        policies={"workflow": "assessment-v5"},
        replay_evidence=(
            ReplayEvidence(owner="new:v3", cassette="new.cassette.json", sha256="a" * 64),
        ),
    )
    reordered = snapshot_subject_v2(
        prompts={"question_mc": "question-mc-v2"},
        model_identities=(grading, generation),
        tool_schemas={"start_quiz": "sha256:tool-a"},
        policies={"workflow": "assessment-v5"},
        replay_evidence=(
            ReplayEvidence(owner="new:v3", cassette="refreshed.json", sha256="b" * 64),
        ),
    )
    changed = snapshot_subject_v2(
        prompts={"question_mc": "question-mc-v2"},
        model_identities=(
            generation.model_copy(update={"configuration_fingerprint": "4" * 64}),
            grading,
        ),
        tool_schemas={"start_quiz": "sha256:tool-a"},
        policies={"workflow": "assessment-v5"},
    )

    assert first.schema_version == "eval-subject.v2"
    assert first.subject_id == reordered.subject_id
    assert first.replay_evidence != reordered.replay_evidence
    assert changed.subject_id != first.subject_id
    assert [identity.purpose for identity in first.model_identities] == [
        "answer_grading",
        "question_generation",
    ]

"""Eval Subject Snapshot public contract."""

import pytest

from grandquiz.evals.subject import ProviderIdentity, ReplayEvidence, snapshot_subject


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

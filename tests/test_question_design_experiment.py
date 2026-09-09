"""Question-design experiment contracts at their public dataset/sampling seams."""

from __future__ import annotations

import pytest

from grandquiz.evals.question_design_experiment import (
    EvidenceAffordanceV0,
    QuestionDesignCandidateV0,
    QuestionDesignSampleV0,
    compile_question_design_dataset,
    select_balanced_candidates,
)


def _sample(
    sample_id: str,
    affordance: EvidenceAffordanceV0,
    *,
    evidence: str = "闭包捕获变量",
) -> QuestionDesignSampleV0:
    return QuestionDesignSampleV0(
        sample_id=sample_id,
        resource_id="resource-a",
        item_id=f"item-{sample_id}",
        concept=f"概念 {sample_id}",
        summary="用于冻结命题设计实验的人工样本。",
        evidence_quotes=(evidence,),
        evidence_affordance=affordance,
        assessment_claim=f"学习者能够解释 {sample_id}",
        requested_format="auto",
        requested_challenge="adaptive",
    )


def test_dataset_identity_is_stable_for_the_same_human_gold() -> None:
    samples = [
        _sample("narrow", "narrow_proposition"),
        _sample("definition", "definition_attributes"),
        _sample("rule", "rule_conditions"),
        _sample("process", "process"),
        _sample("contrast", "contrast_set"),
        _sample("insufficient", "insufficient"),
    ]

    forward = compile_question_design_dataset(samples)
    reversed_order = compile_question_design_dataset(list(reversed(samples)))

    assert forward.dataset_id == reversed_order.dataset_id
    assert [sample.sample_id for sample in forward.samples] == [
        "contrast",
        "definition",
        "insufficient",
        "narrow",
        "process",
        "rule",
    ]
    assert forward.stratum_counts == {
        "contrast_set": 1,
        "definition_attributes": 1,
        "insufficient": 1,
        "narrow_proposition": 1,
        "process": 1,
        "rule_conditions": 1,
    }


def test_dataset_identity_changes_when_the_frozen_evidence_changes() -> None:
    original = compile_question_design_dataset(
        [_sample("narrow", "narrow_proposition", evidence="向量库和 Markdown 不是二选一")],
        require_complete_strata=False,
    )
    changed = compile_question_design_dataset(
        [_sample("narrow", "narrow_proposition", evidence="向量库和 Markdown 可以组合使用")],
        require_complete_strata=False,
    )

    assert original.dataset_id != changed.dataset_id


def test_dataset_rejects_duplicate_identity_and_missing_preregistered_strata() -> None:
    duplicate = _sample("same", "narrow_proposition")
    with pytest.raises(ValueError, match="sample_id 重复"):
        compile_question_design_dataset([duplicate, duplicate], require_complete_strata=False)

    with pytest.raises(ValueError, match="缺少预注册 Evidence 分层"):
        compile_question_design_dataset([_sample("only", "narrow_proposition")])


def test_dataset_enforces_preregistered_real_sample_and_resource_floor() -> None:
    samples = [
        _sample("narrow", "narrow_proposition"),
        _sample("definition", "definition_attributes"),
        _sample("rule", "rule_conditions"),
        _sample("process", "process"),
        _sample("contrast", "contrast_set"),
        _sample("insufficient", "insufficient"),
    ]

    with pytest.raises(ValueError, match="样本不足：需要至少 20 个"):
        compile_question_design_dataset(samples, min_samples=20, min_resources=3)

    expanded = [
        sample.model_copy(
            update={"sample_id": f"{sample.sample_id}-{index}", "item_id": f"item-{index}"}
        )
        for index in range(4)
        for sample in samples
    ]
    with pytest.raises(ValueError, match="材料不足：需要至少 3 份"):
        compile_question_design_dataset(expanded, min_samples=20, min_resources=3)


def test_candidate_sampling_is_deterministic_and_balances_resources_and_extent() -> None:
    candidates = [
        QuestionDesignCandidateV0(
            resource_id=resource,
            resource_label=f"材料 {resource}",
            item_id=f"{resource}-{suffix}",
            concept=f"概念 {suffix}",
            summary="候选",
            evidence_quotes=(quote,),
        )
        for resource in ("a", "b", "c")
        for suffix, quote in (
            ("narrow", "短命题"),
            ("medium", "中等长度的证据用于观察任务形态" * 3),
            ("broad", "较长证据包含定义、边界、条件与例子" * 10),
        )
    ]

    selected = select_balanced_candidates(candidates, sample_count=6, min_resources=3)

    assert [candidate.item_id for candidate in selected] == [
        "a-narrow",
        "b-medium",
        "c-broad",
        "a-medium",
        "b-broad",
        "c-narrow",
    ]
    assert {candidate.resource_id for candidate in selected} == {"a", "b", "c"}
    assert {candidate.evidence_extent for candidate in selected} == {
        "narrow",
        "medium",
        "broad",
    }

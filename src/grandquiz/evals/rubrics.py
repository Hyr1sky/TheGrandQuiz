"""Versioned semantic-quality rubric registry, independent from judge execution."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    description: str
    pass_score: int = 3


@dataclass(frozen=True)
class Rubric:
    rubric_id: str
    criteria: tuple[Criterion, ...]


_RUBRICS = {
    "grounded_answer": Rubric(
        rubric_id="grounded_answer",
        criteria=(
            Criterion(
                "semantic_support",
                "candidate 的实质结论是否被 reference 充分支持",
            ),
            Criterion(
                "question_coverage",
                "candidate 是否直接覆盖 question 的主要要求",
            ),
            Criterion(
                "learning_usefulness",
                "candidate 是否清晰、准确且适合作为学习解释",
            ),
        ),
    ),
    "question_quality": Rubric(
        rubric_id="question_quality",
        criteria=(
            Criterion(
                "evidence_support",
                "题目要求的答案是否被 source reference 充分支持",
            ),
            Criterion(
                "demand_alignment",
                "题目措辞与要求的回答形式是否匹配目标学习需求",
            ),
            Criterion(
                "answer_leakage",
                "题干是否避免直接或间接泄露答案；无泄露为高分",
            ),
            Criterion(
                "response_design",
                "选择题干扰项是否唯一可辨，或开放题评分 rubric 是否可用",
            ),
            Criterion(
                "learning_usefulness",
                "回答该题是否能产生有意义且可解释的学习证据",
            ),
        ),
    ),
}


def get_rubric(rubric_id: str) -> Rubric | None:
    """Return one closed, code-reviewed rubric definition."""

    return _RUBRICS.get(rubric_id)

"""一次性判卷澄清的纯领域策略与状态机。

本 Module 不调用 LLM，也不提交学习状态。它只回答两个问题：当前 uncertain 判决是否存在一个值得向
学习者澄清的决定性 missing point；若存在，如何保证补充一次、重判一次后必然停止。生产 workflow
只有在 Development Gold gate 通过后才可接入本 Module。
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from grandquiz.domain.learning.assessment.grading import (
    Verdict,
    VerdictLabel,
    derive_verdict,
)
from grandquiz.domain.learning.assessment.question import QuestionSpec

ClarificationPhase = Literal[
    "awaiting_clarification",
    "ready_to_regrade",
    "resolved",
    "needs_review",
]


class ClarificationStateError(ValueError):
    """澄清命令不符合一次性状态机。"""


class ClarificationRequest(BaseModel):
    """由代码选出的单个、会改变三值的追问目标。"""

    model_config = ConfigDict(frozen=True)

    point_id: str = Field(min_length=1)
    point_description: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    initial_verdict: VerdictLabel
    verdict_if_matched: VerdictLabel


def plan_clarification(
    question: QuestionSpec,
    verdict: Verdict,
) -> ClarificationRequest | None:
    """为 uncertain 开放题选择至多一个会改变代码三值的 missing point。"""

    if verdict.diagnosis != "uncertain":
        return None
    expected_ids = [point.point_id for point in question.expected_points]
    matched_ids = set(verdict.matched_points)
    current = derive_verdict(
        expected_point_ids=expected_ids,
        matched_point_ids=matched_ids,
        critical_point_ids=question.critical_point_ids,
    )
    missing_ids = set(verdict.missing_points)
    points_by_id = {point.point_id: point for point in question.expected_points}
    ordered_missing = [
        *[point_id for point_id in question.critical_point_ids if point_id in missing_ids],
        *[
            point_id
            for point_id in expected_ids
            if point_id in missing_ids and point_id not in question.critical_point_ids
        ],
    ]
    for point_id in ordered_missing:
        upgraded = derive_verdict(
            expected_point_ids=expected_ids,
            matched_point_ids=matched_ids | {point_id},
            critical_point_ids=question.critical_point_ids,
        )
        if upgraded == current:
            continue
        point = points_by_id[point_id]
        return ClarificationRequest(
            point_id=point_id,
            point_description=point.description,
            prompt=f"你能再明确说明“{point.description}”吗？请只补充这一点。",
            initial_verdict=current,
            verdict_if_matched=upgraded,
        )
    return None


class ClarificationFlow(BaseModel):
    """一次补充、一次重判后必然停止的不可变状态机。"""

    model_config = ConfigDict(frozen=True)

    phase: ClarificationPhase
    request: ClarificationRequest
    initial_answer: str = Field(min_length=1)
    supplemental_answer: str | None = Field(default=None, min_length=1)
    final_verdict: Verdict | None = None

    @field_validator("initial_answer", "supplemental_answer")
    @classmethod
    def _answer_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("答案不能为空")
        return normalized

    @model_validator(mode="after")
    def _phase_matches_payload(self) -> Self:
        if self.phase == "awaiting_clarification" and (
            self.supplemental_answer is not None or self.final_verdict is not None
        ):
            raise ValueError("等待澄清时不能已有补充或最终判决")
        if self.phase == "ready_to_regrade" and (
            self.supplemental_answer is None or self.final_verdict is not None
        ):
            raise ValueError("待重判状态必须已有补充且尚无最终判决")
        if self.phase in {"resolved", "needs_review"} and (
            self.supplemental_answer is None or self.final_verdict is None
        ):
            raise ValueError("终态必须保留补充与最终判决")
        return self

    @classmethod
    def start(cls, *, initial_answer: str, request: ClarificationRequest) -> Self:
        return cls(
            phase="awaiting_clarification",
            request=request,
            initial_answer=initial_answer,
        )

    def submit(self, supplemental_answer: str) -> Self:
        if self.phase != "awaiting_clarification":
            raise ClarificationStateError("每道题只允许提交一次澄清回答")
        return type(self)(
            phase="ready_to_regrade",
            request=self.request,
            initial_answer=self.initial_answer,
            supplemental_answer=supplemental_answer,
        )

    @property
    def answer_for_regrade(self) -> str:
        if self.supplemental_answer is None:
            raise ClarificationStateError("学习者尚未提交澄清回答")
        return (
            f"首次回答：\n{self.initial_answer}\n\n"
            f"针对评分点“{self.request.point_description}”的补充：\n"
            f"{self.supplemental_answer}"
        )

    def finish(self, verdict: Verdict) -> Self:
        if self.phase != "ready_to_regrade":
            raise ClarificationStateError("只有待重判状态可以结束澄清")
        phase: ClarificationPhase = (
            "needs_review" if verdict.diagnosis == "uncertain" else "resolved"
        )
        return type(self)(
            phase=phase,
            request=self.request,
            initial_answer=self.initial_answer,
            supplemental_answer=self.supplemental_answer,
            final_verdict=verdict,
        )

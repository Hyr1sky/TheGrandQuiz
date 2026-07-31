"""一次多题考核的确定性题型计划。"""

import logging
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

MAX_ASSESSMENT_ROUNDS = 20
logger = logging.getLogger(__name__)


class QuizSegment(BaseModel):
    """批内一段（SE-S4）：连续 ``count`` 道题共用同一题型意图短语 ``question_type``。

    ``question_type`` 是**用户原话里的题型意图短语**（"选择题" / "简答" / "追问"…），同 ADR-0006 的
    口径——LLM 只抽短语、代码用冻结同义表映射到既有三题型（**不是**最终题型枚举、也不新增第 4
    题型）。
    ``count`` 为该段题数；``<= 0`` 的段在 ``expand_segments`` 里贡献 0 题、被跳过（fail-soft，容
    LLM 抽出 0 / 负）。
    """

    count: int
    question_type: str


# 领域代码使用更明确的新术语；Pydantic schema 保留 QuizSegment 名称，避免无行为变化的工具契约漂移。
QuestionTypeSegment = QuizSegment


class AssessmentPlan(BaseModel):
    """把外部批次描述收敛成每题一个意图的唯一有序序列。"""

    model_config = ConfigDict(frozen=True)

    question_type_intents: tuple[str | None, ...] = Field(
        min_length=1,
        max_length=MAX_ASSESSMENT_ROUNDS,
    )

    @classmethod
    def create(
        cls,
        *,
        rounds: int,
        question_type: str | None,
        segments: list[QuestionTypeSegment] | None = None,
    ) -> Self:
        if segments:
            intents = [
                segment.question_type for segment in segments for _ in range(max(segment.count, 0))
            ]
        else:
            intents = []
        if not intents:
            clamped_rounds = min(max(rounds, 1), MAX_ASSESSMENT_ROUNDS)
            intents = [question_type] * clamped_rounds
        if len(intents) > MAX_ASSESSMENT_ROUNDS:
            logger.warning(
                "考核计划总题数 %d 超上限 %d，截断到前 %d 题",
                len(intents),
                MAX_ASSESSMENT_ROUNDS,
                MAX_ASSESSMENT_ROUNDS,
            )
        return cls(question_type_intents=tuple(intents[:MAX_ASSESSMENT_ROUNDS]))

    @property
    def rounds(self) -> int:
        return len(self.question_type_intents)

    def intent_for(self, round_index: int) -> str | None:
        if round_index < 1 or round_index > self.rounds:
            raise IndexError(f"考核轮次超出计划：{round_index}/{self.rounds}")
        return self.question_type_intents[round_index - 1]

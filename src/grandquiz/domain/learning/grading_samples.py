"""Stable human-labelled grading sample contract shared by learning and Eval."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.question import QuestionSpec

AnswerProvenance = Literal[
    "unassisted_human",
    "assisted_human",
    "model",
    "synthetic_oracle",
]


class GradingCalibrationSample(BaseModel):
    """One explicit human label for a production question and learner answer."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["grading-calibration-sample.v1"] = "grading-calibration-sample.v1"
    sample_id: str = Field(min_length=1)
    question_id: str | None = Field(
        default=None,
        min_length=1,
        exclude_if=lambda value: value is None,
    )
    annotator: str = Field(min_length=1)
    blind_to_model_output: bool
    answer_provenance: AnswerProvenance = "unassisted_human"
    respondent_model: str | None = None
    question: QuestionSpec
    learner_answer: str = Field(min_length=1)
    human_verdict: VerdictLabel
    human_matched_points: list[str]
    human_missing_points: list[str]

    @model_validator(mode="after")
    def _validate_human_point_partition(self) -> "GradingCalibrationSample":
        expected = {point.point_id for point in self.question.expected_points}
        matched = set(self.human_matched_points)
        missing = set(self.human_missing_points)
        if len(matched) != len(self.human_matched_points) or len(missing) != len(
            self.human_missing_points
        ):
            raise ValueError("human point labels must not contain duplicates")
        if matched & missing:
            raise ValueError("a human-labelled point cannot be both matched and missing")
        if matched | missing != expected:
            raise ValueError("human labels must partition every expected point exactly once")
        return self

    @property
    def eligible(self) -> bool:
        return (
            self.answer_provenance == "unassisted_human"
            and self.blind_to_model_output
            and bool(self.annotator.strip())
        )

    @property
    def resolved_question_id(self) -> str:
        """Return the explicit question identity or the legacy sample identity."""

        return self.question_id or self.sample_id

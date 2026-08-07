"""User-initiated, one-shot supplement submitted after an open-answer judgement.

This module deliberately owns only the immutable input contract. It neither predicts
ambiguity nor decides whether clarification is useful: the learner explicitly opens
the appeal, and the existing grader evaluates the stable combined answer.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AppealSubmissionConflict(ValueError):
    """A different command attempted to replace an accepted supplement."""


class AppealSubmission(BaseModel):
    """One immutable supplement plus the original answer it may not overwrite."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(min_length=1)
    original_answer: str = Field(min_length=1)
    supplemental_answer: str = Field(min_length=1)

    @field_validator("request_id", "original_answer")
    @classmethod
    def _required_text_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("supplemental_answer")
    @classmethod
    def _supplement_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("补充说明不能为空")
        return normalized

    @classmethod
    def create(
        cls,
        *,
        request_id: str,
        original_answer: str,
        supplemental_answer: str,
    ) -> Self:
        return cls(
            request_id=request_id,
            original_answer=original_answer,
            supplemental_answer=supplemental_answer,
        )

    @property
    def answer_for_regrade(self) -> str:
        """Return the only representation handed to the existing grading slot."""

        return f"首次回答：\n{self.original_answer}\n\n补充说明：\n{self.supplemental_answer}"

    def accept_retry(self, *, request_id: str, supplemental_answer: str) -> Self:
        """Accept an exact retry; reject any attempt to submit a second supplement."""

        if (
            request_id.strip() == self.request_id
            and supplemental_answer.strip() == self.supplemental_answer
        ):
            return self
        raise AppealSubmissionConflict("当前题目已经提交过补充说明")

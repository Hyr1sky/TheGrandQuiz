"""Shared human-label contracts for semantic quality calibration."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ScoreRange(BaseModel):
    min: int = Field(ge=1, le=4)
    max: int = Field(ge=1, le=4)

    @model_validator(mode="after")
    def _ordered(self) -> ScoreRange:
        if self.min > self.max:
            raise ValueError("score range 的 min 不能大于 max")
        return self


class CalibrationSample(BaseModel):
    sample_id: str = Field(min_length=1)
    rubric_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    expected_scores: dict[str, ScoreRange] = Field(min_length=1)

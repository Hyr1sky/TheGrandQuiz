"""Tier-2 LLM quality judge：版本化 rubric、结构化判定与独立事件流。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from grandquiz.evals.rubrics import Rubric, get_rubric
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.providers.base import Message, Provider, Usage

QUALITY_JUDGE_STARTED = "eval.quality_judge.started"
QUALITY_JUDGE_ENDED = "eval.quality_judge.ended"


class QualityJudgeError(ValueError):
    """有界重试耗尽后仍无法取得可审计质量判定。"""


class QualityRequest(BaseModel):
    rubric_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    candidate: str = Field(min_length=1)
    reference: str = Field(min_length=1)


class CriterionResult(BaseModel):
    criterion_id: str = Field(min_length=1)
    score: int = Field(ge=1, le=4)
    rationale: str = Field(min_length=1)
    candidate_evidence: str = Field(min_length=1)
    reference_evidence: str = Field(min_length=1)


class _ModelVerdict(BaseModel):
    rubric_id: str
    criteria: list[CriterionResult]
    overall_rationale: str = Field(min_length=1)


class QualityEvaluation(BaseModel):
    rubric_id: str
    passed: bool
    criteria: list[CriterionResult]
    overall_rationale: str
    prompt_version: str
    usage: Usage


@dataclass(frozen=True)
class _Prompt:
    text: str
    version: str


def _load_prompt() -> _Prompt:
    path = Path(__file__).parent / "prompts" / "quality_judge.md"
    text = path.read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return _Prompt(text=text, version=f"quality_judge@{digest}")


class QualityJudge:
    """从一个小输入契约完成完整质量评审，并由代码计算通过状态。"""

    def __init__(self, *, provider: Provider, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 至少为 1")
        self._provider = provider
        self._max_attempts = max_attempts
        self._prompt = _load_prompt()

    async def evaluate(
        self,
        request: QualityRequest,
        *,
        emitter: EventEmitter,
        parent_span_id: str | None = None,
    ) -> QualityEvaluation:
        rubric = get_rubric(request.rubric_id)
        if rubric is None:
            raise QualityJudgeError(f"未注册 rubric：{request.rubric_id}")
        workflow_span = emitter.new_span_id()
        emitter.emit(
            QUALITY_JUDGE_STARTED,
            span_id=workflow_span,
            parent_span_id=parent_span_id,
            payload={
                "rubric_id": rubric.rubric_id,
                "criterion_count": len(rubric.criteria),
            },
        )
        base_messages = self._messages(request, rubric)
        verdict: _ModelVerdict | None = None
        prompt_tokens = 0
        completion_tokens = 0
        retry_note: str | None = None
        last_error = ""
        for _ in range(self._max_attempts):
            messages = list(base_messages)
            if retry_note is not None:
                messages.append(Message(role="user", content=retry_note))
            model_span = emitter.new_span_id()
            emitter.emit(
                EventType.MODEL_STARTED,
                span_id=model_span,
                parent_span_id=workflow_span,
                payload={
                    "messages": [message.model_dump() for message in messages],
                    "prompt_version": self._prompt.version,
                    "role": "basic",
                },
            )
            try:
                completion = await self._provider.complete(messages, role="basic")
            except Exception as exc:
                emitter.emit(
                    EventType.MODEL_ENDED,
                    span_id=model_span,
                    parent_span_id=workflow_span,
                    payload={"ok": False, "error": repr(exc)},
                )
                emitter.emit(
                    QUALITY_JUDGE_ENDED,
                    span_id=workflow_span,
                    parent_span_id=parent_span_id,
                    payload={
                        "ok": False,
                        "rubric_id": rubric.rubric_id,
                        "criterion_count": len(rubric.criteria),
                        "classification": "provider_error",
                    },
                )
                raise
            prompt_tokens += completion.usage.prompt_tokens
            completion_tokens += completion.usage.completion_tokens
            emitter.emit(
                EventType.MODEL_ENDED,
                span_id=model_span,
                parent_span_id=workflow_span,
                payload={
                    "ok": True,
                    "output": completion.text,
                    "usage": completion.usage.model_dump(),
                },
            )
            try:
                candidate = _ModelVerdict.model_validate_json(completion.text)
                verdict = self._validate_verdict(candidate, rubric, request)
                break
            except (ValueError, KeyError) as exc:
                last_error = str(exc)
                retry_note = (
                    f"上一次输出无法采用：{last_error}。"
                    "请按原 rubric 返回每个 criterion 恰好一次的合法 JSON。"
                )
        usage = Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        if verdict is None:
            emitter.emit(
                QUALITY_JUDGE_ENDED,
                span_id=workflow_span,
                parent_span_id=parent_span_id,
                payload={
                    "ok": False,
                    "rubric_id": rubric.rubric_id,
                    "criterion_count": len(rubric.criteria),
                    "classification": "structured_verdict_invalid",
                    "error_fingerprint": hashlib.sha256(last_error.encode()).hexdigest(),
                    "usage": usage.model_dump(),
                },
            )
            raise QualityJudgeError(f"quality judge 在 {self._max_attempts} 次尝试后仍无合法输出")
        by_id = {criterion.criterion_id: criterion for criterion in rubric.criteria}
        passed = all(
            result.score >= by_id[result.criterion_id].pass_score for result in verdict.criteria
        )
        evaluation = QualityEvaluation(
            rubric_id=verdict.rubric_id,
            passed=passed,
            criteria=verdict.criteria,
            overall_rationale=verdict.overall_rationale,
            prompt_version=self._prompt.version,
            usage=usage,
        )
        emitter.emit(
            QUALITY_JUDGE_ENDED,
            span_id=workflow_span,
            parent_span_id=parent_span_id,
            payload={
                "ok": True,
                "rubric_id": rubric.rubric_id,
                "criterion_count": len(rubric.criteria),
                "passed": evaluation.passed,
                "usage": usage.model_dump(),
            },
        )
        return evaluation

    @staticmethod
    def _validate_verdict(
        candidate: _ModelVerdict,
        rubric: Rubric,
        request: QualityRequest,
    ) -> _ModelVerdict:
        if candidate.rubric_id != rubric.rubric_id:
            raise ValueError("rubric_id 与请求不一致")
        expected = [criterion.criterion_id for criterion in rubric.criteria]
        actual = [result.criterion_id for result in candidate.criteria]
        if len(actual) != len(set(actual)):
            raise ValueError("criterion_id 不能重复")
        if set(actual) != set(expected):
            raise ValueError("criteria 必须与 rubric 完全一致")
        for result in candidate.criteria:
            if result.candidate_evidence not in request.candidate:
                raise ValueError("candidate_evidence 必须逐字来自 candidate")
            if result.reference_evidence not in request.reference:
                raise ValueError("reference_evidence 必须逐字来自 reference")
        by_id = {result.criterion_id: result for result in candidate.criteria}
        return candidate.model_copy(update={"criteria": [by_id[item] for item in expected]})

    def _messages(self, request: QualityRequest, rubric: Rubric) -> list[Message]:
        payload = {
            "rubric_id": rubric.rubric_id,
            "criteria": [
                {
                    "criterion_id": criterion.criterion_id,
                    "description": criterion.description,
                }
                for criterion in rubric.criteria
            ],
            "question": request.question,
            "candidate": request.candidate,
            "reference": request.reference,
        }
        return [
            Message(role="system", content=self._prompt.text),
            Message(
                role="user",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        ]

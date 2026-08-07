"""逐题考核的进程内 HTTP 会话 owner；领域 workflow 仍由 AssessmentSession 持有。"""

import asyncio
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.assessment.appeal import (
    AppealSubmission,
    AppealSubmissionConflict,
)
from grandquiz.domain.learning.assessment.grading import (
    AssessmentDiagnosisKind,
    Verdict,
    grade_answer,
)
from grandquiz.domain.learning.assessment.plan import AssessmentPlan
from grandquiz.domain.learning.assessment.question import QuestionSpec
from grandquiz.domain.learning.assessment.scope import SelectedScope
from grandquiz.domain.learning.assessment.selection import Focus, apply_scope
from grandquiz.domain.learning.assessment.session import AssessmentSession
from grandquiz.domain.learning.classification import KnowledgeKind
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.knowledge_facets import (
    KnowledgeFacetFilter,
    select_knowledge_facets,
)
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.domain.learning.responder import (
    AnswerSubmissionMetadata,
    Responder,
)
from grandquiz.domain.learning.verdict_corrections import (
    VerdictCorrectionCommand,
    VerdictCorrectionService,
)
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.learning_outbox import publish_pending_learning_facts
from grandquiz.kernel.clock import Clock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider

AssessmentStatus = Literal[
    "preparing",
    "awaiting_answer",
    "grading",
    "judged",
    "completed",
    "refused",
    "failed",
    "cancelled",
]
AssessmentAppealStatus = Literal["available", "grading", "resolved", "failed"]

ASSESSMENT_RUN_STARTED = "web.assessment_run.started"
ASSESSMENT_RUN_ENDED = "web.assessment_run.ended"
_PUBLIC_ASSESSMENT_DIAGNOSES = frozenset(
    {
        "complete",
        "missing_key_point",
        "wrong_focus",
        "concept_confusion",
        "off_topic",
        "uncertain",
        "incorrect_choice",
    }
)


def project_assessment_diagnosis(value: object) -> AssessmentDiagnosisKind | None:
    """把内部事件值投影到有限 Web 契约；未知值按无诊断安全降级。"""
    if isinstance(value, str) and value in _PUBLIC_ASSESSMENT_DIAGNOSES:
        return cast("AssessmentDiagnosisKind", value)
    return None


class AssessmentStartRequest(BaseModel):
    resource_ids: list[str] = Field(min_length=1)
    rounds: int = Field(default=3, ge=1, le=20)
    question_type: str | None = None
    question_type_plan: list[str | None] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )
    focus: Focus = "mixed"
    knowledge_kinds: set[KnowledgeKind] = Field(default_factory=lambda: set[KnowledgeKind]())


class EvidenceRevealRequest(BaseModel):
    interaction: Literal["hover", "click", "keyboard"]


class AnswerSubmissionRequest(BaseModel):
    request_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    input_modality: Literal["text", "voice"] = "text"

    @field_validator("request_id", "answer")
    @classmethod
    def value_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class NextRoundRequest(BaseModel):
    request_id: str = Field(min_length=1)

    @field_validator("request_id")
    @classmethod
    def request_id_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id 不能为空")
        return normalized


class AssessmentAppealRequest(BaseModel):
    request_id: str = Field(min_length=1)
    supplemental_answer: str = Field(min_length=1)

    @field_validator("request_id", "supplemental_answer")
    @classmethod
    def value_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized


class AssessmentCommandConflict(ValueError):
    """同一道题已经被另一个 command 提交，不能再次驱动记账。"""


def _empty_strings() -> set[str]:
    return set()


class AssessmentQuestionView(BaseModel):
    question_id: str
    item_id: str
    text: str
    question_type: str
    options: list[str]
    evidence_revealed: bool
    evidence: list[str]


class AssessmentPointFeedbackView(BaseModel):
    point_id: str
    description: str


class AssessmentJudgementView(BaseModel):
    verdict: str
    reason: str
    diagnosis: AssessmentDiagnosisKind | None = None
    matched_points: list[AssessmentPointFeedbackView] = Field(
        default_factory=list[AssessmentPointFeedbackView]
    )
    missing_points: list[AssessmentPointFeedbackView] = Field(
        default_factory=list[AssessmentPointFeedbackView]
    )
    concept_state: str | None = None
    correct_answer: str | None = None


class AssessmentAppealView(BaseModel):
    status: AssessmentAppealStatus
    supplemental_answer: str | None = None
    original_verdict: str
    final_verdict: str | None = None
    reason: str | None = None


class AssessmentView(BaseModel):
    session_id: str
    trace_id: str
    status: AssessmentStatus
    round_index: int = Field(ge=1)
    rounds: int = Field(ge=1)
    attempt_id: str | None = None
    question: AssessmentQuestionView | None = None
    judgement: AssessmentJudgementView | None = None
    appeal: AssessmentAppealView | None = None
    error: str | None = None


class _WebResponder(Responder):
    """把领域层一次 ``await answer`` 映射成可由后续 HTTP command 完成的 Future。"""

    def __init__(self) -> None:
        self._answer: asyncio.Future[str] | None = None
        self._metadata: AnswerSubmissionMetadata | None = None

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        del prompt, options
        self._answer = asyncio.get_running_loop().create_future()
        return await self._answer

    def cancel(self) -> None:
        if self._answer is not None and not self._answer.done():
            self._answer.cancel()

    def submit(self, answer: str, metadata: AnswerSubmissionMetadata) -> bool:
        if self._answer is None or self._answer.done():
            return False
        self._metadata = metadata
        self._answer.set_result(answer)
        return True

    def last_submission_metadata(self) -> AnswerSubmissionMetadata:
        if self._metadata is None:
            raise RuntimeError("答案尚未提交")
        return self._metadata


@dataclass
class _AssessmentRecord:
    session_id: str
    trace_id: str
    plan: AssessmentPlan
    round_index: int
    focus: Focus
    scope: SelectedScope
    candidate_item_ids: list[str] | None
    responder: _WebResponder
    session: AssessmentSession
    emitter: EventEmitter
    run_span_id: str
    status: AssessmentStatus = "preparing"
    question_id: str | None = None
    item_id: str | None = None
    question_text: str | None = None
    effective_question_type: str | None = None
    options: list[str] | None = None
    evidence: list[str] | None = None
    evidence_revealed: bool = False
    judgement: AssessmentJudgementView | None = None
    question_spec: QuestionSpec | None = None
    original_answer: str | None = None
    grading_language: str | None = None
    appeal_submission: AppealSubmission | None = None
    appeal: AssessmentAppealView | None = None
    answer_request_id: str | None = None
    submitted_answer: str | None = None
    attempt_id: str | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = None
    appeal_task: asyncio.Task[None] | None = None
    next_request_ids: set[str] = field(default_factory=_empty_strings)
    terminal_emitted: bool = False

    def view(self) -> AssessmentView:
        question = None
        if (
            self.question_id is not None
            and self.item_id is not None
            and self.question_text is not None
            and self.effective_question_type is not None
        ):
            question = AssessmentQuestionView(
                question_id=self.question_id,
                item_id=self.item_id,
                text=self.question_text,
                question_type=self.effective_question_type,
                options=list(self.options or []),
                evidence_revealed=self.evidence_revealed,
                evidence=list(self.evidence or []) if self.evidence_revealed else [],
            )
        return AssessmentView(
            session_id=self.session_id,
            trace_id=self.trace_id,
            status=self.status,
            round_index=self.round_index,
            rounds=self.plan.rounds,
            attempt_id=self.attempt_id,
            question=question,
            judgement=self.judgement,
            appeal=self.appeal,
            error=self.error,
        )


class AssessmentManager:
    """把可阻塞的 Responder seam 投影成可查询的一题一步 Web 会话。"""

    def __init__(
        self,
        *,
        persistence: LearningPersistence,
        provider: Provider,
        trace_store: TraceStore,
        clock: Clock,
        trace_observatory: TraceObservatory | None = None,
    ) -> None:
        self._persistence = persistence
        self._store = persistence.store
        self._provider = provider
        self._memory = persistence.memory
        self._asked_questions = persistence.asked_questions
        self._preferences = persistence.preferences
        self._difficulty = persistence.difficulty
        self._learning_facts = persistence.learning_facts
        self._classifications = persistence.classifications
        self._trace_store = trace_store
        self._clock = clock
        self._corrections = VerdictCorrectionService(persistence, clock)
        self._trace_observatory = trace_observatory
        self._records: dict[str, _AssessmentRecord] = {}

    def start(self, request: AssessmentStartRequest) -> AssessmentView:
        session_id = uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        if self._trace_observatory is not None:
            self._trace_observatory.register_trace(trace_id)
        responder = _WebResponder()
        sink = EventSink()
        sink.register_durable(self._trace_store)
        if self._trace_observatory is not None:
            sink.register(self._trace_observatory)
        emitter = EventEmitter(sink, self._clock, trace_id=trace_id)
        run_span_id = emitter.new_span_id()
        session = AssessmentSession(
            store=self._store,
            provider=self._provider,
            responder=responder,
            memory=self._memory,
            asked_questions=self._asked_questions,
            preferences=self._preferences,
            difficulty=self._difficulty,
            learning_facts=self._learning_facts,
        )
        plan = (
            AssessmentPlan(question_type_intents=tuple(request.question_type_plan))
            if request.question_type_plan is not None
            else AssessmentPlan.create(
                rounds=request.rounds,
                question_type=request.question_type,
            )
        )
        scoped_items = apply_scope(self._store.all_items(), request.resource_ids)
        frozen_item_ids: list[str] | None = None
        if request.knowledge_kinds:
            frozen_item_ids = list(
                select_knowledge_facets(
                    scoped_items,
                    classifications=self._classifications,
                    facet_filter=KnowledgeFacetFilter(
                        primary_kinds=frozenset(request.knowledge_kinds),
                    ),
                ).item_ids
            )
        record = _AssessmentRecord(
            session_id=session_id,
            trace_id=trace_id,
            plan=plan,
            round_index=1,
            focus=request.focus,
            scope=SelectedScope(resource_ids=request.resource_ids),
            candidate_item_ids=frozen_item_ids,
            responder=responder,
            session=session,
            emitter=emitter,
            run_span_id=run_span_id,
        )
        sink.subscribe(lambda event: self._project_event(record, event))
        self._records[session_id] = record
        emitter.emit(
            ASSESSMENT_RUN_STARTED,
            span_id=run_span_id,
            payload={
                "status": "running",
                "rounds": plan.rounds,
                "question_type_plan": list(plan.question_type_intents),
                "knowledge_kinds": sorted(request.knowledge_kinds),
                "facet_match_count": None if frozen_item_ids is None else len(frozen_item_ids),
            },
        )
        record.task = asyncio.create_task(
            self._run_round(record),
            name=f"grandquiz-api-assessment:{session_id}:1",
        )
        return record.view()

    def get(self, session_id: str) -> AssessmentView | None:
        record = self._records.get(session_id)
        return None if record is None else record.view()

    def reveal_evidence(
        self,
        session_id: str,
        question_id: str,
        command: EvidenceRevealRequest,
    ) -> AssessmentView | None:
        record = self._records.get(session_id)
        if record is None or record.question_id != question_id or record.item_id is None:
            return None
        if not record.evidence_revealed:
            record.evidence_revealed = True
            record.emitter.emit(
                LearningEvent.EVIDENCE_REVEALED,
                payload={
                    "question_id": question_id,
                    "item_id": record.item_id,
                    "interaction": command.interaction,
                },
            )
        return record.view()

    def submit_answer(
        self,
        session_id: str,
        question_id: str,
        command: AnswerSubmissionRequest,
    ) -> AssessmentView | None:
        record = self._records.get(session_id)
        if record is None or record.question_id != question_id:
            return None
        if record.answer_request_id is not None:
            if (
                record.answer_request_id == command.request_id
                and record.submitted_answer == command.answer
            ):
                return record.view()
            raise AssessmentCommandConflict("当前题目已经提交过答案")
        metadata = AnswerSubmissionMetadata(
            input_modality=command.input_modality,
            answer_format="choice" if record.options else "natural_language",
            evidence_revealed_before_answer=record.evidence_revealed,
        )
        if record.status != "awaiting_answer" or not record.responder.submit(
            command.answer, metadata
        ):
            raise AssessmentCommandConflict("当前题目不再接受答案")
        record.answer_request_id = command.request_id
        record.submitted_answer = command.answer
        record.status = "grading"
        return record.view()

    def next_round(
        self,
        session_id: str,
        command: NextRoundRequest,
    ) -> AssessmentView | None:
        record = self._records.get(session_id)
        if record is None:
            return None
        if command.request_id in record.next_request_ids:
            return record.view()
        if record.appeal is not None and record.appeal.status == "grading":
            raise AssessmentCommandConflict("补充说明正在重判，暂时不能进入下一题")
        if record.status != "judged" or record.round_index >= record.plan.rounds:
            raise AssessmentCommandConflict("当前考核不能进入下一题")
        record.next_request_ids.add(command.request_id)
        record.round_index += 1
        record.status = "preparing"
        record.question_id = None
        record.item_id = None
        record.question_text = None
        record.effective_question_type = None
        record.options = None
        record.evidence = None
        record.evidence_revealed = False
        record.judgement = None
        record.question_spec = None
        record.original_answer = None
        record.grading_language = None
        record.appeal_submission = None
        record.appeal = None
        record.attempt_id = None
        record.answer_request_id = None
        record.submitted_answer = None
        record.task = asyncio.create_task(
            self._run_round(record),
            name=f"grandquiz-api-assessment:{session_id}:{record.round_index}",
        )
        return record.view()

    def submit_appeal(
        self,
        session_id: str,
        question_id: str,
        command: AssessmentAppealRequest,
    ) -> AssessmentView | None:
        """Accept one explicit supplement without reopening automatic clarification."""

        record = self._records.get(session_id)
        if record is None or record.question_id != question_id:
            return None
        if record.appeal_submission is not None:
            try:
                record.appeal_submission.accept_retry(
                    request_id=command.request_id,
                    supplemental_answer=command.supplemental_answer,
                )
            except AppealSubmissionConflict as exc:
                raise AssessmentCommandConflict(str(exc)) from exc
            return record.view()
        if (
            record.status not in {"judged", "completed"}
            or record.question_spec is None
            or record.original_answer is None
            or record.grading_language is None
            or record.attempt_id is None
            or record.judgement is None
        ):
            raise AssessmentCommandConflict("当前题目不接受补充说明")
        submission = AppealSubmission.create(
            request_id=command.request_id,
            original_answer=record.original_answer,
            supplemental_answer=command.supplemental_answer,
        )
        record.appeal_submission = submission
        record.appeal = AssessmentAppealView(
            status="grading",
            supplemental_answer=submission.supplemental_answer,
            original_verdict=record.judgement.verdict,
        )
        record.emitter.emit(
            LearningEvent.ASSESSMENT_APPEAL_REQUESTED,
            parent_span_id=record.run_span_id,
            payload={
                "question_id": question_id,
                "item_id": record.item_id,
                "attempt_id": record.attempt_id,
            },
        )
        record.appeal_task = asyncio.create_task(
            self._run_appeal(record),
            name=f"grandquiz-api-assessment-appeal:{session_id}:{question_id}",
        )
        return record.view()

    async def _run_round(self, record: _AssessmentRecord) -> None:
        try:
            result = await record.session.assess(
                emitter=record.emitter,
                focus=record.focus,
                scope=record.scope,
                candidate_item_ids=record.candidate_item_ids,
                question_type=record.plan.intent_for(record.round_index),
            )
            if result.status == "refused":
                record.status = "refused"
                self._emit_terminal(record, "failed")
            else:
                record.question_spec = result.question_spec
                record.original_answer = result.answer_text
                record.grading_language = result.grading_language
                if result.question_spec is not None and record.judgement is not None:
                    record.appeal = AssessmentAppealView(
                        status="available",
                        original_verdict=record.judgement.verdict,
                    )
                record.status = (
                    "completed" if record.round_index == record.plan.rounds else "judged"
                )
                if record.status == "completed":
                    self._emit_terminal(record, "completed")
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.responder.cancel()
            self._emit_terminal(record, "cancelled")
            raise
        except Exception as exc:
            record.status = "failed"
            record.error = "本轮考核失败，请通过 trace_id 查看详情"
            record.emitter.emit(
                EventType.ERROR,
                span_id=record.run_span_id,
                payload={"error_type": type(exc).__name__},
            )
            self._emit_terminal(record, "failed")

    async def _run_appeal(self, record: _AssessmentRecord) -> None:
        submission = record.appeal_submission
        question = record.question_spec
        attempt_id = record.attempt_id
        judgement = record.judgement
        language = record.grading_language
        if (
            submission is None
            or question is None
            or attempt_id is None
            or judgement is None
            or language is None
        ):
            raise AssertionError("申诉重判缺少冻结的题目、答案或判决")
        original_verdict = judgement.verdict
        try:
            verdict = await grade_answer(
                question,
                submission.answer_for_regrade,
                provider=self._provider,
                emitter=record.emitter,
                parent_span_id=record.run_span_id,
                language=language,
            )
            corrected = self._corrections.apply(
                attempt_id,
                VerdictCorrectionCommand(
                    request_id=submission.request_id,
                    final_verdict=verdict.verdict,
                    reason=verdict.reason,
                    supplemental_answer=submission.supplemental_answer,
                ),
            )
            publish_pending_learning_facts(
                self._persistence.learning_facts,
                self._trace_store,
                clock=self._clock,
            )
            record.judgement = self._project_regraded_judgement(
                question,
                verdict,
                concept_state=corrected.concept_state,
                correct_answer=judgement.correct_answer,
            )
            record.appeal = AssessmentAppealView(
                status="resolved",
                supplemental_answer=submission.supplemental_answer,
                original_verdict=original_verdict,
                final_verdict=verdict.verdict,
                reason=verdict.reason,
            )
            record.emitter.emit(
                LearningEvent.ASSESSMENT_APPEAL_RESOLVED,
                parent_span_id=record.run_span_id,
                payload={
                    "question_id": record.question_id,
                    "item_id": record.item_id,
                    "attempt_id": attempt_id,
                    "original_verdict": original_verdict,
                    "final_verdict": verdict.verdict,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record.appeal = AssessmentAppealView(
                status="failed",
                supplemental_answer=submission.supplemental_answer,
                original_verdict=original_verdict,
                reason="补充说明重判失败，请通过 trace_id 查看详情",
            )
            record.emitter.emit(
                EventType.ERROR,
                parent_span_id=record.run_span_id,
                payload={"error_type": type(exc).__name__, "operation": "assessment_appeal"},
            )

    @staticmethod
    def _project_regraded_judgement(
        question: QuestionSpec,
        verdict: Verdict,
        *,
        concept_state: str | None,
        correct_answer: str | None,
    ) -> AssessmentJudgementView:
        points = {point.point_id: point.description for point in question.expected_points}
        return AssessmentJudgementView(
            verdict=verdict.verdict,
            reason=verdict.reason,
            diagnosis=verdict.diagnosis,
            matched_points=[
                AssessmentPointFeedbackView(point_id=point_id, description=points[point_id])
                for point_id in verdict.matched_points
            ],
            missing_points=[
                AssessmentPointFeedbackView(point_id=point_id, description=points[point_id])
                for point_id in verdict.missing_points
            ],
            concept_state=concept_state,
            correct_answer=correct_answer,
        )

    @staticmethod
    def _emit_terminal(
        record: _AssessmentRecord,
        status: Literal["completed", "failed", "cancelled"],
    ) -> None:
        if record.terminal_emitted:
            return
        record.terminal_emitted = True
        record.emitter.emit(
            ASSESSMENT_RUN_ENDED,
            span_id=record.run_span_id,
            payload={"status": status, "ok": status == "completed"},
        )

    @staticmethod
    def _project_event(record: _AssessmentRecord, event: AgentEvent) -> None:
        payload = event.payload
        if event.type == LearningEvent.QUESTION_ASKED:
            AssessmentManager._project_question(record, payload)
        elif event.type == LearningEvent.ASSESSMENT_REFUSED:
            reason = payload.get("reason")
            record.error = {
                "empty_kb": "知识库中还没有可用于考核的知识点。",
                "empty_scope": "当前选择的材料中没有可用于考核的知识点。",
                "empty_facet": "当前筛选条件没有已审核、可用于考核的知识点。",
                "unresolved_scope": "当前考核范围无法解析，请重新选择材料。",
            }.get(str(reason), "当前材料暂时无法开始考核。")
        elif event.type == LearningEvent.ANSWER_JUDGED:
            verdict = payload.get("verdict")
            reason = payload.get("reason")
            if isinstance(verdict, str) and isinstance(reason, str):
                diagnosis = payload.get("diagnosis")
                record.judgement = AssessmentJudgementView(
                    verdict=verdict,
                    reason=reason,
                    diagnosis=project_assessment_diagnosis(diagnosis),
                    matched_points=AssessmentManager._project_point_feedback(
                        payload.get("matched_points")
                    ),
                    missing_points=AssessmentManager._project_point_feedback(
                        payload.get("missing_points")
                    ),
                )
        elif event.type == LearningEvent.ASSESSMENT_JUDGEMENT_COMMITTED:
            committed_payload = payload.get("payload")
            if isinstance(committed_payload, Mapping):
                committed = cast("Mapping[str, object]", committed_payload)
                attempt_id = committed.get("attempt_id")
                if isinstance(attempt_id, str):
                    record.attempt_id = attempt_id
        elif event.type == LearningEvent.CONCEPT_STATE_CHANGED and record.judgement is not None:
            to_state = payload.get("to_state")
            record.judgement = record.judgement.model_copy(
                update={"concept_state": to_state if isinstance(to_state, str) else None}
            )
        elif event.type == LearningEvent.FOLLOWUP_GIVEN and record.judgement is not None:
            correct_answer = payload.get("correct_answer")
            if isinstance(correct_answer, str):
                record.judgement = record.judgement.model_copy(
                    update={"correct_answer": correct_answer}
                )

    @staticmethod
    def _project_point_feedback(value: object) -> list[AssessmentPointFeedbackView]:
        if not isinstance(value, list):
            return []
        projected: list[AssessmentPointFeedbackView] = []
        for item in cast("list[object]", value):
            if not isinstance(item, Mapping):
                continue
            point = cast("Mapping[str, object]", item)
            point_id = point.get("point_id")
            description = point.get("description")
            if isinstance(point_id, str) and isinstance(description, str):
                projected.append(
                    AssessmentPointFeedbackView(
                        point_id=point_id,
                        description=description,
                    )
                )
        return projected

    @staticmethod
    def _project_question(record: _AssessmentRecord, payload: Mapping[str, Any]) -> None:
        item_id = payload.get("item_id")
        question = payload.get("question")
        question_type = payload.get("question_type")
        if not all(isinstance(value, str) for value in (item_id, question, question_type)):
            return
        options = payload.get("options")
        evidence = payload.get("cited_evidence")
        record.item_id = str(item_id)
        record.question_text = str(question)
        record.effective_question_type = str(question_type)
        record.options = (
            [str(value) for value in cast("list[object]", options)]
            if isinstance(options, list)
            else []
        )
        record.evidence = (
            [str(value) for value in cast("list[object]", evidence)]
            if isinstance(evidence, list)
            else []
        )
        record.question_id = hashlib.sha256(
            f"{record.round_index}\0{item_id}\0{question}".encode()
        ).hexdigest()[:16]
        record.status = "awaiting_answer"

    async def cancel(self, session_id: str) -> AssessmentView | None:
        record = self._records.get(session_id)
        if record is None:
            return None
        if record.status in {"completed", "refused", "failed", "cancelled"}:
            return record.view()

        record.responder.cancel()
        if record.task is not None and not record.task.done():
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
        else:
            record.status = "cancelled"
            self._emit_terminal(record, "cancelled")
        return record.view()

    async def aclose(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        for record in self._records.values():
            record.responder.cancel()
            if record.task is not None and not record.task.done():
                record.task.cancel()
                tasks.append(record.task)
            if record.appeal_task is not None and not record.appeal_task.done():
                record.appeal_task.cancel()
                tasks.append(record.appeal_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

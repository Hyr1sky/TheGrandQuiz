"""逐题考核的进程内 HTTP 会话 owner；领域 workflow 仍由 AssessmentSession 持有。"""

import asyncio
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.asked_questions import AskedQuestionsLedger
from grandquiz.domain.learning.assessment.scope import SelectedScope
from grandquiz.domain.learning.assessment.selection import Focus
from grandquiz.domain.learning.assessment.session import AssessmentSession
from grandquiz.domain.learning.difficulty import DifficultyLedger
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.preference import PreferenceMemory
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import Store
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.kernel.clock import SystemClock
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

ASSESSMENT_RUN_STARTED = "web.assessment_run.started"
ASSESSMENT_RUN_ENDED = "web.assessment_run.ended"


class AssessmentStartRequest(BaseModel):
    resource_ids: list[str] = Field(min_length=1)
    rounds: int = Field(default=3, ge=1, le=20)
    question_type: str | None = None
    focus: Focus = "mixed"


class EvidenceRevealRequest(BaseModel):
    interaction: Literal["hover", "click", "keyboard"]


class AnswerSubmissionRequest(BaseModel):
    request_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)

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


class AssessmentJudgementView(BaseModel):
    verdict: str
    reason: str
    concept_state: str | None = None
    correct_answer: str | None = None


class AssessmentView(BaseModel):
    session_id: str
    trace_id: str
    status: AssessmentStatus
    round_index: int = Field(ge=1)
    rounds: int = Field(ge=1)
    question: AssessmentQuestionView | None = None
    judgement: AssessmentJudgementView | None = None
    error: str | None = None


class _WebResponder(Responder):
    """把领域层一次 ``await answer`` 映射成可由后续 HTTP command 完成的 Future。"""

    def __init__(self) -> None:
        self._answer: asyncio.Future[str] | None = None

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        del prompt, options
        self._answer = asyncio.get_running_loop().create_future()
        return await self._answer

    def cancel(self) -> None:
        if self._answer is not None and not self._answer.done():
            self._answer.cancel()

    def submit(self, answer: str) -> bool:
        if self._answer is None or self._answer.done():
            return False
        self._answer.set_result(answer)
        return True


@dataclass
class _AssessmentRecord:
    session_id: str
    trace_id: str
    rounds: int
    round_index: int
    question_type: str | None
    focus: Focus
    scope: SelectedScope
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
    answer_request_id: str | None = None
    submitted_answer: str | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = None
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
            rounds=self.rounds,
            question=question,
            judgement=self.judgement,
            error=self.error,
        )


class AssessmentManager:
    """把可阻塞的 Responder seam 投影成可查询的一题一步 Web 会话。"""

    def __init__(
        self,
        *,
        store: Store,
        provider: Provider,
        memory: Memory,
        asked_questions: AskedQuestionsLedger,
        preferences: PreferenceMemory,
        difficulty: DifficultyLedger,
        trace_store: TraceStore,
        trace_observatory: TraceObservatory | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._memory = memory
        self._asked_questions = asked_questions
        self._preferences = preferences
        self._difficulty = difficulty
        self._trace_store = trace_store
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
        emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)
        run_span_id = emitter.new_span_id()
        session = AssessmentSession(
            store=self._store,
            provider=self._provider,
            responder=responder,
            memory=self._memory,
            asked_questions=self._asked_questions,
            preferences=self._preferences,
            difficulty=self._difficulty,
        )
        record = _AssessmentRecord(
            session_id=session_id,
            trace_id=trace_id,
            rounds=request.rounds,
            round_index=1,
            question_type=request.question_type,
            focus=request.focus,
            scope=SelectedScope(resource_ids=request.resource_ids),
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
            payload={"status": "running"},
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
        if record.status != "awaiting_answer" or not record.responder.submit(command.answer):
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
        if record.status != "judged" or record.round_index >= record.rounds:
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
        record.answer_request_id = None
        record.submitted_answer = None
        record.task = asyncio.create_task(
            self._run_round(record),
            name=f"grandquiz-api-assessment:{session_id}:{record.round_index}",
        )
        return record.view()

    async def _run_round(self, record: _AssessmentRecord) -> None:
        try:
            result = await record.session.assess(
                emitter=record.emitter,
                focus=record.focus,
                scope=record.scope,
                question_type=record.question_type,
            )
            if result.status == "refused":
                record.status = "refused"
                self._emit_terminal(record, "failed")
            else:
                record.status = "completed" if record.round_index == record.rounds else "judged"
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
                "unresolved_scope": "当前考核范围无法解析，请重新选择材料。",
            }.get(str(reason), "当前材料暂时无法开始考核。")
        elif event.type == LearningEvent.ANSWER_JUDGED:
            verdict = payload.get("verdict")
            reason = payload.get("reason")
            if isinstance(verdict, str) and isinstance(reason, str):
                record.judgement = AssessmentJudgementView(verdict=verdict, reason=reason)
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
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

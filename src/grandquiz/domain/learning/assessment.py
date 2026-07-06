"""单题考核编排——考我 → 选题 → 出题 → 答 → 判卷的确定性 workflow（非自由 ReAct）。

ADR-0004："LLM 判卷，代码记账"。这里的骨架是确定性代码：选题、判决落账（``weak_item_id``）、
发事件全在代码里；LLM 只在"出题"（role=enrich）与"判卷"（role=basic）两个有界槽被调用。
每步都在**同一条事件脊柱**上发事件——trace 形状：

    assessment（根 span）
    ├── model（出题的 model span[enrich]，挂 assessment 下）
    └── model（判卷的 model span[basic]，挂 assessment 下）
    · assessment_refused / question_asked / answer_judged / concept_state_changed 皆
      parent=assessment span 的点事件（无 span_id，不进树）

失败分两支（照 ingest）：
- **领域优雅分支**：空知识库 → 发 ``ASSESSMENT_REFUSED``（``reason=empty_kb``）+ 优雅返回
  ``status="refused"``，**不调任何 LLM**、**不碰 memory**（eval case 2）。
- **基础设施 / harness 失败**（``QuestionError`` / ``GradingError`` / ``ReplayMiss`` / provider 传输
  异常 / bug）→ 闭合 assessment span 后**原样冒泡**（不吞成 refused 以免掩盖 harness 错误；
  优雅降级属 M6 RecoveryPolicy）。

M3.3（薄弱记忆 + 三态状态机 + 薄弱优先复考）：选题读 ``memory`` 走薄弱优先候选集；判卷后由**代码**
（非 LLM）调 ``memory.record_verdict`` 做三态转移 / 连对销账，并发 ``CONCEPT_STATE_CHANGED``
（在 ``ANSWER_JUDGED`` 之后、``assessment.ended`` 之前）。体现 ADR-0004："LLM 判卷，代码记账"。
"""

from typing import Literal

from pydantic import BaseModel

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.grading import VerdictLabel, grade_answer
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import LearningTask
from grandquiz.domain.learning.question import generate_question
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.selection import select_target
from grandquiz.domain.learning.store import LearningStore
from grandquiz.kernel.clock import Rng
from grandquiz.kernel.events import EventEmitter
from grandquiz.providers.base import Provider

# assessment 是 workflow span，用 kernel 级通用类型串（kernel 不认识 "assessment"，泛型建树即可）。
_ASSESSMENT_STARTED = "assessment.started"
_ASSESSMENT_ENDED = "assessment.ended"

# verdict 属"勉强 / 错"→ 该 item 记为薄弱（代码记账，非 LLM 产）。
_WEAK_VERDICTS: frozenset[VerdictLabel] = frozenset({"勉强", "错"})


class AssessmentResult(BaseModel):
    """一次单题考核的结果：``refused``（空库拒答）或 ``judged``（出题 → 答 → 判卷完成）。

    ``status="refused"`` 时 item_id / verdict / weak_item_id / concept_state 皆 None；
    ``status="judged"`` 时据本轮考核填充（``weak_item_id`` 是代码按 ``verdict`` 算出的记账结果，
    非 LLM 产）。``concept_state`` 是本轮记账后被考 item 在 Learning Memory 里的**最终状态**快照
    （薄弱 / 观察中，或 None=未追踪 / 已销账）——透出记账结果，便于直接断言，与 CONCEPT_STATE_CHANGED
    事件互补（事件携完整转移，此处只留终态）。
    """

    status: Literal["refused", "judged"]
    item_id: str | None = None
    verdict: VerdictLabel | None = None
    weak_item_id: str | None = None
    concept_state: str | None = None


async def assess_once(
    task: LearningTask,
    *,
    store: LearningStore,
    provider: Provider,
    responder: Responder,
    memory: LearningMemory,
    emitter: EventEmitter,
    rng: Rng,
) -> AssessmentResult:
    """对 ``task`` 跑一轮单题考核，全程发事件。见模块 docstring。"""
    # a. 开 assessment span（根）。此后任何未预期异常都必须闭合它（见末尾 except）。
    assessment_span = emitter.new_span_id()
    emitter.emit(_ASSESSMENT_STARTED, span_id=assessment_span, payload={"task_id": task.task_id})
    try:
        # b. 空库拒答（eval case 2）：不调任何 LLM，优雅返回 refused。
        items = store.items_for_task(task.task_id)
        if not items:
            emitter.emit(
                LearningEvent.ASSESSMENT_REFUSED,
                parent_span_id=assessment_span,
                payload={"task_id": task.task_id, "reason": "empty_kb"},
            )
            emitter.emit(
                _ASSESSMENT_ENDED,
                span_id=assessment_span,
                payload={"ok": True, "status": "refused", "reason": "empty_kb"},
            )
            return AssessmentResult(status="refused")

        # c. 选题（确定性，种子化 rng）。有薄弱概念时代码构造薄弱优先候选集（新概念不进集）。
        target = select_target(items, rng=rng, memory=memory)

        # d. 出题（role=enrich）+ 校验门（缝 3）。发 QUESTION_ASKED（锚定真实 item + 非空证据）。
        question = await generate_question(
            target, provider=provider, emitter=emitter, parent_span_id=assessment_span
        )
        emitter.emit(
            LearningEvent.QUESTION_ASKED,
            parent_span_id=assessment_span,
            payload={
                "item_id": target.item_id,
                "question": question.question,
                "cited_evidence": list(question.cited_evidence),
            },
        )

        # e. 作答（注入的确定性 responder）。
        answer = responder.answer(question.question)

        # f. 判卷（role=basic）+ 校验门（缝 3）。
        verdict = await grade_answer(
            target,
            question.question,
            answer,
            provider=provider,
            emitter=emitter,
            parent_span_id=assessment_span,
        )

        # g. 代码记账：verdict 属"勉强 / 错"→ weak_item_id = 被考 item；"对"→ None（不由 LLM 产）。
        weak_item_id = target.item_id if verdict.verdict in _WEAK_VERDICTS else None
        emitter.emit(
            LearningEvent.ANSWER_JUDGED,
            parent_span_id=assessment_span,
            payload={
                "item_id": target.item_id,
                "verdict": verdict.verdict,
                "weak_item_id": weak_item_id,
                "answer": answer,
                "cited_evidence": list(verdict.cited_evidence),
            },
        )

        # h. 代码记三态账（LLM 判卷、代码记账）：写 Learning Memory 并把转移上脊柱。
        #    三态转移 / 连对销账全在 memory 的纯代码里；这里只把结果发成 CONCEPT_STATE_CHANGED
        #    （在 ANSWER_JUDGED 之后、assessment.ended 之前）。
        transition = memory.record_verdict(target.item_id, verdict.verdict)
        emitter.emit(
            LearningEvent.CONCEPT_STATE_CHANGED,
            parent_span_id=assessment_span,
            payload={
                "item_id": transition.item_id,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "consecutive_correct": transition.consecutive_correct,
            },
        )

        # i. 闭合 assessment span。
        emitter.emit(
            _ASSESSMENT_ENDED, span_id=assessment_span, payload={"ok": True, "status": "judged"}
        )
        return AssessmentResult(
            status="judged",
            item_id=target.item_id,
            verdict=verdict.verdict,
            weak_item_id=weak_item_id,
            concept_state=memory.state_of(target.item_id),
        )
    except Exception as exc:
        # 非领域异常（QuestionError / GradingError / ReplayMiss / provider 基础设施错误 / bug）：
        # 闭合 assessment span 后原样冒泡。不吞成 refused——那会掩盖 harness / 基础设施错误。
        emitter.emit(
            _ASSESSMENT_ENDED, span_id=assessment_span, payload={"ok": False, "error": repr(exc)}
        )
        raise

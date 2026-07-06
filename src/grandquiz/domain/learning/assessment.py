"""单题考核编排——考我 → 选题 → 出题 → 答 → 判卷的确定性 workflow（非自由 ReAct）。

ADR-0004："LLM 判卷，代码记账"。这里的骨架是确定性代码：选题、判决落账（``weak_item_id``）、
发事件全在代码里；LLM 只在"出题"（role=enrich）与"判卷"（role=basic）两个有界槽被调用。
每步都在**同一条事件脊柱**上发事件——trace 形状：

    assessment（根 span）
    ├── model（出题的 model span[enrich]，挂 assessment 下）
    └── model（判卷的 model span[basic]，挂 assessment 下；**仅开放 / 追问有**——选择题判卷是
             确定性代码，无此 span）
    · assessment_refused / question_asked / answer_judged / concept_state_changed / followup_given
      皆 parent=assessment span 的点事件（无 span_id，不进树）

M3.4（题型路由 + 追问）：出题前按被考概念在 Learning Memory 的状态**路由题型**
（``route_question_type``，确定性代码）——首次接触 / 未追踪 → 选择题（MC，确定性判卷、
无判卷 model span），薄弱复考 → 追问（深挖 prompt 变体 + LLM 判卷），观察中 → 开放
（标准 LLM 判卷）。判决为"勉强 / 错"时后置追问"给正解"（确定性代码从 summary + evidence
组文本，发 ``FOLLOWUP_GIVEN``）。故 MC 与 开放 的事件序列不同：MC 路径无判卷 model span。
体现 ADR-0004："LLM 判卷，代码记账"——题型、MC 判卷、给正解全是代码。

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

from typing import Any, Literal

from pydantic import BaseModel

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.grading import (
    VerdictLabel,
    grade_answer,
    grade_multiple_choice,
)
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.models import KnowledgeItem, LearningTask
from grandquiz.domain.learning.question import (
    MultipleChoiceQuestion,
    generate_multiple_choice,
    generate_question,
)
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.routing import QuestionType, route_question_type
from grandquiz.domain.learning.selection import select_target
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.clock import Rng
from grandquiz.kernel.events import EventEmitter
from grandquiz.providers.base import Provider

# assessment 是 workflow span，用 kernel 级通用类型串（kernel 不认识 "assessment"，泛型建树即可）。
_ASSESSMENT_STARTED = "assessment.started"
_ASSESSMENT_ENDED = "assessment.ended"

# verdict 属"勉强 / 错"→ 该 item 记为薄弱（代码记账，非 LLM 产）+ 触发后置追问（给正解）。
_WEAK_VERDICTS: frozenset[VerdictLabel] = frozenset({"勉强", "错"})


def _compose_solution(item: KnowledgeItem) -> str:
    """从被考 item 的摘要 + 证据确定性组出正解文本（纯代码、不调 LLM）——后置追问"给正解"用。

    MVP 取确定性版："触发追问或给正解"里的"给正解"分支——直接把该概念的一句话摘要 + 逐字原文
    证据拼成正解，供学习者当场对照盲区。不产幽灵内容：只用 item 自己已有的 summary / evidence。
    """
    evidence = "；".join(ev.quote for ev in item.evidence)
    return f"{item.concept}：{item.summary}（原文依据：{evidence}）"


class AssessmentResult(BaseModel):
    """一次单题考核的结果：``refused``（空库拒答）或 ``judged``（出题 → 答 → 判卷完成）。

    ``status="refused"`` 时 item_id / verdict / weak_item_id / concept_state / question_type
    均为 None；
    ``status="judged"`` 时据本轮考核填充（``weak_item_id`` 是代码按 ``verdict`` 算出的记账结果，
    非 LLM 产）。``concept_state`` 是本轮记账后被考 item 在 Learning Memory 里的**最终状态**快照
    （薄弱 / 观察中，或 None=未追踪 / 已销账）——透出记账结果，便于直接断言，与 CONCEPT_STATE_CHANGED
    事件互补（事件携完整转移，此处只留终态）。``question_type`` 透出本轮路由到的题型（选择题 /
    开放 / 追问），便于直接断言路由决策（eval case 8），与 QUESTION_ASKED 事件的 ``question_type``
    互补。
    """

    status: Literal["refused", "judged"]
    item_id: str | None = None
    verdict: VerdictLabel | None = None
    weak_item_id: str | None = None
    concept_state: str | None = None
    question_type: QuestionType | None = None


async def assess_once(
    task: LearningTask,
    *,
    store: Store,
    provider: Provider,
    responder: Responder,
    memory: Memory,
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

        # d. 题型路由（确定性代码，按被考概念在 Learning Memory 的状态选题型；决策上脊柱供断言）。
        question_type = route_question_type(memory.state_of(target.item_id))

        # e. 分型出题（role=enrich）+ 校验门（缝 3）。选择题走 MC 出题；追问用深挖 prompt 变体；
        #    开放走标准出题。三者都发 QUESTION_ASKED（带 question_type，锚定真实 item + 非空证据）。
        mc: MultipleChoiceQuestion | None = None
        if question_type == "选择题":
            mc = await generate_multiple_choice(
                target, provider=provider, emitter=emitter, parent_span_id=assessment_span
            )
            question_text = mc.question
            asked_evidence = list(mc.cited_evidence)
        else:
            prompt_name = "question_probe" if question_type == "追问" else "question_generate"
            generated = await generate_question(
                target,
                provider=provider,
                emitter=emitter,
                parent_span_id=assessment_span,
                prompt_name=prompt_name,
            )
            question_text = generated.question
            asked_evidence = list(generated.cited_evidence)
        asked_payload: dict[str, Any] = {
            "item_id": target.item_id,
            "question": question_text,
            "cited_evidence": asked_evidence,
            "question_type": question_type,
        }
        if mc is not None:
            # MC 另带 options 供"用户视图"；answer_index 刻意不进事件（不泄露答案键——判卷走 in-code
            # mc 对象的确定性比对，答案键仍在 model span 输出里可供 trace / replay 复查）。
            asked_payload["options"] = list(mc.options)
        emitter.emit(
            LearningEvent.QUESTION_ASKED, parent_span_id=assessment_span, payload=asked_payload
        )

        # f. 作答（注入的确定性 responder）。
        answer = responder.answer(question_text)

        # g. 分型判卷。选择题 → 确定性代码（**不调 LLM**，无判卷 model span）；开放 / 追问 → LLM
        #    判卷（role=basic）+ 校验门（缝 3）。两路统一得到 VerdictLabel + cited_evidence。
        if mc is not None:
            verdict_label: VerdictLabel = grade_multiple_choice(answer, mc)
            judged_evidence = list(mc.cited_evidence)
        else:
            verdict = await grade_answer(
                target,
                question_text,
                answer,
                provider=provider,
                emitter=emitter,
                parent_span_id=assessment_span,
            )
            verdict_label = verdict.verdict
            judged_evidence = list(verdict.cited_evidence)

        # h. 代码记账：verdict 属"勉强 / 错"→ weak_item_id = 被考 item；"对"→ None（不由 LLM 产）。
        weak_item_id = target.item_id if verdict_label in _WEAK_VERDICTS else None
        emitter.emit(
            LearningEvent.ANSWER_JUDGED,
            parent_span_id=assessment_span,
            payload={
                "item_id": target.item_id,
                "verdict": verdict_label,
                "weak_item_id": weak_item_id,
                "answer": answer,
                "cited_evidence": judged_evidence,
            },
        )

        # i. 代码记三态账（LLM 判卷、代码记账）：写 Learning Memory 并把转移上脊柱。
        #    三态转移 / 连对销账全在 memory 的纯代码里；这里只把结果发成 CONCEPT_STATE_CHANGED
        #    （在 ANSWER_JUDGED 之后、assessment.ended 之前）。
        transition = memory.record_verdict(target.item_id, verdict_label)
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

        # j. 后置追问：判"勉强 / 错"→ 给正解（确定性代码，从被考 item 的 summary + evidence
        #    组文本），发 FOLLOWUP_GIVEN（在 CONCEPT_STATE_CHANGED 之后、assessment.ended 之前）。
        #    判"对"不发；MC 判错同样触发（MC 无"勉强"）。
        if verdict_label in _WEAK_VERDICTS:
            emitter.emit(
                LearningEvent.FOLLOWUP_GIVEN,
                parent_span_id=assessment_span,
                payload={
                    "item_id": target.item_id,
                    "correct_answer": _compose_solution(target),
                },
            )

        # k. 闭合 assessment span。
        emitter.emit(
            _ASSESSMENT_ENDED, span_id=assessment_span, payload={"ok": True, "status": "judged"}
        )
        return AssessmentResult(
            status="judged",
            item_id=target.item_id,
            verdict=verdict_label,
            weak_item_id=weak_item_id,
            concept_state=memory.state_of(target.item_id),
            question_type=question_type,
        )
    except Exception as exc:
        # 非领域异常（QuestionError / GradingError / ReplayMiss / provider 基础设施错误 / bug）：
        # 闭合 assessment span 后原样冒泡。不吞成 refused——那会掩盖 harness / 基础设施错误。
        emitter.emit(
            _ASSESSMENT_ENDED, span_id=assessment_span, payload={"ok": False, "error": repr(exc)}
        )
        raise

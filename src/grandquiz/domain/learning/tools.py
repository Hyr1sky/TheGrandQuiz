"""学习域工具——把确定性考官 / 记忆编排包成 kernel ReAct 循环可调的 ``Tool``（R1-S2）。

**住 domain 层**：import kernel 的 ``Tool`` / ``ToolContext`` + domain 的编排函数（``domain→kernel``
合法；``kernel↛domain`` 由 import-linter 守）。工具是 **wrap 不是改写**——``ingest_resource`` /
``Memory`` / ``Store`` 的签名逻辑一行不动，只是被薄薄一层包起来注册进 ``ToolRegistry``。

两个非交互同步工具（不做交互考核 / 不提取 kernel subagent，见 R1-S2 边界）：

- ``ingest(url)``：wrap ``ingest_resource`` → 返回结构化结果（入库知识点数 + 概念名列表）。内部
  span（fetch / Reader model / item_created）经 ``_ScopedEmitter`` **重挂在本次 TOOL_CALL 之下**、
  进 trace；ReAct 消息上下文**只收结构化结果字符串**、看不到考官内部 model 调用 / 消息（隔离在
  工具边界）。
- ``query_weak_concepts()``：**只读**——读 Learning Memory（薄弱 / 观察中 item）+ store（概念名）→
  返回薄弱概念摘要。无 LLM、确定性（context-free 工具，不需要 ctx）。

组装点（CLI / react 装配）用 ``register_learning_tools`` 把两者一并注册；工具的领域依赖
（task / source / provider / store / approval / memory …）在此闭包捕获，per-call 只多收一个 ``url``
与（ingest 才用的）``ToolContext``。
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.assessment import (
    _WEAK_VERDICTS,  # pyright: ignore[reportPrivateUsage]
    _compose_solution,  # pyright: ignore[reportPrivateUsage]
    _resolve_language,  # pyright: ignore[reportPrivateUsage]
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.grading import (
    VerdictLabel,
    grade_answer,
    grade_multiple_choice,
)
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.models import LearningTask
from grandquiz.domain.learning.question import (
    MultipleChoiceQuestion,
    generate_multiple_choice,
    generate_question,
)
from grandquiz.domain.learning.routing import QuestionType, route_question_type
from grandquiz.domain.learning.selection import select_target
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.clock import new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter
from grandquiz.kernel.tools import ModelRetry, Tool, ToolContext, ToolRegistry
from grandquiz.providers.base import Provider


class _ScopedEmitter(EventEmitter):
    """把被包装编排的**根 span** 重挂到给定 parent 之下的 emitter 包装（wrap 不改写）。

    组装持有 inner + ``__getattr__`` 全量委托：本包装不持有自己的 sink / clock / 计数器，只覆写
    ``trace_id`` / ``new_span_id`` / ``emit`` 三个成员（把 seq / span 计数与发布委托 inner，单一
    真源），其余**任意** EventEmitter 成员经 ``__getattr__`` 落到 inner。唯一改写：``emit`` 时把
    ``parent_span_id is None`` 的事件重挂到 ``root_parent``。于是被包装编排（``ingest_resource``）
    自建的根 span（``ingest.started`` / ``.ended``，本无父）成为本次 TOOL_CALL span 的子节点，而内部
    model / 点事件（都携显式 ``parent_span_id``）原样归位不变。``ingest_resource`` 因此一行不动。

    去掉了旧的 partial-subclass 脆弱（不调 ``super().__init__`` 却只覆写 3 方法——任何未覆写却触碰
    实例态的继承成员会 AttributeError）：现在 ``__getattr__`` 把未覆写成员透明委托 inner，故 inner
    未来新增任何方法 / 属性都不再炸（钉死于 test_cli_react）。仍名义上继承 EventEmitter 以保类型兼容
    （装配点把 scoped 当 ``EventEmitter`` 用）。
    """

    def __init__(self, inner: EventEmitter, root_parent: str) -> None:
        # 刻意不调 super().__init__：本包装不持有自己的 sink / clock / 计数器，全部委托 inner。
        self._inner = inner
        self._root_parent = root_parent

    def __getattr__(self, name: str) -> Any:
        # 未覆写的成员（及此前缺失的内部态）透明委托 inner。``_inner`` 本身在 __init__ 里经普通
        # setattr 落定，正常查找即命中、不会递归进本方法；加一道守卫防反序列化等场景的无限递归。
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    @property
    def trace_id(self) -> str:
        return self._inner.trace_id

    def new_span_id(self) -> str:
        return self._inner.new_span_id()

    def emit(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AgentEvent:
        return self._inner.emit(
            event_type,
            payload=payload,
            span_id=span_id,
            # 根 span（无父）重挂到 TOOL_CALL span 之下；内部事件携显式父、原样透传。
            parent_span_id=parent_span_id if parent_span_id is not None else self._root_parent,
        )


class IngestToolResult(BaseModel):
    """``ingest`` 工具回给 ReAct 的**结构化结果**——只透出边界字段，不泄漏考官内部过程。

    ``item_count`` / ``concepts`` 就是 ReAct 上下文能看到的全部；Reader 深读的内部消息 / model
    调用一律留在工具边界之内（隔离不变量）。序列化经 ``model_dump_json`` 进 tool 结果消息。
    """

    resource_id: str
    status: str
    item_count: int
    concepts: list[str]


class WeakConcept(BaseModel):
    """一个被追踪的薄弱概念摘要：item_id + 概念名 + 当前状态（薄弱 / 观察中）。"""

    item_id: str
    concept: str
    state: str


class WeakConceptsResult(BaseModel):
    """``query_weak_concepts`` 的结构化结果：当前任务下被追踪的薄弱概念（按 item_id 升序）。"""

    weak: list[WeakConcept]


class _IngestParams(BaseModel):
    url: str


class _QueryWeakParams(BaseModel):
    # 无入参：只读当前任务的薄弱台账（task / store / memory 在工具闭包里捕获）。
    pass


class _NextQuestionParams(BaseModel):
    # 无入参：task 在闭包捕获（考核对象即会话任务），选题 / 题型全由代码定。
    pass


class _SubmitAnswerParams(BaseModel):
    answer: str  # 学习者对上一道 next_question 的作答文本（选择题传所选项文本）。


def make_ingest_tool(
    task: LearningTask,
    *,
    source: Callable[[str], str],
    provider: Provider,
    store: Store,
    approval: ApprovalGate,
    max_bytes: int,
    allowed_domains: Collection[str],
) -> Tool:
    """建 ``ingest(url)`` 工具：wrap ``ingest_resource``，把内部 span 重挂到本次 TOOL_CALL 之下。

    领域依赖在闭包捕获（同 CLI ``run_ingest`` 的组装形状）；per-call 只多收 ``url`` 与
    ``ToolContext``（emitter + TOOL_CALL span id）。返回结构化 ``IngestToolResult`` 的 JSON 串。
    """

    async def handler(params: _IngestParams, ctx: ToolContext) -> str:
        # 作用域化 emitter：把 ingest 编排的根 span 重挂到本次 TOOL_CALL 之下（隔离在工具边界）。
        scoped: EventEmitter = (
            _ScopedEmitter(ctx.emitter, ctx.parent_span_id)
            if ctx.parent_span_id is not None
            else ctx.emitter
        )
        result = await ingest_resource(
            task,
            params.url,
            source=source,
            provider=provider,
            store=store,
            approval=approval,
            emitter=scoped,
            max_bytes=max_bytes,
            allowed_domains=allowed_domains,
        )
        return IngestToolResult(
            resource_id=result.resource_id,
            status=result.status,
            item_count=len(result.items),
            concepts=[item.concept for item in result.items],
        ).model_dump_json()

    return Tool(
        name="ingest",
        description="喂入一个 URL：深读入库，返回入库知识点数与概念名列表。",
        params=_IngestParams,
        handler=handler,
        wants_context=True,
    )


def make_query_weak_concepts_tool(task: LearningTask, *, store: Store, memory: Memory) -> Tool:
    """建 ``query_weak_concepts()`` 工具：只读 Learning Memory + store，返回本任务薄弱概念摘要。

    确定性、无 LLM（context-free，不需 ctx）：取记忆里被追踪的 item，交集到本任务的 item（跨任务
    隔离——他任务薄弱点不泄漏），按 item_id 升序输出概念名 + 状态。
    """

    async def handler(params: _QueryWeakParams) -> str:
        _ = params  # 无入参：全部依赖在闭包捕获
        concept_by_id = {item.item_id: item.concept for item in store.items_for_task(task.task_id)}
        weak = [
            WeakConcept(item_id=item_id, concept=concept_by_id[item_id], state=state)
            for item_id in sorted(memory.weak_item_ids())
            if item_id in concept_by_id and (state := memory.state_of(item_id)) is not None
        ]
        return WeakConceptsResult(weak=weak).model_dump_json()

    return Tool(
        name="query_weak_concepts",
        description="只读查询当前任务的薄弱概念（薄弱 / 观察中）及其概念名。",
        params=_QueryWeakParams,
        handler=handler,
    )


# --------------------------------------------------------------------------- #
# 交互考核：next_question / submit_answer（对话回合驱动，不需 suspend/resume #6）
# --------------------------------------------------------------------------- #


@dataclass
class _PendingQuestion:
    """会话内**待答态**：一次 ``next_question`` 出题后、``submit_answer`` 判卷前的挂起快照。

    ``mc`` 非 None 表示选择题（判卷走 ``grade_multiple_choice`` 确定性代码、不打 LLM）；为 None 则
    是开放 / 追问（判卷走 ``grade_answer`` LLM 槽）。判卷 / 记账用到的一切（被考 item、题干、题型、
    MC 对象）都在此持久，故 ``submit_answer`` 无需重跑出题、跨对话回合边界续上（replay 时同样 LLM
    输出重建同一待答态）。
    """

    target_item_id: str
    question_text: str
    question_type: QuestionType
    mc: MultipleChoiceQuestion | None
    asked_evidence: list[str]


def _empty_pending() -> dict[str, "_PendingQuestion"]:
    # 显式类型工厂（照 trace.Span._empty_children）：裸 default_factory=dict 会被推成 Unknown。
    return {}


def _empty_asked() -> dict[str, list[str]]:
    return {}


@dataclass
class _QuizSession:
    """交互考核的**会话作用域**状态（进程内、按 task 键），与 S2 工具闭包同一套会话依赖并列。

    ``pending``：每个 task 至多一道挂起待答题；``recently_asked``：会话内"已问过"台账（同
    ``assess_once`` 的 ``recently_asked``，复考同一薄弱概念时换角度去重）；``seed`` + ``_counter``：
    种子化选题的确定性推进——每次 ``next_question`` 用 ``new_rng(seed + counter)`` 并把 counter
    自增（**禁墙上时钟 / 全局 random**；replay 时同 seed + 同调用序 → 同选题）。
    """

    seed: int
    _counter: int = 0
    pending: dict[str, _PendingQuestion] = field(default_factory=_empty_pending)
    recently_asked: dict[str, list[str]] = field(default_factory=_empty_asked)

    def next_seed(self) -> int:
        """取本次出题的选题种子并确定性推进会话计数器（同 ``run_quiz`` 的 ``seed + 轮次``）。"""
        seed = self.seed + self._counter
        self._counter += 1
        return seed


class NextQuestionResult(BaseModel):
    """``next_question`` 回给 ReAct 的结构化结果：题 + 题型 +（选择题才有的）options。

    ``status="refused"``（空库）时其余字段为 None；``status="asked"`` 时透出 item_id / 题干 /
    题型 / options（**刻意不含 answer_index**——不泄露答案键给作答方，同 ``QUESTION_ASKED`` 事件）。
    """

    status: Literal["asked", "refused"]
    item_id: str | None = None
    question: str | None = None
    question_type: QuestionType | None = None
    options: list[str] | None = None


class SubmitAnswerResult(BaseModel):
    """``submit_answer`` 回给 ReAct 的结构化结果：判决 + 记账终态 +（勉强 / 错才有的）追问正解。

    ``weak_item_id`` / ``concept_state`` 是代码按 verdict 算出的记账结果（非 LLM 产，ADR-0004）；
    ``followup`` 仅在判"勉强 / 错"时非 None（``_compose_solution`` 从被考 item 的 summary + evidence
    确定性组出正解），判"对"为 None。
    """

    item_id: str
    verdict: VerdictLabel
    weak_item_id: str | None = None
    concept_state: str | None = None
    followup: str | None = None


def make_next_question_tool(
    task: LearningTask,
    *,
    provider: Provider,
    store: Store,
    memory: Memory,
    session: _QuizSession,
) -> Tool:
    """建 ``next_question()`` 工具：选题 → 题型路由 → 分型出题 → 发 ``QUESTION_ASKED`` → 存待答态。

    **只组合** assessment 的确定性子函数（``select_target`` / ``route_question_type`` /
    ``generate_multiple_choice`` / ``generate_question`` / ``_resolve_language``），零逻辑重复；
    出题的 LLM 槽（role=enrich）与 ``QUESTION_ASKED`` 事件都挂在本次 TOOL_CALL span 之下
    （``ctx.parent_span_id``）、上同一条脊柱。待答态入会话（按 task 键）供 ``submit_answer`` 用。
    """

    async def handler(_params: _NextQuestionParams, ctx: ToolContext) -> str:
        items = store.items_for_task(task.task_id)
        if not items:  # 空库优雅拒答（同 assess_once）：不调任何 LLM、不碰 memory。
            ctx.emitter.emit(
                LearningEvent.ASSESSMENT_REFUSED,
                parent_span_id=ctx.parent_span_id,
                payload={"task_id": task.task_id, "reason": "empty_kb"},
            )
            return NextQuestionResult(status="refused").model_dump_json()

        # 选题（确定性、会话计数器推进的种子化 rng）→ 题型路由（读 Learning Memory 状态）。
        target = select_target(items, rng=new_rng(session.next_seed()), memory=memory)
        question_type = route_question_type(memory.state_of(target.item_id))
        language = _resolve_language(task, None)
        asked_before = session.recently_asked.get(target.item_id, [])

        # 分型出题（role=enrich）：选择题走 MC 出题；追问用深挖 prompt 变体；开放走标准出题。
        mc: MultipleChoiceQuestion | None = None
        if question_type == "选择题":
            mc = await generate_multiple_choice(
                target,
                provider=provider,
                emitter=ctx.emitter,
                parent_span_id=ctx.parent_span_id,
                language=language,
                asked_before=asked_before,
            )
            question_text = mc.question
            asked_evidence = list(mc.cited_evidence)
        else:
            prompt_name = "question_probe" if question_type == "追问" else "question_generate"
            generated = await generate_question(
                target,
                provider=provider,
                emitter=ctx.emitter,
                parent_span_id=ctx.parent_span_id,
                prompt_name=prompt_name,
                language=language,
                asked_before=asked_before,
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
            asked_payload["options"] = list(mc.options)  # answer_index 不进事件（不泄答案键）
        ctx.emitter.emit(
            LearningEvent.QUESTION_ASKED, parent_span_id=ctx.parent_span_id, payload=asked_payload
        )

        # 记账 + 持久待答态：已问台账追加本题（复考去重）；待答态入会话供 submit_answer 续上。
        session.recently_asked.setdefault(target.item_id, []).append(question_text)
        session.pending[task.task_id] = _PendingQuestion(
            target_item_id=target.item_id,
            question_text=question_text,
            question_type=question_type,
            mc=mc,
            asked_evidence=asked_evidence,
        )
        return NextQuestionResult(
            status="asked",
            item_id=target.item_id,
            question=question_text,
            question_type=question_type,
            options=list(mc.options) if mc is not None else None,
        ).model_dump_json()

    return Tool(
        name="next_question",
        description="对当前任务出下一道考核题（自动选薄弱概念 + 路由题型），返回题干与选项。",
        params=_NextQuestionParams,
        handler=handler,
        wants_context=True,
    )


def make_submit_answer_tool(
    task: LearningTask,
    *,
    provider: Provider,
    store: Store,
    memory: Memory,
    session: _QuizSession,
) -> Tool:
    """建 ``submit_answer(answer)`` 工具：读待答态 → 判卷 → 记账 → 发事件 → 清态 → 返回判决 + 追问。

    **判卷 / 记账绝不由 ReAct LLM 决定**（不变量）：MC 判卷走确定性代码、开放走 ``grade_answer``；
    ``weak_item_id`` 由代码按 verdict 算、``record_verdict`` 写 Learning Memory——都在本工具的确定性
    代码里（ADR-0004"LLM 判卷，代码记账"）。事件序 ``ANSWER_JUDGED`` → ``CONCEPT_STATE_CHANGED``
    →（勉强 / 错）``FOLLOWUP_GIVEN``，全挂在本次 TOOL_CALL span 之下。无待答态 → ``ModelRetry``
    （提示 ReAct 先调 ``next_question``）。判完清待答态（挡二次提交）。
    """

    async def handler(params: _SubmitAnswerParams, ctx: ToolContext) -> str:
        pending = session.pending.get(task.task_id)
        if pending is None:
            raise ModelRetry("尚无待答题：请先调用 next_question 出题，再提交作答。")
        items = store.items_for_task(task.task_id)
        target = next((it for it in items if it.item_id == pending.target_item_id), None)
        if target is None:  # 护栏：待答 item 已不在库（正常不该发生）——清态并让模型重来。
            del session.pending[task.task_id]
            raise ModelRetry("待答题对应的知识点已不在库，请重新 next_question 出题。")

        # 分型判卷：MC 走确定性代码（不打 LLM、无判卷 model span）；开放 / 追问走 LLM basic 槽。
        if pending.mc is not None:
            verdict_label: VerdictLabel = grade_multiple_choice(params.answer, pending.mc)
            judged_evidence = list(pending.mc.cited_evidence)
        else:
            verdict = await grade_answer(
                target,
                pending.question_text,
                params.answer,
                provider=provider,
                emitter=ctx.emitter,
                parent_span_id=ctx.parent_span_id,
                language=_resolve_language(task, None),
            )
            verdict_label = verdict.verdict
            judged_evidence = list(verdict.cited_evidence)

        # 代码记账：weak_item_id 由 verdict 算（非 LLM 产），发 ANSWER_JUDGED。
        weak_item_id = target.item_id if verdict_label in _WEAK_VERDICTS else None
        ctx.emitter.emit(
            LearningEvent.ANSWER_JUDGED,
            parent_span_id=ctx.parent_span_id,
            payload={
                "item_id": target.item_id,
                "verdict": verdict_label,
                "weak_item_id": weak_item_id,
                "answer": params.answer,
                "cited_evidence": judged_evidence,
            },
        )
        # 代码记三态账：写 Learning Memory，把转移上脊柱（CONCEPT_STATE_CHANGED）。
        transition = memory.record_verdict(target.item_id, verdict_label)
        ctx.emitter.emit(
            LearningEvent.CONCEPT_STATE_CHANGED,
            parent_span_id=ctx.parent_span_id,
            payload={
                "item_id": transition.item_id,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "consecutive_correct": transition.consecutive_correct,
            },
        )
        # 后置追问：判"勉强 / 错"→ 给正解（确定性代码组文本），发 FOLLOWUP_GIVEN；判"对"不发。
        followup: str | None = None
        if verdict_label in _WEAK_VERDICTS:
            followup = _compose_solution(target)
            ctx.emitter.emit(
                LearningEvent.FOLLOWUP_GIVEN,
                parent_span_id=ctx.parent_span_id,
                payload={"item_id": target.item_id, "correct_answer": followup},
            )

        del session.pending[task.task_id]  # 清待答态：一题一答，挡二次提交。
        return SubmitAnswerResult(
            item_id=target.item_id,
            verdict=verdict_label,
            weak_item_id=weak_item_id,
            concept_state=memory.state_of(target.item_id),
            followup=followup,
        ).model_dump_json()

    return Tool(
        name="submit_answer",
        description="提交对上一道 next_question 的作答，返回判决与（答错时的）正解追问。",
        params=_SubmitAnswerParams,
        handler=handler,
        wants_context=True,
    )


def register_learning_tools(
    registry: ToolRegistry,
    *,
    task: LearningTask,
    source: Callable[[str], str],
    provider: Provider,
    store: Store,
    approval: ApprovalGate,
    memory: Memory,
    max_bytes: int,
    allowed_domains: Collection[str],
    quiz_seed: int = 0,
) -> None:
    """组装点：注册 ``ingest`` / ``query_weak_concepts`` / ``next_question`` / ``submit_answer``。

    领域依赖在此注入并被各工具闭包捕获；注册后 ReAct 主体（``run_agent_turn``）即可按名调它们，
    kernel 侧 registry / dispatch 完全不认识这些工具的领域语义（kernel 领域无关）。交互考核的
    ``next_question`` / ``submit_answer`` 共享同一 ``_QuizSession``（待答态 + 已问台账 + 种子化选题
    计数器）；``quiz_seed`` 给选题种子（replay 传固定值 → 可复现，CLI 可传可变值）。
    """
    registry.register(
        make_ingest_tool(
            task,
            source=source,
            provider=provider,
            store=store,
            approval=approval,
            max_bytes=max_bytes,
            allowed_domains=allowed_domains,
        )
    )
    registry.register(make_query_weak_concepts_tool(task, store=store, memory=memory))
    session = _QuizSession(seed=quiz_seed)
    registry.register(
        make_next_question_tool(
            task, provider=provider, store=store, memory=memory, session=session
        )
    )
    registry.register(
        make_submit_answer_tool(
            task, provider=provider, store=store, memory=memory, session=session
        )
    )

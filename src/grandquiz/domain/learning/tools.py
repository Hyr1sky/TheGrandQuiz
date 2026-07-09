"""学习域工具——把确定性考官 / 记忆编排包成 kernel ReAct 循环可调的 ``Tool``（R1-S2）。

**住 domain 层**：import kernel 的 ``Tool`` / ``ToolContext`` + domain 的编排函数（``domain→kernel``
合法；``kernel↛domain`` 由 import-linter 守）。工具是 **wrap 不是改写**——``ingest_resource`` /
``Memory`` / ``Store`` 的签名逻辑一行不动，只是被薄薄一层包起来注册进 ``ToolRegistry``。

三个工具（R1-S6：交互考核硬化为受控子流程，见下）：

- ``ingest(url)``：wrap ``ingest_resource`` → 返回结构化结果（入库知识点数 + 概念名列表）。内部
  span（fetch / Reader model / item_created）经 ``_ScopedEmitter`` **重挂在本次 TOOL_CALL 之下**、
  进 trace；ReAct 消息上下文**只收结构化结果字符串**、看不到考官内部 model 调用 / 消息（隔离在
  工具边界）。
- ``query_weak_concepts()``：**只读**——读 Learning Memory（薄弱 / 观察中 item）+ store（概念名，
  全库读）→ 返回薄弱概念摘要。无 LLM、确定性（context-free 工具，不需要 ctx）。
- ``start_quiz(count?, focus?)``：**受控一问一答子流程**——内部跑 ``assess_once × count``
  （``assess_once`` 一行不改），用**注入的 Responder** 逐题作答（MC 走 ``questionary.select`` 逐字
  选项文本 → 确定性逐字判卷），共享 emitter（内部 assess_once span 嵌 TOOL_CALL 之下），返回结构化
  小结（考几题 / 每题判决 / 暴露哪些薄弱点）。**LLM 只触发它、拿小结，不进逐题循环、不复述题目、
  不自己判卷**——
  取代 S2b 的软工具 ``next_question`` / ``submit_answer``（那套把逐轮编排压给 LLM，deepseek 守不住：
  编题 / 把 MC 答案加 "B. " 前缀毁逐字判卷 / 题目双重渲染 / confabulate）。

组装点（CLI / react 装配）用 ``register_learning_tools`` 把三者一并注册（``start_quiz`` 仅当注入了
``responder`` 时注册——无 responder 无从逐题作答）；工具的领域依赖（source / provider / store /
approval / memory / responder / preferences …）在此闭包捕获，per-call 只多收工具入参与
（context-aware 工具才用的）``ToolContext``。``LearningTask`` 已消解（ADR-0005）——知识进全局 KB
单池、无 task 线程，工具不再收 task。
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.assessment import AssessmentResult, assess_once
from grandquiz.domain.learning.grading import VerdictLabel
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.preference import PreferenceMemory
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.selection import Focus
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.clock import new_rng
from grandquiz.kernel.events import AgentEvent, EventEmitter
from grandquiz.kernel.tools import Tool, ToolContext, ToolRegistry
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
    """``query_weak_concepts`` 的结构化结果：全库被追踪的薄弱概念（按 item_id 升序，全局 KB）。"""

    weak: list[WeakConcept]


class _IngestParams(BaseModel):
    url: str


class _QueryWeakParams(BaseModel):
    # 无入参：只读全库薄弱台账（store / memory 在工具闭包里捕获）。
    pass


def make_ingest_tool(
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
    ``ToolContext``（emitter + TOOL_CALL span id）。资源内容寻址（``resource_id = derive_id(url)``，
    ADR-0005）、进全局 KB 单池。返回结构化 ``IngestToolResult`` 的 JSON 串。
    """

    async def handler(params: _IngestParams, ctx: ToolContext) -> str:
        # 作用域化 emitter：把 ingest 编排的根 span 重挂到本次 TOOL_CALL 之下（隔离在工具边界）。
        scoped: EventEmitter = (
            _ScopedEmitter(ctx.emitter, ctx.parent_span_id)
            if ctx.parent_span_id is not None
            else ctx.emitter
        )
        result = await ingest_resource(
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


def make_query_weak_concepts_tool(*, store: Store, memory: Memory) -> Tool:
    """建 ``query_weak_concepts()`` 工具：只读 Learning Memory + store，返回全库薄弱概念摘要。

    确定性、无 LLM（context-free，不需 ctx）：取记忆里被追踪的 item，用**全库**概念名映射解析
    （全局 KB——``LearningTask`` 已消解、知识进同一池，ADR-0005），按 item_id 升序输出概念名 + 状态。
    """

    async def handler(params: _QueryWeakParams) -> str:
        _ = params  # 无入参：全部依赖在闭包捕获
        concept_by_id = {item.item_id: item.concept for item in store.all_items()}
        weak = [
            WeakConcept(item_id=item_id, concept=concept_by_id[item_id], state=state)
            for item_id in sorted(memory.weak_item_ids())
            if item_id in concept_by_id and (state := memory.state_of(item_id)) is not None
        ]
        return WeakConceptsResult(weak=weak).model_dump_json()

    return Tool(
        name="query_weak_concepts",
        description="只读查询已积累的薄弱概念（薄弱 / 观察中）及其概念名。",
        params=_QueryWeakParams,
        handler=handler,
    )


# --------------------------------------------------------------------------- #
# 交互考核受控子流程：start_quiz（一问一答，assess_once × N，LLM 不进逐题循环）
# --------------------------------------------------------------------------- #

# start_quiz 单次调用的出题上限：挡 LLM 传超大 count 把一次工具调用拖成长跑（保守取 20）。
_MAX_QUIZ_COUNT = 20


@dataclass
class _QuizSeedCounter:
    """受控考核循环的**选题种子推进器**（进程内、跨同一会话的多次 start_quiz 调用累积）。

    每题取 ``seed + counter`` 并把 counter 自增（**禁墙上时钟 / 全局 random**——replay 时同 seed +
    同题序 → 同选题）；同 ``run_quiz`` 的 ``seed + 轮次``，只是把计数器提升为跨调用会话态，故连续
    两次 ``start_quiz`` 不会因种子重置而复现同一选题序。
    """

    seed: int
    _counter: int = 0

    def next_seed(self) -> int:
        seed = self.seed + self._counter
        self._counter += 1
        return seed


class QuizRoundResult(BaseModel):
    """``start_quiz`` 小结里的**单题结果**：被考概念 + 判决 + 记账后终态（全由代码算，非 LLM 产）。

    ``concept_state`` 是本题记账后被考 item 在 Learning Memory 的最终状态（薄弱 / 观察中，或
    None=未追踪 / 已销账），透出记账结果供 LLM 转述——与逐题的 ``CONCEPT_STATE_CHANGED`` 事件互补。
    """

    item_id: str
    concept: str
    verdict: VerdictLabel
    concept_state: str | None = None


class StartQuizResult(BaseModel):
    """``start_quiz`` 回给 ReAct 的**结构化小结**——LLM 据此转述，不复述题目 / 不自己判卷。

    ``status="refused"``（空库）时 ``asked=0`` / ``rounds=[]`` / ``weak=[]``；``status="completed"``
    时 ``asked`` 为实际考过题数（用户中途取消则少于请求 count），``rounds`` 逐题判决，``weak`` 是
    本次考核后全库仍被追踪的薄弱概念（按 item_id 升序，同 ``query_weak_concepts`` 全局 KB 口径）。
    """

    status: Literal["completed", "refused"]
    asked: int
    rounds: list[QuizRoundResult]
    weak: list[WeakConcept]


class _StartQuizParams(BaseModel):
    count: int = 1  # 本次考核出题数（默认 1；handler 夹到 [1, _MAX_QUIZ_COUNT]）
    focus: Focus = "mixed"  # 选题聚焦：mixed 覆盖优先（默认）/ new 只考没考过的 / weak 复习薄弱
    # 目录式 scope（GKB-S4）：按 exact resource_id 收窄考哪些材料；None（默认）= 全库。LLM 从目录
    # 清单认出用户意图对应的 resource_id 填入（命中不了 → 拿不到 id → 别填，assess_once 诚实拒答）。
    resource_ids: list[str] | None = None
    # 用户显式题型意图短语（GKB-S5，修 #1 错题型；ADR-0006）：LLM 只抽用户原话里的题型意图
    # （"简答"/"选择题"/"追问"…），代码用冻结同义表映射到既有三题型、显式意图胜过记忆状态自适应
    # 路由；None（默认）= 不指定 → 按薄弱状态自适应路由。别自造题型、别把"简答"填成"选择题"。
    question_type: str | None = None


def _weak_concepts(store: Store, memory: Memory) -> list[WeakConcept]:
    """全库被追踪的薄弱概念摘要（item_id 升序，全局 KB）——与 ``query_weak_concepts`` 同口径。"""
    concept_by_id = {item.item_id: item.concept for item in store.all_items()}
    return [
        WeakConcept(item_id=item_id, concept=concept_by_id[item_id], state=state)
        for item_id in sorted(memory.weak_item_ids())
        if item_id in concept_by_id and (state := memory.state_of(item_id)) is not None
    ]


def make_start_quiz_tool(
    *,
    provider: Provider,
    store: Store,
    memory: Memory,
    responder: Responder,
    preferences: PreferenceMemory | None = None,
    quiz_seed: int = 0,
) -> Tool:
    """建 ``start_quiz(count)`` 工具：受控一问一答子流程，内部跑 ``assess_once × count``。

    **只组合** ``assess_once``（一行不改）：逐题选题 / 出题（role=enrich）/ 判卷（MC 走确定性代码、
    开放走 role=basic）/ 记账全在 ``assess_once`` 的确定性骨架里，本工具只做 **N 题编排 + 收小结**。
    每题作答走**注入的 Responder**（真机 ``InteractiveResponder`` 的 ``questionary.select`` 逐字
    返回所选项文本 → ``grade_multiple_choice`` 逐字比对，从根杜绝 "B. " 前缀污染判卷）。

    内部 ``assess_once`` 的 assessment 根 span（本无父）经 ``_ScopedEmitter`` 重挂到本次 TOOL_CALL
    span 之下（``ctx.parent_span_id``），故整棵考核子树上同一条脊柱、进 trace；出题 / 判卷 / 记账的
    点事件（QUESTION_ASKED / ANSWER_JUDGED / …）携显式父原样透传。

    ``focus``（工具入参，LLM 按用户意图逐次给）：选题聚焦档位，透传每题的 ``assess_once`` →
    ``select_target``——``mixed``（默认）覆盖优先（先考未考过、考完一遍才兜底薄弱，修 dogfood
    锁死）、``new`` 只考未考过、``weak`` 复习薄弱。``recently_asked`` 跨同一会话累积、其 keys 作
    已考集喂选题，故连续 mixed 考核自然覆盖不同 item（不再锁死）。

    ``resource_ids``（工具入参，目录式 scope，GKB-S4，修 #1 考错库）：按 exact resource_id 把候选
    池收窄到指定材料，透传每题 ``assess_once`` → ``apply_scope``（选题前的确定性预过滤）。``None``
    （默认）= 全库。LLM 从注入的目录清单认出用户意图对应的 resource_id 填入；命中为空 →
    ``assess_once`` 发 ``empty_scope`` 拒答（诚实说"还没这主题的材料"，不静默考别的库）。

    ``question_type``（工具入参，用户显式题型意图短语，GKB-S5，修 #1 错题型；ADR-0006）：LLM 只抽
    用户原话里的题型意图短语（"简答"/"选择题"/"追问"…）原样填入，透传每题 ``assess_once`` →
    ``resolve_question_type`` 用冻结同义表映射到既有三题型——**显式意图胜过记忆状态自适应路由**，
    未知 / ``None``（默认）回落自适应；短答意图代码层禁止映射到"选择题"（护栏，防复现 #1）。

    ``preferences``：透传给 ``assess_once`` 解析出题语言（**偏好 > 中文**）；``None`` 时行为不变
    （走"中文"兜底）。``recently_asked`` / ``_QuizSeedCounter`` 在闭包捕获、跨同一会话的多次
    ``start_quiz`` 累积（复考换角度去重 + 选题种子确定性推进）。空库 → 优雅返回 ``refused``（不调
    任何 LLM）；用户中途取消作答（Responder 抛 ``KeyboardInterrupt``）→ 结束考核、返回已完成部分。
    """
    seed_counter = _QuizSeedCounter(seed=quiz_seed)
    recently_asked: dict[str, list[str]] = {}

    async def handler(params: _StartQuizParams, ctx: ToolContext) -> str:
        # 作用域化 emitter：把每题 assessment 根 span 重挂到本次 TOOL_CALL 之下（隔离在工具边界）。
        scoped: EventEmitter = (
            _ScopedEmitter(ctx.emitter, ctx.parent_span_id)
            if ctx.parent_span_id is not None
            else ctx.emitter
        )
        concept_by_id = {it.item_id: it.concept for it in store.all_items()}
        count = min(max(params.count, 1), _MAX_QUIZ_COUNT)  # 夹到 [1, 上限]，挡 0 / 负 / 超大
        rounds: list[QuizRoundResult] = []
        for _ in range(count):
            try:
                result: AssessmentResult = await assess_once(
                    store=store,
                    provider=provider,
                    responder=responder,
                    memory=memory,
                    emitter=scoped,
                    rng=new_rng(seed_counter.next_seed()),
                    recently_asked=recently_asked,
                    focus=params.focus,
                    preferences=preferences,
                    resource_ids=params.resource_ids,
                    question_type=params.question_type,
                )
            except KeyboardInterrupt:
                # 用户取消作答：结束本次考核，返回已完成部分（不把取消当空作答污染判卷）。
                break
            if result.status == "refused":
                # 空库：无题可考，优雅拒答（不调任何 LLM）——首题即 refused，直接返回。
                return StartQuizResult(
                    status="refused", asked=0, rounds=[], weak=[]
                ).model_dump_json()
            # judged 分支：item_id / verdict 必非 None（AssessmentResult 契约）；防御性收窄。
            if result.item_id is None or result.verdict is None:
                continue
            rounds.append(
                QuizRoundResult(
                    item_id=result.item_id,
                    concept=concept_by_id.get(result.item_id, result.item_id),
                    verdict=result.verdict,
                    concept_state=result.concept_state,
                )
            )
        return StartQuizResult(
            status="completed",
            asked=len(rounds),
            rounds=rounds,
            weak=_weak_concepts(store, memory),
        ).model_dump_json()

    return Tool(
        name="start_quiz",
        description=(
            "从全库发起一次考核：出 count 道题（默认 1）逐题问用户并判卷，返回考了几题 / "
            "每题判决 / 暴露的薄弱点小结。你只触发它、转述小结——"
            "不要复述题目、不要自行判卷、不要编题。\n"
            "据用户意图填两个可选旋钮（都不填 = 全库、按掌握状态自适应出题）：\n"
            "· resource_ids（考哪份材料）：从上文【学情】里的库存材料清单认出用户点名的主题对应的 "
            "exact resource_id 填入（可多选）——语义匹配是你的活（'代理通信协议' 认到 ACP 那条）；"
            "认不出对应材料 / 用户没点具体材料 → 别填，宁可诚实拒答也不考错库。\n"
            "· question_type（要哪种题型）：只抽用户原话里的题型意图短语（如 '简答' / '选择题' / "
            "'追问'）原样填入，由代码映射到题型；用户没点题型 → 别填。"
            "别自造题型、别把 '简答' 填成 '选择题'。\n"
            "· focus（选题聚焦）：mixed=覆盖优先（默认，先考没考过的），new=只考没考过的"
            "（用户说'考其他的 / 换一批'），weak=复习薄弱（用户说'复习 / 考薄弱'）。\n"
            "工具输入示例——用户'考代理通信协议的简答题'（清单里该主题的 resource_id 记作 r-acp）："
            '{"resource_ids": ["r-acp"], "question_type": "简答"}；'
            "用户'随便考我一道'：{}（两旋钮都不填 = 全库自动路由）。"
        ),
        params=_StartQuizParams,
        handler=handler,
        wants_context=True,
    )


def register_learning_tools(
    registry: ToolRegistry,
    *,
    source: Callable[[str], str],
    provider: Provider,
    store: Store,
    approval: ApprovalGate,
    memory: Memory,
    max_bytes: int,
    allowed_domains: Collection[str],
    responder: Responder | None = None,
    preferences: PreferenceMemory | None = None,
    quiz_seed: int = 0,
) -> None:
    """组装点：注册 ``ingest`` / ``query_weak_concepts`` /（有 responder 时）``start_quiz``。

    领域依赖在此注入并被各工具闭包捕获；注册后 ReAct 主体（``run_agent_turn``）即可按名调它们，
    kernel 侧 registry / dispatch 完全不认识这些工具的领域语义（kernel 领域无关）。

    ``responder`` 为 ``None`` 时**不注册** ``start_quiz``——受控考核无从逐题作答（如 S2 的 ingest /
    query 单测装配无需交互作答）；真机 react 装配注入 ``InteractiveResponder`` 后即可考核。
    ``preferences`` 透传给 ``start_quiz`` → ``assess_once`` 解析出题语言；``quiz_seed`` 给选题种子
    （replay 传固定值 → 可复现，CLI 可传可变值）。
    """
    registry.register(
        make_ingest_tool(
            source=source,
            provider=provider,
            store=store,
            approval=approval,
            max_bytes=max_bytes,
            allowed_domains=allowed_domains,
        )
    )
    registry.register(make_query_weak_concepts_tool(store=store, memory=memory))
    if responder is not None:
        registry.register(
            make_start_quiz_tool(
                provider=provider,
                store=store,
                memory=memory,
                responder=responder,
                preferences=preferences,
                quiz_seed=quiz_seed,
            )
        )

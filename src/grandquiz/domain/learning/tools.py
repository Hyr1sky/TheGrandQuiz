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
from typing import Any

from pydantic import BaseModel

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.models import LearningTask
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.events import AgentEvent, EventEmitter
from grandquiz.kernel.tools import Tool, ToolContext, ToolRegistry
from grandquiz.providers.base import Provider


class _ScopedEmitter(EventEmitter):
    """把被包装编排的**根 span** 重挂到给定 parent 之下的 emitter 包装（wrap 不改写）。

    只用真 emitter 的**公开面**（``new_span_id`` / ``emit`` / ``trace_id``）委托 seq / span 计数与
    发布；唯一改写：``emit`` 时把 ``parent_span_id is None`` 的事件重挂到 ``root_parent``。于是
    被包装编排（``ingest_resource``）自建的根 span（``ingest.started`` / ``.ended``，本无父）成为
    本次 TOOL_CALL span 的子节点，而内部 model / 点事件（都携显式 ``parent_span_id``）原样归位不变。
    ``ingest_resource`` 因此一行不动，只是收到一个作用域化的 emitter——考官内核零改写。
    """

    def __init__(self, inner: EventEmitter, root_parent: str) -> None:
        # 刻意不调 super().__init__：本包装不持有自己的 sink / clock / 计数器，全部委托 inner。
        self._inner = inner
        self._root_parent = root_parent

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
) -> None:
    """组装点：把 ``ingest`` + ``query_weak_concepts`` 一并注册进 kernel ``ToolRegistry``。

    领域依赖在此注入并被两个工具闭包捕获；注册后 ReAct 主体（``run_agent_turn``）即可按名调它们，
    kernel 侧 registry / dispatch 完全不认识这些工具的领域语义（kernel 领域无关）。
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

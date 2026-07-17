"""``ingest(url)`` 工具：wrap ``ingest_resource``，把内部编排一行不改地包成 ReAct 可调的
``Tool``。
"""

from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.ingest import ingest_resource
from grandquiz.domain.learning.ingest.fetch import FetchSource
from grandquiz.domain.learning.store import Store
from grandquiz.domain.learning.tools._scoped_emitter import ScopedEmitter
from grandquiz.kernel.events import EventEmitter
from grandquiz.kernel.tools import Tool, ToolContext
from grandquiz.providers.base import Provider


class IngestToolResult(BaseModel):
    """``ingest`` 工具回给 ReAct 的**结构化结果**——只透出边界字段，不泄漏考官内部过程。

    ``item_count`` / ``concepts`` 就是 ReAct 上下文能看到的全部；Reader 深读的内部消息 / model
    调用一律留在工具边界之内（隔离不变量）。序列化经 ``model_dump_json`` 进 tool 结果消息。
    """

    resource_id: str
    status: str
    item_count: int
    concepts: list[str]


class _IngestParams(BaseModel):
    url: str


def make_ingest_tool(
    *,
    source: FetchSource,
    provider: Provider,
    store: Store,
    approval: ApprovalGate,
    max_bytes: int,
    allowed_domains: Collection[str] | Literal["*"],
) -> Tool:
    """建 ``ingest(url)`` 工具：wrap ``ingest_resource``，把内部 span 重挂到本次 TOOL_CALL 之下。

    领域依赖在闭包捕获（同 CLI ``run_ingest`` 的组装形状）；per-call 只多收 ``url`` 与
    ``ToolContext``（emitter + TOOL_CALL span id）。资源内容寻址（``resource_id = derive_id(url)``，
    ADR-0005）、进全局 KB 单池。返回结构化 ``IngestToolResult`` 的 JSON 串。
    """

    async def handler(params: _IngestParams, ctx: ToolContext) -> str:
        # 作用域化 emitter：把 ingest 编排的根 span 重挂到本次 TOOL_CALL 之下（隔离在工具边界）。
        scoped: EventEmitter = (
            ScopedEmitter(ctx.emitter, ctx.parent_span_id)
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

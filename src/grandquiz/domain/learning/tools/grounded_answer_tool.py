"""把 GroundedDocumentAnswer 的同一实现暴露为 ReAct 高层工具。"""

from grandquiz.domain.learning.grounded_answer import (
    GroundedAnswerRequest,
    GroundedDocumentAnswer,
)
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.tools import Tool, ToolContext
from grandquiz.providers.base import Provider


def make_grounded_answer_tool(*, store: Store, provider: Provider) -> Tool:
    workflow = GroundedDocumentAnswer(store=store, provider=provider)

    async def handler(params: GroundedAnswerRequest, ctx: ToolContext) -> str:
        result = await workflow.answer(
            params,
            emitter=ctx.emitter,
            parent_span_id=ctx.parent_span_id,
        )
        return result.model_dump_json()

    return Tool(
        name="answer_from_documents",
        description=(
            "在精确材料范围内完成有界搜索、读取、回答和逐字 citation；"
            "普通基于材料的问答优先使用本工具。"
        ),
        params=GroundedAnswerRequest,
        handler=handler,
        wants_context=True,
    )

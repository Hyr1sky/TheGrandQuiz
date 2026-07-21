"""``web_search`` 工具：只发现有界候选，不自动抓取或入库。"""

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.ingest.web_search import SearchError, SearchProvider, SearchResult
from grandquiz.kernel.tools import Tool, ToolContext


def _empty_domains() -> list[str]:
    return []


class SearchToolResult(BaseModel):
    adapter: str
    selection_required: Literal[True] = True
    results: list[SearchResult]


class _SearchParams(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)
    domains: list[str] = Field(default_factory=_empty_domains, max_length=10)

    @field_validator("query")
    @classmethod
    def _query_is_not_whitespace(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("query 不能为空白")
        return normalized


def make_web_search_tool(*, provider: SearchProvider) -> Tool:
    """把一个 SearchProvider 暴露为 context-aware ReAct tool。"""

    async def handler(params: _SearchParams, ctx: ToolContext) -> str:
        span_id = ctx.emitter.new_span_id()
        query_fingerprint = hashlib.sha256(params.query.encode("utf-8")).hexdigest()
        ctx.emitter.emit(
            LearningEvent.WEB_SEARCH_STARTED,
            span_id=span_id,
            parent_span_id=ctx.parent_span_id,
            payload={
                "adapter": provider.adapter_name,
                "query_fingerprint": query_fingerprint,
                "query_chars": len(params.query),
                "limit": params.limit,
                "domains": sorted(set(params.domains)),
            },
        )
        try:
            results = await provider.search(
                params.query,
                limit=params.limit,
                domains=tuple(params.domains),
            )
        except SearchError as exc:
            ctx.emitter.emit(
                LearningEvent.WEB_SEARCH_ENDED,
                span_id=span_id,
                payload={"ok": False, "adapter": provider.adapter_name, "reason": exc.reason},
            )
            raise
        ctx.emitter.emit(
            LearningEvent.WEB_SEARCH_ENDED,
            span_id=span_id,
            payload={
                "ok": True,
                "adapter": provider.adapter_name,
                "result_count": len(results),
            },
        )
        return SearchToolResult(
            adapter=provider.adapter_name,
            results=results,
        ).model_dump_json()

    return Tool(
        name="web_search",
        description=(
            "按主题搜索学习材料候选，返回标题、URL 和摘要；只发现候选，不会自动读取或入库。"
            "调用后必须结束当前回合并等待用户显式选择，不能在同一回合继续调用 ingest。"
        ),
        params=_SearchParams,
        handler=handler,
        wants_context=True,
    )

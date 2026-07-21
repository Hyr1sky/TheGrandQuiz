"""``grandquiz search``——不经 LLM 的 Web Search provider 连通与候选检查。"""

import asyncio
import uuid
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from grandquiz.domain.learning.ingest.web_search import (
    SearchError,
    SearchProvider,
    SearchResult,
)
from grandquiz.domain.learning.tools.web_search_tool import SearchToolResult, make_web_search_tool
from grandquiz.interfaces.cli.commands import _print_trace_location
from grandquiz.interfaces.cli.composition import (
    _DEFAULT_DB,
    _ensure_parent,
    _resolve_trace_db,
    build_event_backbone,
    search_provider_from_env,
)
from grandquiz.kernel.events import EventEmitter
from grandquiz.kernel.tools import ToolContext, ToolRegistry

__all__ = ["_run_search_cli", "run_search"]


async def run_search(
    *,
    query: str,
    limit: int,
    domains: tuple[str, ...],
    provider: SearchProvider,
    emitter: EventEmitter,
) -> list[SearchResult]:
    """经正式 ``web_search`` 工具返回候选；不调用 LLM、Fetch、Reader 或 learning store。"""
    registry = ToolRegistry()
    registry.register(make_web_search_tool(provider=provider))
    raw = await registry.dispatch(
        "web_search",
        {"query": query, "limit": limit, "domains": list(domains)},
        ctx=ToolContext(emitter=emitter),
    )
    return SearchToolResult.model_validate_json(raw).results


def _run_search_cli(
    *, query: str, limit: int, domains: tuple[str, ...], trace_db_path: Path | None
) -> None:
    console = Console()
    resolved_trace_db = _resolve_trace_db(_DEFAULT_DB, trace_db_path)
    _ensure_parent(resolved_trace_db)
    trace_id = uuid.uuid4().hex
    trace_store = None
    try:
        provider = search_provider_from_env()
        if provider is None:
            raise ValueError(
                "未配置 Web Search：设置 TAVILY_API_KEY，或设置 SEARXNG_URL；"
                "两者同时存在时再设置 WEB_SEARCH_PROVIDER"
            )
        emitter, trace_store = build_event_backbone(resolved_trace_db, trace_id=trace_id)
        results = asyncio.run(
            run_search(
                query=query,
                limit=limit,
                domains=domains,
                provider=provider,
                emitter=emitter,
            )
        )
    except (SearchError, ValueError) as exc:
        console.print(f"[red]搜索失败：{escape(str(exc))}[/]")
        raise SystemExit(1) from exc
    finally:
        if trace_store is not None:
            trace_store.close()

    console.print(f"[bold green]{provider.adapter_name} 返回 {len(results)} 条候选[/]")
    console.print_json(data=[result.model_dump(mode="json") for result in results])
    _print_trace_location(console, trace_id, resolved_trace_db)

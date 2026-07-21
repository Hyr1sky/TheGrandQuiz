"""可拔插 Web Search adapter 与 ReAct 候选工具测试。"""

import json

import httpx
import pytest

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.ingest.web_search import (
    SearchError,
    SearchResult,
    SearXNGSearchProvider,
    TavilySearchProvider,
)
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.store import LearningStore
from grandquiz.domain.learning.tools import register_learning_tools
from grandquiz.domain.learning.tools.web_search_tool import SearchToolResult
from grandquiz.evals.harness import build_event_harness
from grandquiz.interfaces.cli.commands.search import run_search
from grandquiz.interfaces.cli.composition import search_provider_from_env
from grandquiz.kernel.tools import ToolContext, ToolRegistry


def _keep_all(_item: KnowledgeItem) -> bool:
    return True


def _registry(*, search_provider: object | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    register_learning_tools(
        registry,
        source=lambda url: "local fixture",
        provider=None,  # type: ignore[arg-type]
        store=LearningStore(),
        approval=ScriptedApprovalGate(keep=_keep_all),
        memory=LearningMemory(),
        max_bytes=4096,
        allowed_domains={"local"},
        search_provider=search_provider,  # type: ignore[arg-type]
    )
    return registry


class _FakeSearchProvider:
    adapter_name = "fake_search"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    async def search(
        self, query: str, *, limit: int, domains: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        self.calls.append((query, limit, domains))
        return [
            SearchResult(
                title="MySQL 索引面试题",
                url="https://guide.example/mysql/indexes",
                snippet="覆盖索引、回表与最左前缀",
                adapter=self.adapter_name,
                rank=1,
            )
        ]


def test_web_search_is_absent_without_configured_provider() -> None:
    registry = _registry()
    assert "web_search" not in registry


def test_searxng_is_only_enabled_by_explicit_environment_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    assert search_provider_from_env() is None

    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080")
    configured = search_provider_from_env()
    assert configured is not None
    assert configured.adapter_name == "searxng"


def test_tavily_is_enabled_by_api_key_without_credit_card_provider_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-secret")

    configured = search_provider_from_env()

    assert isinstance(configured, TavilySearchProvider)
    assert configured.adapter_name == "tavily"


def test_search_provider_requires_explicit_choice_when_both_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-secret")
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080")

    with pytest.raises(ValueError, match="WEB_SEARCH_PROVIDER"):
        search_provider_from_env()


@pytest.mark.parametrize(
    ("selected", "provider_type"),
    [("tavily", TavilySearchProvider), ("searxng", SearXNGSearchProvider)],
)
def test_explicit_search_provider_choice_is_honored(
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
    provider_type: type[object],
) -> None:
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", selected)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-secret")
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080")

    assert isinstance(search_provider_from_env(), provider_type)


async def test_web_search_returns_candidates_and_emits_bounded_trace() -> None:
    provider = _FakeSearchProvider()
    registry = _registry(search_provider=provider)
    emitter, events, trace = build_event_harness()

    raw = await registry.dispatch(
        "web_search",
        {"query": "MySQL 面试高频考点", "limit": 3, "domains": ["guide.example"]},
        ctx=ToolContext(emitter=emitter, parent_span_id="tool-call"),
    )
    result = SearchToolResult.model_validate_json(raw)

    assert provider.calls == [("MySQL 面试高频考点", 3, ("guide.example",))]
    assert result.adapter == "fake_search"
    assert result.selection_required is True
    assert [candidate.url for candidate in result.results] == [
        "https://guide.example/mysql/indexes"
    ]
    assert [event.type for event in events] == [
        "learning.web_search.started",
        "learning.web_search.ended",
    ]
    assert events[0].parent_span_id == "tool-call"
    assert events[0].payload["query_chars"] == len("MySQL 面试高频考点")
    assert "MySQL" not in json.dumps(events[0].payload, ensure_ascii=False)
    assert events[1].payload["result_count"] == 1
    trace.close()


async def test_direct_search_cli_service_lists_candidates_without_llm_or_ingest() -> None:
    provider = _FakeSearchProvider()
    emitter, events, trace = build_event_harness()

    results = await run_search(
        query="MySQL 面试高频考点",
        limit=3,
        domains=("guide.example",),
        provider=provider,
        emitter=emitter,
    )

    assert provider.calls == [("MySQL 面试高频考点", 3, ("guide.example",))]
    assert [result.url for result in results] == ["https://guide.example/mysql/indexes"]
    assert [event.type for event in events] == [
        "learning.web_search.started",
        "learning.web_search.ended",
    ]
    trace.close()


async def test_searxng_adapter_maps_and_domain_filters_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["q"] == "mysql interview site:guide.example"
        assert request.url.params["format"] == "json"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "MySQL Index Guide",
                        "url": "https://guide.example/mysql-index",
                        "content": "B-tree and covering indexes",
                        "engine": "brave",
                    },
                    {
                        "title": "Unselected domain",
                        "url": "https://noise.example/mysql",
                        "content": "noise",
                        "engine": "google",
                    },
                ]
            },
        )

    provider = SearXNGSearchProvider(
        endpoint="https://search.example", transport=httpx.MockTransport(handler)
    )
    results = await provider.search("mysql interview", limit=5, domains=("guide.example",))

    assert [result.model_dump() for result in results] == [
        {
            "title": "MySQL Index Guide",
            "url": "https://guide.example/mysql-index",
            "snippet": "B-tree and covering indexes",
            "adapter": "searxng",
            "rank": 1,
            "metadata": {"engine": "brave"},
        }
    ]


async def test_tavily_adapter_uses_basic_search_and_maps_candidates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.tavily.com/search"
        assert request.headers["authorization"] == "Bearer tvly-test-secret"
        payload = json.loads(request.content)
        assert payload == {
            "query": "mysql interview",
            "max_results": 3,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_domains": ["guide.example"],
        }
        assert "tvly-test-secret" not in request.url.query.decode()
        assert "tvly-test-secret" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "MySQL Index Guide",
                        "url": "https://guide.example/mysql-index",
                        "content": "B-tree and covering indexes",
                        "score": 0.91,
                    }
                ]
            },
        )

    provider = TavilySearchProvider(
        api_key="tvly-test-secret",
        transport=httpx.MockTransport(handler),
    )
    results = await provider.search("mysql interview", limit=3, domains=("guide.example",))

    assert [result.model_dump() for result in results] == [
        {
            "title": "MySQL Index Guide",
            "url": "https://guide.example/mysql-index",
            "snippet": "B-tree and covering indexes",
            "adapter": "tavily",
            "rank": 1,
            "metadata": {"score": "0.91"},
        }
    ]


async def test_tavily_invalid_schema_fails_with_stable_reason() -> None:
    provider = TavilySearchProvider(
        api_key="tvly-test-secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"oops": []})),
    )

    with pytest.raises(SearchError) as captured:
        await provider.search("mysql interview", limit=5)

    assert captured.value.reason == "invalid_response"


async def test_searxng_invalid_schema_fails_with_stable_reason() -> None:
    provider = SearXNGSearchProvider(
        endpoint="https://search.example",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"oops": []})),
    )

    with pytest.raises(SearchError) as captured:
        await provider.search("mysql interview", limit=5)

    assert captured.value.reason == "invalid_response"

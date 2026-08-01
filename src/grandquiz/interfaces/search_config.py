"""Channel-neutral Web Search provider composition from environment variables."""

import os

from grandquiz.domain.learning.ingest.web_search import (
    SearchProvider,
    SearXNGSearchProvider,
    TavilySearchProvider,
)

_SEARXNG_URL_ENV = "SEARXNG_URL"
_SEARXNG_TIMEOUT_ENV = "SEARXNG_TIMEOUT_SECONDS"
_TAVILY_API_KEY_ENV = "TAVILY_API_KEY"
_TAVILY_TIMEOUT_ENV = "TAVILY_TIMEOUT_SECONDS"
_WEB_SEARCH_PROVIDER_ENV = "WEB_SEARCH_PROVIDER"


def search_provider_from_env() -> SearchProvider | None:
    """Select an optional provider without owning its service or credentials."""

    selected = os.environ.get(_WEB_SEARCH_PROVIDER_ENV, "").strip().casefold()
    tavily_api_key = os.environ.get(_TAVILY_API_KEY_ENV, "").strip()
    endpoint = os.environ.get(_SEARXNG_URL_ENV, "").strip()

    if selected and selected not in {"tavily", "searxng"}:
        raise ValueError("WEB_SEARCH_PROVIDER 只能是 tavily 或 searxng")
    if not selected and tavily_api_key and endpoint:
        raise ValueError("同时配置 Tavily 与 SearXNG 时必须设置 WEB_SEARCH_PROVIDER")

    if selected == "tavily" or (not selected and tavily_api_key):
        if not tavily_api_key:
            raise ValueError("WEB_SEARCH_PROVIDER=tavily 需要 TAVILY_API_KEY")
        timeout = float(os.environ.get(_TAVILY_TIMEOUT_ENV, "10"))
        return TavilySearchProvider(api_key=tavily_api_key, timeout_seconds=timeout)
    if selected == "searxng" and not endpoint:
        raise ValueError("WEB_SEARCH_PROVIDER=searxng 需要 SEARXNG_URL")
    if not endpoint:
        return None
    timeout = float(os.environ.get(_SEARXNG_TIMEOUT_ENV, "10"))
    return SearXNGSearchProvider(endpoint=endpoint, timeout_seconds=timeout)

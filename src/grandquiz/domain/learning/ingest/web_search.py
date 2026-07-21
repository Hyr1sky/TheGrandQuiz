"""供应商无关的 Web Search 契约与可选 SearXNG 直接 adapter。"""

from collections.abc import Sequence
from typing import Literal, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from grandquiz.kernel.recovery import ErrorClass

SearchFailureReason = Literal[
    "invalid_query",
    "invalid_limit",
    "timeout",
    "http_status",
    "invalid_response",
    "source_failure",
]


def _empty_metadata() -> dict[str, str]:
    return {}


class SearchError(Exception):
    """搜索边界的稳定失败分类；开放 ReAct 可看到失败后改路。"""

    error_class = ErrorClass.DEGRADED

    def __init__(self, reason: SearchFailureReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class SearchResult(BaseModel):
    """搜索供应商无关的候选材料；结果始终是不可信输入。"""

    title: str
    url: str
    snippet: str
    adapter: str
    rank: int = Field(ge=1)
    metadata: dict[str, str] = Field(default_factory=_empty_metadata)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or parsed.hostname is None:
            raise ValueError("搜索候选必须是带主机名的 http(s) URL")
        return value


class SearchProvider(Protocol):
    """可拔插搜索 seam；只发现候选，不读取正文或写 KB。"""

    adapter_name: str

    async def search(
        self, query: str, *, limit: int, domains: tuple[str, ...] = ()
    ) -> list[SearchResult]: ...


class _SearXResult(BaseModel):
    title: str = ""
    url: str
    content: str = ""
    engine: str | None = None


class _SearXResponse(BaseModel):
    results: list[_SearXResult]


class SearXNGSearchProvider:
    """调用已配置 SearXNG JSON API；不拥有服务进程或 Docker 生命周期。"""

    adapter_name = "searxng"

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or parsed.hostname is None:
            raise ValueError("SearXNG endpoint 必须是带主机名的 http(s) URL")
        self._endpoint = endpoint.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def search(
        self, query: str, *, limit: int, domains: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise SearchError("invalid_query", "搜索 query 不能为空")
        if not 1 <= limit <= 10:
            raise SearchError("invalid_limit", "搜索结果上限必须在 1..10")
        normalized_domains = _normalize_domains(domains)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_seconds), transport=self._transport
            ) as client:
                response = await client.get(
                    f"{self._endpoint}/search",
                    params={"q": normalized_query, "format": "json"},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SearchError("timeout", "SearXNG 搜索超时") from exc
        except httpx.HTTPStatusError as exc:
            raise SearchError(
                "http_status", f"SearXNG HTTP 状态异常：{exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchError("source_failure", f"SearXNG 请求失败：{exc!r}") from exc

        try:
            payload = _SearXResponse.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise SearchError("invalid_response", "SearXNG 返回了无效 JSON schema") from exc

        results: list[SearchResult] = []
        seen: set[str] = set()
        for raw_rank, candidate in enumerate(payload.results, start=1):
            if candidate.url in seen or not _domain_allowed(candidate.url, normalized_domains):
                continue
            try:
                result = SearchResult(
                    title=candidate.title.strip() or candidate.url,
                    url=candidate.url,
                    snippet=candidate.content.strip(),
                    adapter=self.adapter_name,
                    rank=raw_rank,
                    metadata={"engine": candidate.engine} if candidate.engine else {},
                )
            except ValidationError:
                continue
            seen.add(candidate.url)
            results.append(result)
            if len(results) >= limit:
                break
        return results


def _normalize_domains(domains: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for domain in domains:
        host = domain.strip().casefold().rstrip(".")
        if host and "/" not in host and ":" not in host:
            normalized.append(host)
    return tuple(dict.fromkeys(normalized))


def _domain_allowed(url: str, domains: tuple[str, ...]) -> bool:
    if not domains:
        return True
    host = (urlparse(url).hostname or "").casefold().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)

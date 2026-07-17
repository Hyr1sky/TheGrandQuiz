"""结构化资源获取边界：域名守卫、异步隔离与确定性大小限制。"""

import asyncio
import hashlib
from collections.abc import Callable, Collection
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlparse

from pydantic import BaseModel

from grandquiz.kernel.recovery import ErrorClass

ALLOW_ANY_DOMAIN: Literal["*"] = "*"
FetchFailureReason = Literal[
    "invalid_url",
    "domain_not_allowed",
    "too_large",
    "ssrf",
    "redirect_limit",
    "timeout",
    "http_status",
    "unsupported_content_type",
    "source_failure",
]


class FetchError(Exception):
    """抓取失败的稳定分类；ingest 据此优雅失败且不产幽灵 item。"""

    error_class = ErrorClass.RESOURCE_UNREADABLE

    def __init__(self, reason: FetchFailureReason, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class FetchResult(BaseModel):
    """获取层的结构化结果；正文始终是不可信输入。"""

    requested_url: str
    final_url: str
    content: str
    content_type: str
    content_hash: str


@runtime_checkable
class BoundedFetchSource(Protocol):
    """能在传输途中执行解压后字节上限的原生异步 source。"""

    async def fetch(self, url: str, *, max_bytes: int) -> FetchResult: ...


FetchSource = Callable[[str], str] | BoundedFetchSource


async def fetch_resource(
    url: str,
    *,
    source: FetchSource,
    max_bytes: int,
    allowed_domains: Collection[str] | Literal["*"],
) -> FetchResult:
    """获取一个资源；同步 source 在线程中执行，原生 source 自己流式限流。"""
    host = urlparse(url).hostname
    if host is None:
        raise FetchError("invalid_url", f"URL 缺主机名（url={url}）")
    if allowed_domains != ALLOW_ANY_DOMAIN and host not in allowed_domains:
        raise FetchError("domain_not_allowed", f"域名不在白名单：{host!r}（url={url}）")
    try:
        if isinstance(source, BoundedFetchSource):
            return await source.fetch(url, max_bytes=max_bytes)
        content = await asyncio.to_thread(source, url)
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError("source_failure", f"抓取源失败：{exc!r}（url={url}）") from exc

    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise FetchError(
            "too_large", f"内容超过大小上限：{len(encoded)} > {max_bytes} 字节（url={url}）"
        )
    return FetchResult(
        requested_url=url,
        final_url=url,
        content=content,
        content_type="text/plain",
        content_hash=hashlib.sha256(encoded).hexdigest(),
    )

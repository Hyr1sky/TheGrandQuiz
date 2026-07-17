"""异步流式 HTTP 获取：SSRF、逐跳重定向、类型与解压后大小硬限制。"""

import hashlib
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from grandquiz.domain.learning.ingest.fetch import FetchError, FetchResult

_MAX_REDIRECTS = 5
_ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "text/plain", "text/markdown", "application/xhtml+xml"}
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


def _assert_globally_reachable(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise FetchError("ssrf", f"DNS 解析失败（SSRF 防护拒绝）：{hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("ssrf", f"目标解析到非全局可达地址：{hostname} -> {ip}")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.chunks.append(data)


def extract_text_from_html(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    lines = [line.strip() for line in "".join(parser.chunks).splitlines()]
    return "\n".join(line for line in lines if line)


class HttpFetchSource:
    """可注入 transport 的原生异步有界 HTTP source。"""

    def __init__(
        self, *, timeout_seconds: float = 10.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def fetch(self, url: str, *, max_bytes: int) -> FetchResult:
        timeout = httpx.Timeout(self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            request = client.build_request("GET", url)
            response = await self._follow_validated_redirects(client, request)
            try:
                content_type = _content_type(response)
                raw = await _read_bounded(response, max_bytes=max_bytes)
                encoding = response.encoding or "utf-8"
                decoded = raw.decode(encoding, errors="replace")
                content = (
                    extract_text_from_html(decoded)
                    if content_type in ("text/html", "application/xhtml+xml")
                    else decoded
                )
                encoded_content = content.encode("utf-8")
                return FetchResult(
                    requested_url=url,
                    final_url=str(response.url),
                    content=content,
                    content_type=content_type,
                    content_hash=hashlib.sha256(encoded_content).hexdigest(),
                )
            finally:
                await response.aclose()

    async def _follow_validated_redirects(
        self, client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(str(request.url))
            if parsed.scheme not in ("http", "https") or parsed.hostname is None:
                raise FetchError("invalid_url", f"仅支持带主机名的 http(s) URL：{request.url}")
            _assert_globally_reachable(parsed.hostname)
            try:
                response = await client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise FetchError("timeout", f"HTTP 抓取超时：{request.url}") from exc
            next_request = response.next_request
            if next_request is None:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    await response.aclose()
                    raise FetchError(
                        "http_status", f"HTTP 状态异常：{exc.response.status_code}"
                    ) from exc
                return response
            await response.aclose()
            request = next_request
        raise FetchError("redirect_limit", f"重定向次数过多（超过 {_MAX_REDIRECTS} 跳）")


def _content_type(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise FetchError(
            "unsupported_content_type", f"不支持的内容类型：{content_type or '(未知)'}"
        )
    return content_type


async def _read_bounded(response: httpx.Response, *, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise FetchError("too_large", f"Content-Length 超过大小上限：{declared} > {max_bytes}")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise FetchError("too_large", f"解压后内容超过大小上限：{size} > {max_bytes}")
        chunks.append(chunk)
    return b"".join(chunks)


def create_http_source(
    *, timeout_seconds: float = 10.0, transport: httpx.AsyncBaseTransport | None = None
) -> HttpFetchSource:
    return HttpFetchSource(timeout_seconds=timeout_seconds, transport=transport)

"""web_fetch.py 测试——SSRF 防护（含逐跳重定向重验证）+ HTML 提取 + content-type 过滤。

不触真网络：``httpx.MockTransport`` 模拟传输层；``socket.getaddrinfo`` 用 monkeypatch 控制
DNS 解析结果（测试主机名 → 可控 IP），使 SSRF 检查的行为完全确定、可复现、无需真实网络。
"""

import socket
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from grandquiz.domain.learning.ingest.fetch import FetchError
from grandquiz.domain.learning.ingest.web_fetch import create_http_source, extract_text_from_html

_PUBLIC_IP_A = "93.184.216.34"  # 任意真实公网地址——只关心 is_global 判定
_PUBLIC_IP_B = "1.1.1.1"  # 注意：RFC 5737 的 TEST-NET 段（如 203.0.113.0/24）被 ipaddress 标记
# reserved、is_global 为 False，不能拿来当"公网"测试数据用。
_PRIVATE_IP = "10.0.0.5"  # RFC1918 私网
_LOOPBACK_IP = "127.0.0.1"
_LINK_LOCAL_IP = "169.254.169.254"  # 云平台 metadata 端点，经典 SSRF 目标

_AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _fake_getaddrinfo(dns_map: dict[str, str]) -> Callable[..., list[_AddrInfo]]:
    def fake(host: str, port: object, *args: object, **kwargs: object) -> list[_AddrInfo]:
        ip = dns_map.get(host)
        if ip is None:
            raise socket.gaierror(f"no fake DNS entry for {host!r}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return fake


# --- SSRF 防护：私网 / 环回 / 链路本地一律拒绝 --------------------------------------------


@pytest.mark.parametrize(
    "hostname,ip",
    [
        ("private.internal", _PRIVATE_IP),
        ("localhost.test", _LOOPBACK_IP),
        ("metadata.test", _LINK_LOCAL_IP),
    ],
)
async def test_source_rejects_non_global_hosts(
    monkeypatch: pytest.MonkeyPatch, hostname: str, ip: str
) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({hostname: ip}),
    )
    source = create_http_source(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(FetchError) as captured:
        await source.fetch(f"http://{hostname}/page", max_bytes=1024)
    assert captured.value.reason == "ssrf"


async def test_source_rejects_dns_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo", _fake_getaddrinfo({})
    )
    source = create_http_source(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(FetchError) as captured:
        await source.fetch("http://nowhere.test/page", max_bytes=1024)
    assert captured.value.reason == "ssrf"


async def test_source_rejects_non_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )
    source = create_http_source(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(FetchError) as captured:
        await source.fetch("ftp://public.test/page", max_bytes=1024)
    assert captured.value.reason == "invalid_url"


# --- 正常抓取 + 逐跳重定向重验证 --------------------------------------------------------


async def test_source_fetches_plain_text_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="hello world")

    source = create_http_source(transport=httpx.MockTransport(handler))
    result = await source.fetch("http://public.test/page", max_bytes=1024)
    assert result.content == "hello world"
    assert result.final_url == "http://public.test/page"
    assert result.content_type == "text/plain"


async def test_source_stops_stream_immediately_after_decompressed_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )
    stream = _CountingStream([b"abcd", b"efgh", b"ijkl", b"mnop"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=stream,
        )

    source = create_http_source(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError) as captured:
        await source.fetch("http://public.test/big", max_bytes=5)

    assert getattr(captured.value, "reason", None) == "too_large"
    assert stream.consumed == 2  # 读到首次越界即停，后两块从未消费
    assert stream.closed is True


async def test_source_follows_redirect_and_revalidates_each_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 两跳都是公网主机——证明重定向目标主机（不只是最初的 host）也被 SSRF 检查覆盖到。
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"start.test": _PUBLIC_IP_A, "final.test": _PUBLIC_IP_B}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "start.test":
            return httpx.Response(302, headers={"location": "http://final.test/landed"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="landed page")

    source = create_http_source(transport=httpx.MockTransport(handler))
    result = await source.fetch("http://start.test/enter", max_bytes=1024)
    assert result.content == "landed page"
    assert result.final_url == "http://final.test/landed"


async def test_source_rejects_redirect_to_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # 公网首跳、私网第二跳——经典 redirect-based SSRF：必须在第二跳就被拒，而非放行到底。
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"start.test": _PUBLIC_IP_A, "internal.test": _PRIVATE_IP}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "start.test":
            return httpx.Response(302, headers={"location": "http://internal.test/secrets"})
        return httpx.Response(200, text="should never be reached")

    source = create_http_source(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError) as captured:
        await source.fetch("http://start.test/enter", max_bytes=1024)
    assert captured.value.reason == "ssrf"


async def test_source_rejects_too_many_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"loop.test": _PUBLIC_IP_A}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://loop.test/again"})

    source = create_http_source(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError) as captured:
        await source.fetch("http://loop.test/start", max_bytes=1024)
    assert captured.value.reason == "redirect_limit"


# --- content-type 过滤 + HTML 提取 -------------------------------------------------------


async def test_source_rejects_unsupported_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    source = create_http_source(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchError) as captured:
        await source.fetch("http://public.test/file.pdf", max_bytes=1024)
    assert captured.value.reason == "unsupported_content_type"


async def test_source_extracts_text_from_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )
    html = """
    <html><head><style>body{color:red}</style><script>alert(1)</script></head>
    <body><h1>闭包</h1><article>
    <p>闭包捕获变量而非值。内部函数即使在外层函数返回后，仍然能够读取创建时词法环境里的变量。</p>
    <p>循环创建闭包时要留意晚绑定：多个函数可能在调用时读取同一个最终值，可以用默认参数冻结当前值。</p>
    <p>闭包适合封装少量状态；状态和行为逐渐复杂时，使用具有清晰类型边界的类通常更容易维护和测试。</p>
    </article></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    source = create_http_source(transport=httpx.MockTransport(handler))
    text = (await source.fetch("http://public.test/article", max_bytes=4096)).content
    assert "闭包捕获变量而非值" in text
    assert "alert(1)" not in text  # script 内容被剔除
    assert "color:red" not in text  # style 内容被剔除


def test_extract_text_from_html_strips_script_and_style() -> None:
    html = "<div><script>evil()</script><style>.x{}</style><p>正文内容</p></div>"
    assert extract_text_from_html(html) == "正文内容"


def test_extract_text_from_html_collapses_blank_lines() -> None:
    html = "<p>第一行</p>\n\n\n   \n<p>第二行</p>"
    assert extract_text_from_html(html) == "第一行\n第二行"

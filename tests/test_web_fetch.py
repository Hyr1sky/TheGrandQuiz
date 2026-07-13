"""web_fetch.py 测试——SSRF 防护（含逐跳重定向重验证）+ HTML 提取 + content-type 过滤。

不触真网络：``httpx.MockTransport`` 模拟传输层；``socket.getaddrinfo`` 用 monkeypatch 控制
DNS 解析结果（测试主机名 → 可控 IP），使 SSRF 检查的行为完全确定、可复现、无需真实网络。
"""

import socket
from collections.abc import Callable

import httpx
import pytest

from grandquiz.domain.learning.web_fetch import create_http_source, extract_text_from_html

_PUBLIC_IP_A = "93.184.216.34"  # 任意真实公网地址——只关心 is_global 判定
_PUBLIC_IP_B = "1.1.1.1"  # 注意：RFC 5737 的 TEST-NET 段（如 203.0.113.0/24）被 ipaddress 标记
# reserved、is_global 为 False，不能拿来当"公网"测试数据用。
_PRIVATE_IP = "10.0.0.5"  # RFC1918 私网
_LOOPBACK_IP = "127.0.0.1"
_LINK_LOCAL_IP = "169.254.169.254"  # 云平台 metadata 端点，经典 SSRF 目标

_AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]


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
def test_source_rejects_non_global_hosts(
    monkeypatch: pytest.MonkeyPatch, hostname: str, ip: str
) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({hostname: ip}),
    )
    source = create_http_source(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(ValueError, match="SSRF"):
        source(f"http://{hostname}/page")


def test_source_rejects_dns_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo", _fake_getaddrinfo({})
    )
    source = create_http_source(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(ValueError, match="SSRF"):
        source("http://nowhere.test/page")


def test_source_rejects_non_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )
    source = create_http_source(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(ValueError, match="http"):
        source("ftp://public.test/page")


# --- 正常抓取 + 逐跳重定向重验证 --------------------------------------------------------


def test_source_fetches_plain_text_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="hello world")

    source = create_http_source(transport=httpx.MockTransport(handler))
    assert source("http://public.test/page") == "hello world"


def test_source_follows_redirect_and_revalidates_each_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    # 两跳都是公网主机——证明重定向目标主机（不只是最初的 host）也被 SSRF 检查覆盖到。
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"start.test": _PUBLIC_IP_A, "final.test": _PUBLIC_IP_B}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "start.test":
            return httpx.Response(302, headers={"location": "http://final.test/landed"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="landed page")

    source = create_http_source(transport=httpx.MockTransport(handler))
    assert source("http://start.test/enter") == "landed page"


def test_source_rejects_redirect_to_private_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # 公网首跳、私网第二跳——经典 redirect-based SSRF：必须在第二跳就被拒，而非放行到底。
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"start.test": _PUBLIC_IP_A, "internal.test": _PRIVATE_IP}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "start.test":
            return httpx.Response(302, headers={"location": "http://internal.test/secrets"})
        return httpx.Response(200, text="should never be reached")

    source = create_http_source(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="SSRF"):
        source("http://start.test/enter")


def test_source_rejects_too_many_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"loop.test": _PUBLIC_IP_A}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://loop.test/again"})

    source = create_http_source(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="重定向"):
        source("http://loop.test/start")


# --- content-type 过滤 + HTML 提取 -------------------------------------------------------


def test_source_rejects_unsupported_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    source = create_http_source(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="内容类型"):
        source("http://public.test/file.pdf")


def test_source_extracts_text_from_html(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.web_fetch.socket.getaddrinfo",
        _fake_getaddrinfo({"public.test": _PUBLIC_IP_A}),
    )
    html = """
    <html><head><style>body{color:red}</style><script>alert(1)</script></head>
    <body><h1>闭包</h1><p>闭包捕获变量而非值。</p></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)

    source = create_http_source(transport=httpx.MockTransport(handler))
    text = source("http://public.test/article")
    assert "闭包捕获变量而非值" in text
    assert "alert(1)" not in text  # script 内容被剔除
    assert "color:red" not in text  # style 内容被剔除


def test_extract_text_from_html_strips_script_and_style() -> None:
    html = "<div><script>evil()</script><style>.x{}</style><p>正文内容</p></div>"
    assert extract_text_from_html(html) == "正文内容"


def test_extract_text_from_html_collapses_blank_lines() -> None:
    html = "<p>第一行</p>\n\n\n   \n<p>第二行</p>"
    assert extract_text_from_html(html) == "第一行\n第二行"

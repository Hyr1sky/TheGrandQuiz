"""Web Acquisition 正文抽取与质量门的公开行为测试。"""

import socket
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx
import pytest

from grandquiz.domain.learning.ingest.fetch import FetchError
from grandquiz.domain.learning.ingest.pipeline import ingest_resource
from grandquiz.domain.learning.ingest.web_fetch import create_http_source
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.harness import build_event_harness
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Role, ToolSpec

_FIXTURES = Path(__file__).parent / "fixtures" / "web"
_PUBLIC_IP = "93.184.216.34"
_AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]


def _public_dns() -> Callable[..., list[_AddrInfo]]:
    def fake(host: str, port: object, *args: object, **kwargs: object) -> list[_AddrInfo]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 0))]

    return fake


class _NeverProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        raise AssertionError("质量门失败后不得调用 Reader provider")


class _NeverApproval:
    def __init__(self) -> None:
        self.calls = 0

    def request_approval(
        self,
        candidates: list[KnowledgeItem],
        *,
        emitter: EventEmitter,
        parent_span_id: str | None,
    ) -> list[KnowledgeItem]:
        self.calls += 1
        raise AssertionError("质量门失败后不得请求审批")


async def test_html_becomes_grounded_fetched_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo", _public_dns()
    )
    html = (_FIXTURES / "article.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=html,
        )

    source = create_http_source(transport=httpx.MockTransport(handler))
    result = await source.fetch("https://docs.example.test/redirected", max_bytes=16_384)

    assert result.requested_url == "https://docs.example.test/redirected"
    assert result.final_url == "https://docs.example.test/redirected"
    assert result.canonical_url == "https://docs.example.test/python/closures"
    assert result.title == "Python 闭包指南"
    assert result.adapter == "native_http"
    assert result.extractor.startswith("trafilatura:")
    assert result.quality.accepted is True
    assert "# Python 闭包指南" in result.content
    assert "闭包捕获的是变量本身" in result.content
    assert "首页" not in result.content
    assert "接受 Cookie" not in result.content
    assert "window.analytics" not in result.content


@pytest.mark.parametrize(
    ("html", "reason"),
    [
        ("<html><body></body></html>", "empty_content"),
        (
            "<html><body><form><h1>Sign in</h1><p>Please log in to continue.</p>"
            "<input type='password'></form></body></html>",
            "login_page",
        ),
        (
            "<html><body><h1>Just a moment...</h1><p>Verify you are human</p>"
            "<p>Checking your browser before accessing the site.</p></body></html>",
            "bot_challenge",
        ),
    ],
)
async def test_low_quality_page_fails_with_stable_reason(
    monkeypatch: pytest.MonkeyPatch, html: str, reason: str
) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo", _public_dns()
    )
    source = create_http_source(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=html,
            )
        )
    )

    with pytest.raises(FetchError) as captured:
        await source.fetch("https://docs.example.test/page", max_bytes=4096)

    assert captured.value.reason == reason


async def test_quality_failure_never_reaches_reader_approval_or_kb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "grandquiz.domain.learning.ingest.web_fetch.socket.getaddrinfo", _public_dns()
    )
    login_page = (
        "<html><body><form><h1>Sign in</h1><p>Please log in to continue.</p>"
        "<input type='password'></form></body></html>"
    )
    source = create_http_source(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=login_page,
            )
        )
    )
    provider = _NeverProvider()
    approval = _NeverApproval()
    store = LearningStore()
    emitter, events, trace = build_event_harness()

    result = await ingest_resource(
        "https://docs.example.test/login",
        source=source,
        provider=provider,
        store=store,
        approval=approval,
        emitter=emitter,
        max_bytes=4096,
        allowed_domains={"docs.example.test"},
    )

    assert result.status == "failed"
    assert result.items == []
    assert provider.calls == 0
    assert approval.calls == 0
    assert EventType.MODEL_STARTED not in {event.type for event in events}
    assert store.items_for_resource(result.resource_id) == []
    resource = store.get_resource(result.resource_id)
    assert resource is not None
    assert resource.status == "failed"
    failure = next(event for event in events if event.type == "learning.resource_fetch_failed")
    assert failure.payload["classification"] == "login_page"
    trace.close()

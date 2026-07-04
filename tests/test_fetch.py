"""fetch 守卫测试（缝 2 确定性核心）——注入 source，无真实网络。

被测不变量：域名白名单、大小上限、源异常归一为 FetchError，happy path 返回内容 + 正确
content_hash。这些是注入防护 + eval case 7（失败不产幽灵 item）的地基。
"""

import hashlib

import pytest

from grandquiz.domain.learning.fetch import FetchError, fetch_resource

_ALLOWED = {"example.com", "docs.python.org"}


def test_rejects_domain_not_in_allowlist() -> None:
    with pytest.raises(FetchError):
        fetch_resource(
            "https://evil.test/page",
            source=lambda _url: "内容",
            max_bytes=1024,
            allowed_domains=_ALLOWED,
        )


def test_rejects_content_over_max_bytes() -> None:
    # 5 个中文字符 = 15 UTF-8 字节 > max_bytes=10 → 拒绝（按字节数而非字符数计）。
    with pytest.raises(FetchError):
        fetch_resource(
            "https://example.com/big",
            source=lambda _url: "一二三四五",
            max_bytes=10,
            allowed_domains=_ALLOWED,
        )


def test_wraps_source_exception_as_fetch_error() -> None:
    def _boom(_url: str) -> str:
        raise RuntimeError("网络超时")  # 模拟抓取失败

    with pytest.raises(FetchError):
        fetch_resource(
            "https://example.com/x",
            source=_boom,
            max_bytes=1024,
            allowed_domains=_ALLOWED,
        )


def test_happy_path_returns_content_and_correct_hash() -> None:
    content = "闭包捕获的是变量而非值"
    got_content, got_hash = fetch_resource(
        "https://example.com/article",
        source=lambda _url: content,
        max_bytes=1024,
        allowed_domains=_ALLOWED,
    )
    assert got_content == content
    assert got_hash == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_content_exactly_at_max_bytes_is_allowed() -> None:
    # 边界：正好等于上限不拒绝（只有严格超过才拒）。
    payload = "abcde"  # 5 ASCII 字节
    got, _ = fetch_resource(
        "https://example.com/edge",
        source=lambda _url: payload,
        max_bytes=5,
        allowed_domains=_ALLOWED,
    )
    assert got == payload

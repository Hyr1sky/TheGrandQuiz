"""网页正文抽取与确定性质量门；HTTP transport 不认识 DOM 细节。"""

import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from importlib.metadata import version

from trafilatura import extract as trafilatura_extract
from trafilatura import extract_metadata

from grandquiz.domain.learning.ingest.fetch import (
    DocumentQuality,
    FetchedDocument,
    FetchError,
    QualityFailureReason,
)

EXTRACTOR_FINGERPRINT = f"trafilatura:{version('trafilatura')}"
_BOT_SIGNALS = (
    "verify you are human",
    "checking your browser",
    "cloudflare ray id",
)
_LOGIN_SIGNALS = ("sign in", "log in", "login", "登录")


@dataclass(frozen=True)
class QualityPolicy:
    """保守的 v1 质量边界；阈值属于公开 normalization contract。"""

    min_content_chars: int = 120
    navigation_link_ratio: float = 0.55
    navigation_min_links: int = 5


_DEFAULT_QUALITY_POLICY = QualityPolicy()


@dataclass(frozen=True)
class ExtractedContent:
    title: str | None
    canonical_url: str | None
    markdown: str


class _PageSignals(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.visible_chars = 0
        self.link_chars = 0
        self.link_count = 0
        self.visible_text: list[str] = []
        self._in_link = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
        if tag == "a" and self._skip_depth == 0:
            self._in_link = True
            self.link_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "a":
            self._in_link = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        size = len("".join(data.split()))
        self.visible_chars += size
        self.visible_text.append(data)
        if self._in_link:
            self.link_chars += size


def extract_web_document(
    html: str,
    *,
    requested_url: str,
    final_url: str,
    content_type: str,
    policy: QualityPolicy = _DEFAULT_QUALITY_POLICY,
) -> FetchedDocument:
    """把受限 HTML 规范化为 Markdown，并在返回前执行 fail-closed 质量门。"""
    early_failure = _classify_page_shell(html)
    if early_failure is not None:
        _raise_quality_failure(early_failure, 0)

    extracted = _extract(html, final_url=final_url)
    quality = evaluate_document_quality(html, extracted.markdown, policy=policy)
    if not quality.accepted:
        _raise_quality_failure(quality.reasons[0], quality.content_char_count)

    content = extracted.markdown.strip()
    return FetchedDocument(
        requested_url=requested_url,
        final_url=final_url,
        canonical_url=extracted.canonical_url,
        title=extracted.title,
        content=content,
        content_type=content_type,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        adapter="native_http",
        extractor=EXTRACTOR_FINGERPRINT,
        quality=quality,
        trusted=False,
    )


def evaluate_document_quality(
    html: str, markdown: str, *, policy: QualityPolicy = _DEFAULT_QUALITY_POLICY
) -> DocumentQuality:
    """基于规范化正文与原始页面形状给出确定性、结构化质量结论。"""
    content_chars = len("".join(markdown.split()))
    reasons: list[QualityFailureReason] = []
    if _looks_like_navigation(html, policy=policy):
        reasons.append("navigation_page")
    elif content_chars == 0:
        reasons.append("empty_content")
    elif content_chars < policy.min_content_chars:
        reasons.append("too_short")
    return DocumentQuality(
        accepted=not reasons,
        reasons=reasons,
        content_char_count=content_chars,
    )


def _extract(html: str, *, final_url: str) -> ExtractedContent:
    markdown = trafilatura_extract(
        html,
        url=final_url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_links=False,
        deduplicate=True,
        favor_precision=True,
    )
    metadata = extract_metadata(html, default_url=final_url)
    return ExtractedContent(
        title=metadata.title,
        canonical_url=metadata.url,
        markdown=markdown or "",
    )


def _classify_page_shell(html: str) -> QualityFailureReason | None:
    signals = _PageSignals()
    signals.feed(html)
    visible = " ".join(" ".join(signals.visible_text).lower().split())
    if any(signal in visible for signal in _BOT_SIGNALS):
        return "bot_challenge"
    lowered_html = html.lower()
    has_password = "type='password'" in lowered_html or 'type="password"' in lowered_html
    if has_password and any(signal in visible for signal in _LOGIN_SIGNALS):
        return "login_page"
    return None


def _looks_like_navigation(html: str, *, policy: QualityPolicy) -> bool:
    signals = _PageSignals()
    signals.feed(html)
    if signals.link_count < policy.navigation_min_links or signals.visible_chars == 0:
        return False
    return signals.link_chars / signals.visible_chars >= policy.navigation_link_ratio


def _raise_quality_failure(reason: QualityFailureReason, content_chars: int) -> None:
    raise FetchError(reason, f"网页正文质量门拒绝：{reason}（正文字符数={content_chars}）")

"""Web Acquisition 规范化 Record/Replay 契约。"""

from pathlib import Path

import pytest

from grandquiz.domain.learning.ingest.acquisition_replay import (
    AcquisitionCassette,
    AcquisitionReplayMiss,
    RecordingFetchSource,
    RecordingSearchProvider,
    ReplayFetchSource,
    ReplaySearchProvider,
)
from grandquiz.domain.learning.ingest.fetch import (
    DocumentQuality,
    FetchedDocument,
    FetchError,
)
from grandquiz.domain.learning.ingest.web_search import SearchResult

_FINGERPRINT = "sha256:public-adapter-config"


class _LiveSearch:
    adapter_name = "searxng"

    async def search(
        self, query: str, *, limit: int, domains: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        return [
            SearchResult(
                title="Agent Runtime Guide",
                url="https://guide.example/runtime",
                snippet="Events and replay",
                adapter=self.adapter_name,
                rank=1,
            )
        ]


class _LiveFetch:
    async def fetch(self, url: str, *, max_bytes: int) -> FetchedDocument:
        return FetchedDocument(
            requested_url=url,
            final_url=url,
            canonical_url="https://guide.example/runtime",
            title="Agent Runtime Guide",
            content="# Runtime\n\nAgent events can be recorded and replayed.",
            content_type="text/html",
            content_hash="abc123",
            adapter="native_http",
            extractor="trafilatura:2.1.0",
            quality=DocumentQuality(content_char_count=48),
        )


class _FailingFetch:
    async def fetch(self, url: str, *, max_bytes: int) -> FetchedDocument:
        raise FetchError("bot_challenge", "网页正文质量门拒绝：bot_challenge")


async def test_normalized_search_and_fetch_round_trip_offline(tmp_path: Path) -> None:
    cassette = AcquisitionCassette()
    search_recorder = RecordingSearchProvider(
        _LiveSearch(), cassette, adapter_fingerprint=_FINGERPRINT
    )
    fetch_recorder = RecordingFetchSource(
        _LiveFetch(),
        cassette,
        adapter_fingerprint=_FINGERPRINT,
        normalization_version="trafilatura:2.1.0/web-v1",
    )
    expected_search = await search_recorder.search(
        "agent runtime", limit=3, domains=("guide.example",)
    )
    expected_fetch = await fetch_recorder.fetch(
        "https://guide.example/runtime", max_bytes=4096
    )
    path = tmp_path / "acquisition.cassette.json"
    cassette.save(path)

    offline = AcquisitionCassette.load(path)
    search_replay = ReplaySearchProvider(
        offline,
        adapter_name="searxng",
        adapter_fingerprint=_FINGERPRINT,
    )
    fetch_replay = ReplayFetchSource(
        offline,
        adapter_fingerprint=_FINGERPRINT,
        normalization_version="trafilatura:2.1.0/web-v1",
    )

    assert await search_replay.search(
        "agent runtime", limit=3, domains=("guide.example",)
    ) == expected_search
    assert (
        await fetch_replay.fetch("https://guide.example/runtime", max_bytes=4096)
        == expected_fetch
    )
    stored = path.read_text(encoding="utf-8")
    assert "Authorization" not in stored
    assert "Bearer" not in stored


async def test_normalization_change_misses_loudly() -> None:
    cassette = AcquisitionCassette()
    recorder = RecordingFetchSource(
        _LiveFetch(),
        cassette,
        adapter_fingerprint=_FINGERPRINT,
        normalization_version="trafilatura:2.1.0/web-v1",
    )
    await recorder.fetch("https://guide.example/runtime", max_bytes=4096)
    replay = ReplayFetchSource(
        cassette,
        adapter_fingerprint=_FINGERPRINT,
        normalization_version="trafilatura:2.2.0/web-v2",
    )

    with pytest.raises(AcquisitionReplayMiss):
        await replay.fetch("https://guide.example/runtime", max_bytes=4096)


async def test_quality_failure_is_recorded_and_replayed() -> None:
    cassette = AcquisitionCassette()
    recorder = RecordingFetchSource(
        _FailingFetch(),
        cassette,
        adapter_fingerprint=_FINGERPRINT,
        normalization_version="trafilatura:2.1.0/web-v1",
    )
    with pytest.raises(FetchError, match="bot_challenge"):
        await recorder.fetch("https://guide.example/challenge", max_bytes=4096)

    replay = ReplayFetchSource(
        cassette,
        adapter_fingerprint=_FINGERPRINT,
        normalization_version="trafilatura:2.1.0/web-v1",
    )
    with pytest.raises(FetchError) as captured:
        await replay.fetch("https://guide.example/challenge", max_bytes=4096)
    assert captured.value.reason == "bot_challenge"

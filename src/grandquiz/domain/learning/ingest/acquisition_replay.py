"""Search / Fetch 外部边界的规范化 Record/Replay。"""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from grandquiz.domain.learning.ingest.fetch import (
    BoundedFetchSource,
    FetchedDocument,
    FetchError,
    FetchFailureReason,
)
from grandquiz.domain.learning.ingest.web_search import SearchProvider, SearchResult
from grandquiz.kernel.recovery import ErrorClass

_CASSETTE_VERSION = 1
_SEARCH_NORMALIZATION = "search-v1"
AcquisitionKind = Literal["search", "fetch"]


class AcquisitionReplayMiss(Exception):
    """外部 acquisition cassette 未命中；harness 配置错误必须大声失败。"""

    error_class = ErrorClass.FATAL


class AcquisitionCassette:
    """只保存规范化内部模型、稳定请求和公开 adapter 指纹的 JSON cassette。"""

    def __init__(self, entries: dict[str, dict[str, Any]] | None = None) -> None:
        self._entries = entries if entries is not None else {}

    @classmethod
    def load(cls, path: str | Path) -> "AcquisitionCassette":
        raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Acquisition cassette 顶层必须是 JSON object")
        return cls(cast("dict[str, dict[str, Any]]", raw))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self._entries, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def put(self, key: str, entry: Mapping[str, Any]) -> None:
        self._entries[key] = dict(entry)

    def get(self, key: str) -> dict[str, Any] | None:
        return self._entries.get(key)


def acquisition_key(
    kind: AcquisitionKind,
    request: Mapping[str, Any],
    *,
    adapter_fingerprint: str,
    normalization_version: str,
) -> str:
    """按公开执行信封算稳定键；不接收 header、credential 或客户端对象。"""
    envelope = json.dumps(
        {
            "cassette_version": _CASSETTE_VERSION,
            "kind": kind,
            "request": dict(request),
            "adapter_fingerprint": adapter_fingerprint,
            "normalization_version": normalization_version,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(envelope.encode("utf-8")).hexdigest()


def _search_request(query: str, limit: int, domains: tuple[str, ...]) -> dict[str, Any]:
    return {
        "query": " ".join(query.split()),
        "limit": limit,
        "domains": sorted(set(domain.casefold().rstrip(".") for domain in domains)),
    }


def _fetch_request(url: str, max_bytes: int) -> dict[str, Any]:
    return {"url": url, "max_bytes": max_bytes}


class RecordingSearchProvider:
    """透传真实 SearchProvider，并把规范化候选写入 cassette。"""

    def __init__(
        self,
        inner: SearchProvider,
        cassette: AcquisitionCassette,
        *,
        adapter_fingerprint: str,
        normalization_version: str = _SEARCH_NORMALIZATION,
    ) -> None:
        self._inner = inner
        self._cassette = cassette
        self._adapter_fingerprint = adapter_fingerprint
        self._normalization_version = normalization_version
        self.adapter_name = inner.adapter_name

    async def search(
        self, query: str, *, limit: int, domains: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        request = _search_request(query, limit, domains)
        key = acquisition_key(
            "search",
            request,
            adapter_fingerprint=self._adapter_fingerprint,
            normalization_version=self._normalization_version,
        )
        results = await self._inner.search(query, limit=limit, domains=domains)
        self._cassette.put(
            key,
            {
                "cassette_version": _CASSETTE_VERSION,
                "kind": "search",
                "adapter_fingerprint": self._adapter_fingerprint,
                "normalization_version": self._normalization_version,
                "request": request,
                "results": [result.model_dump() for result in results],
            },
        )
        return results


class ReplaySearchProvider:
    """纯回放 SearchProvider；未命中或 entry 形状错误均大声失败。"""

    def __init__(
        self,
        cassette: AcquisitionCassette,
        *,
        adapter_name: str,
        adapter_fingerprint: str,
        normalization_version: str = _SEARCH_NORMALIZATION,
    ) -> None:
        self._cassette = cassette
        self.adapter_name = adapter_name
        self._adapter_fingerprint = adapter_fingerprint
        self._normalization_version = normalization_version

    async def search(
        self, query: str, *, limit: int, domains: tuple[str, ...] = ()
    ) -> list[SearchResult]:
        request = _search_request(query, limit, domains)
        key = acquisition_key(
            "search",
            request,
            adapter_fingerprint=self._adapter_fingerprint,
            normalization_version=self._normalization_version,
        )
        entry = self._cassette.get(key)
        if entry is None:
            raise AcquisitionReplayMiss(f"search 回放未命中：key={key[:12]}…")
        try:
            raw_results = cast("list[object]", entry["results"])
            return [SearchResult.model_validate(result) for result in raw_results]
        except (KeyError, TypeError, ValueError) as exc:
            raise AcquisitionReplayMiss(f"search cassette entry 无效：key={key[:12]}…") from exc


class RecordingFetchSource:
    """透传真实有界 Fetch source，记录规范化文档或稳定失败分类。"""

    def __init__(
        self,
        inner: BoundedFetchSource,
        cassette: AcquisitionCassette,
        *,
        adapter_fingerprint: str,
        normalization_version: str,
    ) -> None:
        self._inner = inner
        self._cassette = cassette
        self._adapter_fingerprint = adapter_fingerprint
        self._normalization_version = normalization_version

    async def fetch(self, url: str, *, max_bytes: int) -> FetchedDocument:
        request = _fetch_request(url, max_bytes)
        key = acquisition_key(
            "fetch",
            request,
            adapter_fingerprint=self._adapter_fingerprint,
            normalization_version=self._normalization_version,
        )
        base_entry: dict[str, Any] = {
            "cassette_version": _CASSETTE_VERSION,
            "kind": "fetch",
            "adapter_fingerprint": self._adapter_fingerprint,
            "normalization_version": self._normalization_version,
            "request": request,
        }
        try:
            document = await self._inner.fetch(url, max_bytes=max_bytes)
        except FetchError as exc:
            self._cassette.put(
                key,
                {
                    **base_entry,
                    "failure": {"reason": exc.reason, "message": str(exc)},
                },
            )
            raise
        self._cassette.put(key, {**base_entry, "document": document.model_dump()})
        return document


class ReplayFetchSource:
    """纯回放有界 Fetch source；复原规范化文档或同一稳定失败。"""

    def __init__(
        self,
        cassette: AcquisitionCassette,
        *,
        adapter_fingerprint: str,
        normalization_version: str,
    ) -> None:
        self._cassette = cassette
        self._adapter_fingerprint = adapter_fingerprint
        self._normalization_version = normalization_version

    async def fetch(self, url: str, *, max_bytes: int) -> FetchedDocument:
        request = _fetch_request(url, max_bytes)
        key = acquisition_key(
            "fetch",
            request,
            adapter_fingerprint=self._adapter_fingerprint,
            normalization_version=self._normalization_version,
        )
        entry = self._cassette.get(key)
        if entry is None:
            raise AcquisitionReplayMiss(f"fetch 回放未命中：key={key[:12]}…")
        failure = entry.get("failure")
        if isinstance(failure, Mapping):
            failure_data = cast("Mapping[str, object]", failure)
            reason_value = failure_data.get("reason")
            if not isinstance(reason_value, str):
                raise AcquisitionReplayMiss(f"fetch failure entry 无效：key={key[:12]}…")
            reason = cast("FetchFailureReason", reason_value)
            message = str(failure_data.get("message", reason))
            raise FetchError(reason, message)
        try:
            return FetchedDocument.model_validate(entry["document"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AcquisitionReplayMiss(f"fetch cassette entry 无效：key={key[:12]}…") from exc

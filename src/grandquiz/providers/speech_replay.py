"""Record/Replay adapters for deterministic speech-recognition tests and dogfood."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import cast

from grandquiz.providers.speech import (
    SpeechRecognitionError,
    SpeechRecognitionErrorCode,
    SpeechRecognitionProvider,
    TranscriptionRequest,
    TranscriptionResult,
)

_SCHEMA_VERSION = "speech-cassette.v1"


class SpeechReplayMiss(KeyError):
    pass


def _request_identity(request: TranscriptionRequest, provider_identity: str) -> dict[str, object]:
    return {
        "provider_identity": provider_identity,
        "audio_sha256": hashlib.sha256(request.audio_bytes).hexdigest(),
        "audio_bytes": len(request.audio_bytes),
        "mime_type": request.mime_type,
        "locale_hints": list(request.locale_hints),
        "material_hints_enabled": request.material_hints_enabled,
        "hint_set_id": request.hints.hint_set_id,
        "hints": [entry.model_dump(mode="json") for entry in request.hints.entries],
    }


def _replay_key(request: TranscriptionRequest, provider_identity: str) -> str:
    canonical = json.dumps(
        _request_identity(request, provider_identity),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _empty_cassette() -> dict[str, object]:
    return {"schema_version": _SCHEMA_VERSION, "records": []}


def _load(path: Path) -> dict[str, object]:
    if not path.exists():
        return _empty_cassette()
    raw_object: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_object, dict):
        raise ValueError("speech cassette root must be an object")
    raw = cast("dict[str, object]", raw_object)
    if raw.get("schema_version") != _SCHEMA_VERSION or not isinstance(raw.get("records"), list):
        raise ValueError("unsupported speech cassette schema")
    return raw


def _save(path: Path, cassette: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(cassette, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class SpeechRecordingProvider:
    """Wrap a live adapter and append safe result/error records after each attempt."""

    def __init__(
        self,
        provider: SpeechRecognitionProvider,
        cassette_path: str | Path,
        *,
        provider_identity: str | None = None,
    ) -> None:
        self._provider = provider
        self._path = Path(cassette_path)
        inferred = getattr(provider, "provider_identity", type(provider).__name__)
        self._provider_identity = provider_identity or str(inferred)

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        identity = _request_identity(request, self._provider_identity)
        key = _replay_key(request, self._provider_identity)
        try:
            result = await self._provider.transcribe(request)
        except SpeechRecognitionError as exc:
            self._append(
                {
                    "key": key,
                    "request": identity,
                    "error": {
                        "code": exc.code,
                        "reason": exc.reason,
                        "retryable": exc.retryable,
                        "provider_status": exc.provider_status,
                        "provider_request_id": exc.provider_request_id,
                    },
                }
            )
            raise
        self._append(
            {
                "key": key,
                "request": identity,
                "result": result.model_dump(mode="json"),
            }
        )
        return result

    def _append(self, record: dict[str, object]) -> None:
        cassette = _load(self._path)
        records = cast("list[object]", cassette["records"])
        records.append(record)
        _save(self._path, cassette)


class SpeechReplayProvider:
    """Replay ordered responses for each canonical request without external I/O."""

    def __init__(
        self,
        cassette_path: str | Path,
        *,
        provider_identity: str = "_SpeechFake",
    ) -> None:
        cassette = _load(Path(cassette_path))
        self._provider_identity = provider_identity
        self._records: dict[str, list[dict[str, object]]] = defaultdict(list)
        for raw in cast("list[object]", cassette["records"]):
            if not isinstance(raw, dict):
                raise ValueError("invalid speech cassette record")
            record = cast("dict[str, object]", raw)
            if not isinstance(record.get("key"), str):
                raise ValueError("invalid speech cassette record")
            self._records[str(record["key"])].append(record)
        self._cursors: dict[str, int] = defaultdict(int)

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        key = _replay_key(request, self._provider_identity)
        records = self._records.get(key, [])
        cursor = self._cursors[key]
        if cursor >= len(records):
            raise SpeechReplayMiss(key)
        self._cursors[key] += 1
        record = records[cursor]
        error = record.get("error")
        if isinstance(error, dict):
            error_record = cast("dict[str, object]", error)
            code = cast("SpeechRecognitionErrorCode", error_record["code"])
            provider_status = error_record.get("provider_status")
            provider_request_id = error_record.get("provider_request_id")
            raise SpeechRecognitionError(
                code,
                str(error_record["reason"]),
                retryable=bool(error_record["retryable"]),
                provider_status=(None if provider_status is None else int(str(provider_status))),
                provider_request_id=(
                    None if provider_request_id is None else str(provider_request_id)
                ),
            )
        result = record.get("result")
        if not isinstance(result, dict):
            raise ValueError("speech cassette record has no result or error")
        return TranscriptionResult.model_validate(cast("dict[str, object]", result))

"""SpeechRecognitionProvider adapters through their shared public interface."""

import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from grandquiz.domain.learning.recognition_lexicon import (
    TranscriptionHintEntry,
    TranscriptionHints,
)
from grandquiz.providers.dashscope_speech import DashScopeSpeechRecognitionAdapter
from grandquiz.providers.speech import (
    SpeechRecognitionError,
    TranscriptionRequest,
    TranscriptionResult,
)
from grandquiz.providers.speech_replay import SpeechRecordingProvider, SpeechReplayProvider


def _hints() -> TranscriptionHints:
    return TranscriptionHints(
        hint_set_id="hints-1",
        lexicon_ids=("lexicon-1",),
        item_ids=("item-1",),
        selector_version="selector.v1",
        entries=(
            TranscriptionHintEntry(entry_id="entry-1", term="ReAct", priority=5),
            TranscriptionHintEntry(entry_id="entry-2", term="AgentEvent", priority=4),
        ),
    )


@pytest.mark.asyncio
async def test_dashscope_adapter_maps_provider_request_and_normalizes_result() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "request_id": "provider-request-1",
                "output": {
                    "output": {
                        "sentence": {
                            "text": "ReAct 使用 AgentEvent。",
                            "sentence_end": True,
                        }
                    }
                },
                "usage": {"duration": 12},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DashScopeSpeechRecognitionAdapter(
            api_key="secret",
            workspace_id="workspace-1",
            region="cn-beijing",
            client=client,
            monotonic=iter((10.0, 10.125)).__next__,
        )
        result = await adapter.transcribe(
            TranscriptionRequest(
                audio_bytes=b"webm-audio",
                mime_type="audio/webm;codecs=opus",
                hints=_hints(),
                material_hints_enabled=True,
                timeout_seconds=30,
            )
        )

    assert captured["url"] == (
        "https://workspace-1.cn-beijing.maas.aliyuncs.com/"
        "api/v1/services/aigc/multimodal-generation/generation"
    )
    assert captured["authorization"] == "Bearer secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen-audio-3.0-asr-flash"
    assert payload["parameters"] == {
        "format": "webm",
        "language_hints": ["zh", "en"],
        "vocabulary": {"ReAct": 5, "AgentEvent": 4},
    }
    assert result.transcript == "ReAct 使用 AgentEvent。"
    assert result.provider_request_id == "provider-request-1"
    assert result.provider_audio_duration_ms == 12_000
    assert result.latency_ms == 125


@pytest.mark.asyncio
async def test_dashscope_adapter_keeps_hints_disabled_until_quality_gate() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"request_id": "no-hints-1", "output": {"text": "普通转写"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DashScopeSpeechRecognitionAdapter(
            api_key="secret",
            workspace_id="workspace-1",
            client=client,
            monotonic=iter((10.0, 10.1)).__next__,
        )
        await adapter.transcribe(
            TranscriptionRequest(
                audio_bytes=b"webm-audio",
                mime_type="audio/webm;codecs=opus",
                hints=_hints(),
            )
        )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    parameters = cast("dict[str, object]", payload)["parameters"]
    assert isinstance(parameters, dict)
    assert "vocabulary" not in parameters


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable"),
    [
        (403, "provider_auth", False),
        (429, "provider_rate_limited", True),
        (503, "provider_unavailable", True),
    ],
)
@pytest.mark.asyncio
async def test_dashscope_adapter_maps_safe_http_errors(
    status: int,
    expected_code: str,
    retryable: bool,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"request_id": "provider-error-1", "message": "sensitive provider detail"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = DashScopeSpeechRecognitionAdapter(
            api_key="secret",
            workspace_id="workspace-1",
            client=client,
        )
        with pytest.raises(SpeechRecognitionError) as captured:
            await adapter.transcribe(
                TranscriptionRequest(
                    audio_bytes=b"webm-audio",
                    mime_type="audio/webm;codecs=opus",
                    hints=_hints(),
                )
            )

    assert captured.value.code == expected_code
    assert captured.value.retryable is retryable
    assert captured.value.provider_request_id == "provider-error-1"
    assert "sensitive provider detail" not in str(captured.value)


class _SpeechFake:
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        return TranscriptionResult(
            transcript="ReAct 使用 AgentEvent。",
            provider_request_id="provider-request-1",
            provider_audio_duration_ms=12_000,
            latency_ms=125,
        )


@pytest.mark.asyncio
async def test_speech_record_replay_uses_audio_hash_without_storing_audio(tmp_path: Path) -> None:
    cassette = tmp_path / "speech.json"
    request = TranscriptionRequest(
        audio_bytes=b"private-webm-audio",
        mime_type="audio/webm;codecs=opus",
        hints=_hints(),
    )
    recorder = SpeechRecordingProvider(_SpeechFake(), cassette)

    live = await recorder.transcribe(request)
    raw = cassette.read_text(encoding="utf-8")
    replay = await SpeechReplayProvider(cassette).transcribe(request)

    assert replay == live
    assert "private-webm-audio" not in raw
    assert request.audio_bytes.hex() not in raw
    assert "audio_sha256" in raw


@pytest.mark.asyncio
async def test_dashscope_adapter_maps_timeout_and_malformed_success() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        adapter = DashScopeSpeechRecognitionAdapter(
            api_key="secret",
            workspace_id="workspace-1",
            client=client,
        )
        with pytest.raises(SpeechRecognitionError) as timeout_error:
            await adapter.transcribe(
                TranscriptionRequest(
                    audio_bytes=b"webm-audio",
                    mime_type="audio/webm;codecs=opus",
                    hints=_hints(),
                )
            )
    assert timeout_error.value.code == "provider_timeout"
    assert timeout_error.value.retryable is True

    def malformed_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"request_id": "malformed-1", "output": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(malformed_handler)) as client:
        adapter = DashScopeSpeechRecognitionAdapter(
            api_key="secret",
            workspace_id="workspace-1",
            client=client,
        )
        with pytest.raises(SpeechRecognitionError) as malformed_error:
            await adapter.transcribe(
                TranscriptionRequest(
                    audio_bytes=b"webm-audio",
                    mime_type="audio/webm;codecs=opus",
                    hints=_hints(),
                )
            )
    assert malformed_error.value.code == "provider_unavailable"
    assert malformed_error.value.retryable is False


@pytest.mark.asyncio
async def test_dashscope_adapter_rejects_unsupported_media_before_network() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: pytest.fail("network must not run"))
    ) as client:
        adapter = DashScopeSpeechRecognitionAdapter(
            api_key="secret",
            workspace_id="workspace-1",
            client=client,
        )
        with pytest.raises(SpeechRecognitionError) as captured:
            await adapter.transcribe(
                TranscriptionRequest(
                    audio_bytes=b"wav-audio",
                    mime_type="audio/wav",
                    hints=_hints(),
                )
            )

    assert captured.value.code == "unsupported_media"
    assert captured.value.retryable is False


@pytest.mark.parametrize("audio", [b"", b"x" * 7_000_001])
def test_transcription_request_rejects_empty_or_oversized_audio(audio: bytes) -> None:
    with pytest.raises(ValidationError):
        TranscriptionRequest(
            audio_bytes=audio,
            mime_type="audio/webm;codecs=opus",
            hints=_hints(),
        )


class _SpeechErrorFake:
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        raise SpeechRecognitionError(
            "provider_rate_limited",
            "语音服务限流",
            retryable=True,
            provider_status=429,
            provider_request_id="provider-error-2",
        )


@pytest.mark.asyncio
async def test_speech_record_replay_preserves_safe_error_contract(tmp_path: Path) -> None:
    cassette = tmp_path / "speech-error.json"
    request = TranscriptionRequest(
        audio_bytes=b"private-webm-audio",
        mime_type="audio/webm;codecs=opus",
        hints=_hints(),
    )
    recorder = SpeechRecordingProvider(_SpeechErrorFake(), cassette)

    with pytest.raises(SpeechRecognitionError):
        await recorder.transcribe(request)
    with pytest.raises(SpeechRecognitionError) as replayed:
        await SpeechReplayProvider(
            cassette,
            provider_identity="_SpeechErrorFake",
        ).transcribe(request)

    assert replayed.value.code == "provider_rate_limited"
    assert replayed.value.retryable is True
    assert replayed.value.provider_status == 429
    assert replayed.value.provider_request_id == "provider-error-2"

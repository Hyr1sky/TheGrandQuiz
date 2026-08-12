"""DashScope Qwen Audio complete-file speech-recognition Adapter."""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Callable
from typing import Any, cast

import httpx

from grandquiz.providers.speech import (
    SpeechRecognitionError,
    SpeechRecognitionErrorCode,
    TranscriptionRequest,
    TranscriptionResult,
)

DEFAULT_MODEL = "qwen-audio-3.0-asr-flash"
_REGION_HOSTS = {
    "cn-beijing": "cn-beijing.maas.aliyuncs.com",
    "ap-southeast-1": "ap-southeast-1.maas.aliyuncs.com",
}
_MIME_TO_FORMAT = {"audio/webm": "webm"}


def _base_mime(value: str) -> str:
    return value.split(";", maxsplit=1)[0].strip().casefold()


def _mapping(value: object) -> dict[str, Any] | None:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _transcript(payload: dict[str, Any]) -> str:
    output = _mapping(payload.get("output"))
    if output is None:
        return ""
    direct = output.get("text")
    if isinstance(direct, str):
        return direct.strip()
    nested = _mapping(output.get("output"))
    sentence = None if nested is None else _mapping(nested.get("sentence"))
    text = None if sentence is None else sentence.get("text")
    return text.strip() if isinstance(text, str) else ""


def _audio_duration_ms(payload: dict[str, Any]) -> int | None:
    usage = _mapping(payload.get("usage"))
    duration = None if usage is None else usage.get("duration")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        return round(float(duration) * 1000)
    return None


def _error_for_status(status: int) -> tuple[SpeechRecognitionErrorCode, str, bool]:
    if status in {401, 403}:
        return "provider_auth", "语音 Provider 鉴权或访问范围无效", False
    if status == 429:
        return "provider_rate_limited", "语音 Provider 当前请求过多", True
    if status in {408, 504}:
        return "provider_timeout", "语音 Provider 处理超时", True
    return "provider_unavailable", "语音 Provider 暂时不可用", status >= 500


class DashScopeSpeechRecognitionAdapter:
    """Translate the stable speech interface to DashScope's multimodal JSON dialect."""

    def __init__(
        self,
        *,
        api_key: str,
        workspace_id: str,
        region: str = "cn-beijing",
        model: str = DEFAULT_MODEL,
        client: httpx.AsyncClient | None = None,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DASHSCOPE_API_KEY 不能为空")
        if not workspace_id.strip():
            raise ValueError("DASHSCOPE_WORKSPACE_ID 不能为空")
        if region not in _REGION_HOSTS:
            raise ValueError(f"不支持的 DASHSCOPE_REGION：{region}")
        self._api_key = api_key.strip()
        self._workspace_id = workspace_id.strip()
        self._region = region
        self._model = model
        self._client = client
        self._monotonic = monotonic

    @classmethod
    def from_env(cls) -> DashScopeSpeechRecognitionAdapter:
        return cls(
            api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            workspace_id=os.environ.get("DASHSCOPE_WORKSPACE_ID", ""),
            region=os.environ.get("DASHSCOPE_REGION", "cn-beijing"),
            model=os.environ.get("ASR_MODEL", DEFAULT_MODEL),
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def region(self) -> str:
        return self._region

    @property
    def provider_identity(self) -> str:
        return f"dashscope|{self._region}|{self._model}"

    def _url(self) -> str:
        host = _REGION_HOSTS[self._region]
        return (
            f"https://{self._workspace_id}.{host}"
            "/api/v1/services/aigc/multimodal-generation/generation"
        )

    async def _post(
        self,
        payload: dict[str, Any],
        *,
        timeout: httpx.Timeout,
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {
            "headers": {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-DashScope-SSE": "disable",
            },
            "json": payload,
            "timeout": timeout,
        }
        if self._client is not None:
            return await self._client.post(self._url(), **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.post(self._url(), **kwargs)

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        mime = _base_mime(request.mime_type)
        audio_format = _MIME_TO_FORMAT.get(mime)
        if audio_format is None:
            raise SpeechRecognitionError(
                "unsupported_media",
                "v0.5 只支持桌面 Chromium 生成的 WebM/Opus 录音",
                retryable=False,
            )
        encoded = base64.b64encode(request.audio_bytes).decode("ascii")
        parameters: dict[str, Any] = {
            "format": audio_format,
            "language_hints": list(request.locale_hints),
        }
        if request.material_hints_enabled and request.hints.entries:
            parameters["vocabulary"] = {
                entry.term: entry.priority for entry in request.hints.entries
            }
        payload = {
            "model": self._model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": f"data:{mime};base64,{encoded}"},
                            }
                        ],
                    }
                ]
            },
            "parameters": parameters,
        }
        timeout = httpx.Timeout(
            connect=min(5.0, request.timeout_seconds),
            write=min(15.0, request.timeout_seconds),
            read=request.timeout_seconds,
            pool=min(5.0, request.timeout_seconds),
        )
        started = self._monotonic()
        try:
            response = await self._post(payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise SpeechRecognitionError(
                "provider_timeout",
                "语音 Provider 处理超时",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise SpeechRecognitionError(
                "provider_unavailable",
                "语音 Provider 网络不可用",
                retryable=True,
            ) from exc
        latency_ms = max(0, round((self._monotonic() - started) * 1000))
        try:
            body_object: object = response.json()
        except ValueError as exc:
            raise SpeechRecognitionError(
                "provider_unavailable",
                "语音 Provider 返回了无效响应",
                retryable=response.status_code >= 500,
                provider_status=response.status_code,
            ) from exc
        body = _mapping(body_object)
        if body is None:
            raise SpeechRecognitionError(
                "provider_unavailable",
                "语音 Provider 返回了无效响应",
                retryable=False,
                provider_status=response.status_code,
            )
        provider_request_id = body.get("request_id")
        request_id = provider_request_id if isinstance(provider_request_id, str) else None
        if response.is_error:
            code, reason, retryable = _error_for_status(response.status_code)
            raise SpeechRecognitionError(
                code,
                reason,
                retryable=retryable,
                provider_status=response.status_code,
                provider_request_id=request_id,
            )
        transcript = _transcript(body)
        if not transcript:
            raise SpeechRecognitionError(
                "provider_unavailable",
                "语音 Provider 没有返回可用 transcript",
                retryable=False,
                provider_status=response.status_code,
                provider_request_id=request_id,
            )
        return TranscriptionResult(
            transcript=transcript,
            provider_request_id=request_id,
            provider_audio_duration_ms=_audio_duration_ms(body),
            latency_ms=latency_ms,
        )

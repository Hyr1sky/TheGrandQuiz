"""Raw-audio HTTP boundary for the persistent VoiceRun workflow."""

from typing import Annotated, cast

from fastapi import APIRouter, Body, Header, Request, Response, status
from pydantic import BaseModel

from grandquiz.interfaces.api.errors import ApiError, ErrorResponse
from grandquiz.interfaces.api.voice_runs import (
    VoiceRunCommandConflict,
    VoiceRunManager,
    VoiceRunStartCommand,
    VoiceRunSubmitCommand,
    VoiceRunView,
)
from grandquiz.providers.speech import MAX_AUDIO_BYTES

router = APIRouter(tags=["voice"])
_SUPPORTED_MIME = "audio/webm;codecs=opus"
_AUDIO_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    413: {"model": ErrorResponse, "description": "Audio payload exceeds the v0.5 limit"},
    415: {"model": ErrorResponse, "description": "Unsupported audio media type"},
    422: {"model": ErrorResponse, "description": "Invalid audio recording or request"},
}


class VoiceRuntimeConfig(BaseModel):
    enabled: bool
    mime_types: list[str]
    max_duration_ms: int
    max_audio_bytes: int
    max_provider_attempts: int
    review_ttl_seconds: int
    max_hint_entries: int
    hints_enabled: bool


def voice_manager_from(request: Request) -> VoiceRunManager:
    manager = getattr(request.app.state, "voice_run_manager", None)
    if manager is None:
        raise ApiError(
            status_code=503,
            code="speech_recognition_not_configured",
            message="语音识别尚未配置",
            retryable=False,
        )
    return cast("VoiceRunManager", manager)


def _validate_audio_boundary(request: Request, audio_bytes: bytes) -> None:
    content_type = request.headers.get("content-type", "").strip().casefold()
    if content_type != _SUPPORTED_MIME:
        raise ApiError(
            status_code=415,
            code="unsupported_media",
            message="v0.5 只支持 audio/webm;codecs=opus",
            retryable=False,
        )
    if not audio_bytes:
        raise ApiError(
            status_code=422,
            code="invalid_audio",
            message="录音不能为空",
            retryable=False,
        )
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ApiError(
            status_code=413,
            code="payload_too_large",
            message=f"录音不能超过 {MAX_AUDIO_BYTES} bytes",
            retryable=False,
        )


@router.get("/api/v1/voice/config", response_model=VoiceRuntimeConfig)
async def get_voice_runtime_config(request: Request) -> VoiceRuntimeConfig:
    manager = getattr(request.app.state, "voice_run_manager", None)
    return VoiceRuntimeConfig(
        enabled=manager is not None,
        mime_types=["audio/webm;codecs=opus"],
        max_duration_ms=90_000,
        max_audio_bytes=MAX_AUDIO_BYTES,
        max_provider_attempts=2,
        review_ttl_seconds=30 * 60,
        max_hint_entries=50,
        hints_enabled=False if manager is None else manager.hints_enabled,
    )


@router.post(
    "/api/v1/assessments/{session_id}/questions/{question_id}/voice-runs",
    response_model=VoiceRunView,
    status_code=202,
    responses={409: {"model": ErrorResponse}, **_AUDIO_ERROR_RESPONSES},
)
async def start_voice_run(
    session_id: str,
    question_id: str,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    client_duration_ms: Annotated[int, Header(alias="X-Client-Duration-Ms", ge=1)],
    audio_bytes: Annotated[bytes, Body(media_type="audio/webm")] = b"",
) -> VoiceRunView:
    _validate_audio_boundary(request, audio_bytes)
    try:
        command = VoiceRunStartCommand.model_validate(
            {
                "request_id": idempotency_key,
                "assessment_session_id": session_id,
                "question_id": question_id,
                "mime_type": request.headers.get("content-type", ""),
                "client_duration_ms": client_duration_ms,
            }
        )
        return voice_manager_from(request).start(command, audio_bytes)
    except VoiceRunCommandConflict as exc:
        raise ApiError(
            status_code=409,
            code="voice_run_conflict",
            message=str(exc),
        ) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=422,
            code="invalid_voice_recording",
            message=str(exc),
        ) from exc


@router.get("/api/v1/voice-runs/{voice_run_id}", response_model=VoiceRunView)
async def get_voice_run(voice_run_id: str, request: Request) -> VoiceRunView:
    voice_run = voice_manager_from(request).get(voice_run_id)
    if voice_run is None:
        raise ApiError(
            status_code=404,
            code="voice_run_not_found",
            message=f"语音任务不存在：{voice_run_id}",
        )
    return voice_run


@router.delete("/api/v1/voice-runs/{voice_run_id}", response_model=VoiceRunView)
async def cancel_voice_run(voice_run_id: str, request: Request) -> VoiceRunView:
    voice_run = voice_manager_from(request).cancel(voice_run_id)
    if voice_run is None:
        raise ApiError(
            status_code=404,
            code="voice_run_not_found",
            message=f"语音任务不存在：{voice_run_id}",
        )
    return voice_run


@router.delete(
    "/api/v1/voice-requests/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_voice_request(request_id: str, request: Request) -> Response:
    """Reserve cancellation even when an upload has not returned its VoiceRun ID yet."""

    voice_manager_from(request).cancel_request(request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v1/voice-runs/{voice_run_id}/retry",
    response_model=VoiceRunView,
    status_code=202,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        **_AUDIO_ERROR_RESPONSES,
    },
)
async def retry_voice_run(
    voice_run_id: str,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    audio_bytes: Annotated[bytes, Body(media_type="audio/webm")] = b"",
) -> VoiceRunView:
    _validate_audio_boundary(request, audio_bytes)
    try:
        return voice_manager_from(request).retry(
            voice_run_id,
            request_id=idempotency_key,
            audio_bytes=audio_bytes,
        )
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="voice_run_not_found",
            message=f"语音任务不存在：{voice_run_id}",
        ) from exc
    except VoiceRunCommandConflict as exc:
        raise ApiError(
            status_code=409,
            code="voice_retry_conflict",
            message=str(exc),
        ) from exc


@router.post(
    "/api/v1/voice-runs/{voice_run_id}/submit",
    response_model=VoiceRunView,
    status_code=202,
)
async def submit_voice_run(
    voice_run_id: str,
    command: VoiceRunSubmitCommand,
    request: Request,
) -> VoiceRunView:
    try:
        return voice_manager_from(request).submit(voice_run_id, command)
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="voice_run_not_found",
            message=f"语音任务不存在：{voice_run_id}",
        ) from exc
    except VoiceRunCommandConflict as exc:
        raise ApiError(
            status_code=409,
            code="voice_submit_conflict",
            message=str(exc),
        ) from exc

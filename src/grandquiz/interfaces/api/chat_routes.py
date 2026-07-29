"""ReAct chat session 的 HTTP endpoint：创建 session、发消息、SSE 事件流。"""

import json
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from grandquiz.interfaces.api.chat import (
    ActiveResourceNotFoundError,
    ChatManager,
    ChatTurnInProgressError,
    ChatTurnNotFoundError,
    ChatUiEvent,
    MessageAccepted,
    MessageRequest,
    SessionView,
    TurnCancelled,
)
from grandquiz.interfaces.api.errors import ApiError

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def chat_manager_from(request: Request) -> ChatManager:
    return cast("ChatManager", request.app.state.chat_manager)


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(request: Request) -> SessionView:
    return chat_manager_from(request).create_session()


@router.post(
    "/sessions/{session_id}/messages",
    response_model=MessageAccepted,
    status_code=202,
)
async def send_message(
    session_id: str,
    body: MessageRequest,
    request: Request,
) -> MessageAccepted:
    manager = chat_manager_from(request)
    if manager.get_session(session_id) is None:
        raise ApiError(
            status_code=404,
            code="session_not_found",
            message=f"会话不存在：{session_id}",
        )
    try:
        return manager.send_message(
            session_id,
            body.text,
            active_resource_id=body.active_resource_id,
        )
    except ChatTurnInProgressError as exc:
        raise ApiError(
            status_code=409,
            code="turn_in_progress",
            message="当前会话仍有一轮消息正在处理",
            retryable=True,
        ) from exc
    except ActiveResourceNotFoundError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message=f"当前材料不存在：{body.active_resource_id}",
        ) from exc


@router.post(
    "/sessions/{session_id}/turns/{turn_id}/cancel",
    response_model=TurnCancelled,
)
async def cancel_turn(
    session_id: str,
    turn_id: str,
    request: Request,
) -> TurnCancelled:
    manager = chat_manager_from(request)
    if manager.get_session(session_id) is None:
        raise ApiError(
            status_code=404,
            code="session_not_found",
            message=f"会话不存在：{session_id}",
        )
    try:
        return await manager.cancel_turn(session_id, turn_id)
    except ChatTurnNotFoundError as exc:
        raise ApiError(
            status_code=404,
            code="turn_not_found",
            message=f"当前会话中没有可取消的轮次：{turn_id}",
        ) from exc


@router.get(
    "/sessions/{session_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "model": ChatUiEvent,
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/ChatUiEvent"},
                }
            },
        }
    },
)
async def stream_chat_events(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    manager = chat_manager_from(request)
    if manager.get_session(session_id) is None:
        raise ApiError(
            status_code=404,
            code="session_not_found",
            message=f"会话不存在：{session_id}",
        )

    async def stream() -> AsyncIterator[str]:
        async for event in manager.iter_events(session_id, after=after):
            payload = json.dumps(
                event.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

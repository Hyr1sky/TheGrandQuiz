"""Native Anthropic Messages transport behind the provider-neutral Model contract.

This adapter translates only current runtime primitives: leading system text,
text messages, client tool calls/results, usage, and native streaming. Anthropic
thinking, server tools, continuation, multimodal blocks, and provider-side
fallback are rejected instead of being silently discarded.
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from anthropic import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DeadlineExceededError,
    InternalServerError,
    NotFoundError,
    Omit,
    OverloadedError,
    PermissionDeniedError,
    RateLimitError,
    RequestTooLargeError,
    ServiceUnavailableError,
    UnprocessableEntityError,
    omit,
)
from anthropic.types import MessageParam, TextBlockParam, ToolUnionParam

from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    ProviderResponseProtocolError,
    ProviderStreamEvent,
    ProviderStreamProtocolError,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
    mark_malformed_arguments,
)
from grandquiz.providers.failure import (
    ProviderFailure,
    ProviderFailureCategory,
    RetryAfter,
    parse_retry_after,
    safe_provider_code,
)


@dataclass(frozen=True)
class AnthropicMessagesConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    max_output_tokens: int
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.api_key.strip() or not self.base_url.strip() or not self.model.strip():
            raise ValueError("Anthropic Messages 配置缺少必需字段")
        if isinstance(self.max_output_tokens, bool) or self.max_output_tokens < 1:
            raise ValueError("Anthropic Messages max_output_tokens 必须为正数")
        if (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("Anthropic Messages timeout_seconds 必须为正数")


@dataclass(frozen=True)
class _PreparedMessagesRequest:
    messages: list[MessageParam]
    system: list[TextBlockParam] | Omit
    tools: list[ToolUnionParam] | Omit


@dataclass
class _StreamBlock:
    kind: str
    text: list[str] = field(default_factory=lambda: list[str]())
    tool_id: str | None = None
    tool_name: str | None = None
    tool_json: list[str] = field(default_factory=lambda: list[str]())
    closed: bool = False


def _provider_code(exc: APIError) -> str | None:
    direct = safe_provider_code(getattr(exc, "code", None))
    if direct is not None:
        return direct
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return None
    error = cast("Mapping[str, object]", body).get("error")
    if not isinstance(error, Mapping):
        return None
    error_mapping = cast("Mapping[str, object]", error)
    return safe_provider_code(error_mapping.get("type")) or safe_provider_code(
        error_mapping.get("code")
    )


def _retry_after(exc: APIError) -> RetryAfter:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    value = headers.get("retry-after") if headers is not None else None
    return RetryAfter() if value is None else parse_retry_after(value)


def _normalize_anthropic_failure(
    exc: APIError,
    *,
    response_started: bool = False,
) -> ProviderFailure:
    provider_code = _provider_code(exc)
    retry_after = _retry_after(exc)
    status_code = getattr(exc, "status_code", None)
    status = status_code if isinstance(status_code, int) else None

    if (
        isinstance(exc, (APITimeoutError, DeadlineExceededError))
        or status == 504
        or provider_code == "timeout_error"
    ):
        category = ProviderFailureCategory.TIMEOUT
        retryable = True
    elif isinstance(exc, APIConnectionError):
        category = ProviderFailureCategory.CONNECTION
        retryable = True
    elif (
        isinstance(exc, AuthenticationError)
        or status == 401
        or provider_code == "authentication_error"
    ):
        category = ProviderFailureCategory.AUTHENTICATION
        retryable = False
    elif (
        isinstance(exc, PermissionDeniedError)
        or status == 403
        or provider_code == "permission_error"
    ):
        category = ProviderFailureCategory.PERMISSION_DENIED
        retryable = False
    elif isinstance(
        exc,
        (BadRequestError, UnprocessableEntityError, RequestTooLargeError),
    ) or provider_code in {"invalid_request_error", "request_too_large"}:
        category = ProviderFailureCategory.INVALID_REQUEST
        retryable = False
    elif isinstance(exc, NotFoundError) or status == 404 or provider_code == "not_found_error":
        category = ProviderFailureCategory.NOT_FOUND
        retryable = False
    elif isinstance(exc, ConflictError) or status == 409 or provider_code == "conflict_error":
        category = ProviderFailureCategory.CONFLICT
        retryable = True
    elif isinstance(exc, RateLimitError) or status == 429 or provider_code == "rate_limit_error":
        category = ProviderFailureCategory.RATE_LIMITED
        retryable = True
    elif status == 402 or provider_code == "billing_error":
        category = ProviderFailureCategory.QUOTA_EXHAUSTED
        retryable = False
    elif (
        isinstance(
            exc,
            (InternalServerError, OverloadedError, ServiceUnavailableError),
        )
        or provider_code in {"api_error", "overloaded_error"}
        or (status is not None and status >= 500)
    ):
        category = ProviderFailureCategory.SERVER_ERROR
        retryable = True
    else:
        category = ProviderFailureCategory.UNKNOWN
        retryable = False
    return ProviderFailure(
        category=category,
        status_code=status,
        provider_code=provider_code,
        retryable=retryable,
        retry_after_seconds=retry_after.seconds,
        retry_after_at=retry_after.utc_timestamp,
        retry_after_invalid=retry_after.invalid,
        response_started=response_started,
        replay_safe=not response_started,
    )


def _text_block(text: str) -> dict[str, object]:
    return {"type": "text", "text": text}


def _tool_use_block(tool_call: ToolCall) -> dict[str, object]:
    return {
        "type": "tool_use",
        "id": tool_call.id,
        "name": tool_call.name,
        "input": dict(tool_call.arguments),
    }


def _prepare_messages(
    messages: Sequence[Message],
) -> tuple[list[MessageParam], list[TextBlockParam]]:
    system: list[dict[str, object]] = []
    conversation: list[dict[str, object]] = []
    pending_tool_ids: tuple[str, ...] = ()
    conversation_started = False
    index = 0

    while index < len(messages):
        message = messages[index]
        if message.role == "system":
            if conversation_started or message.tool_calls or message.tool_call_id is not None:
                raise ProviderResponseProtocolError("invalid_message_sequence")
            system.append(_text_block(message.content))
            index += 1
            continue

        conversation_started = True
        if message.role == "assistant":
            if (
                pending_tool_ids
                or message.tool_call_id is not None
                or message.tool_error is not None
            ):
                raise ProviderResponseProtocolError("invalid_message_sequence")
            blocks: list[dict[str, object]] = []
            if message.content:
                blocks.append(_text_block(message.content))
            calls = tuple(message.tool_calls or ())
            call_ids = tuple(call.id for call in calls)
            if len(call_ids) != len(set(call_ids)):
                raise ProviderResponseProtocolError("invalid_message_sequence")
            blocks.extend(_tool_use_block(call) for call in calls)
            if not blocks:
                raise ProviderResponseProtocolError("invalid_message_sequence")
            conversation.append({"role": "assistant", "content": blocks})
            pending_tool_ids = call_ids
            index += 1
            continue

        if message.role == "user":
            if (
                pending_tool_ids
                or message.tool_calls
                or message.tool_call_id is not None
                or message.tool_error is not None
            ):
                raise ProviderResponseProtocolError("invalid_message_sequence")
            conversation.append({"role": "user", "content": [_text_block(message.content)]})
            index += 1
            continue

        if message.role != "tool" or not pending_tool_ids:
            raise ProviderResponseProtocolError("invalid_message_sequence")
        result_blocks: list[dict[str, object]] = []
        seen_results: set[str] = set()
        while index < len(messages) and messages[index].role == "tool":
            result = messages[index]
            tool_call_id = result.tool_call_id
            if (
                tool_call_id is None
                or tool_call_id not in pending_tool_ids
                or tool_call_id in seen_results
                or result.tool_calls
            ):
                raise ProviderResponseProtocolError("invalid_message_sequence")
            block: dict[str, object] = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": result.content,
            }
            if result.tool_error is True:
                block["is_error"] = True
            result_blocks.append(block)
            seen_results.add(tool_call_id)
            index += 1
        if seen_results != set(pending_tool_ids):
            raise ProviderResponseProtocolError("invalid_message_sequence")
        conversation.append({"role": "user", "content": result_blocks})
        pending_tool_ids = ()

    if pending_tool_ids or not conversation:
        raise ProviderResponseProtocolError("invalid_message_sequence")
    return (
        cast("list[MessageParam]", conversation),
        cast("list[TextBlockParam]", system),
    )


def _prepare_tools(tools: Sequence[ToolSpec]) -> list[ToolUnionParam]:
    return cast(
        "list[ToolUnionParam]",
        [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ],
    )


def _usage(raw: object) -> Usage:
    if raw is None:
        raise ProviderResponseProtocolError("invalid_response")
    if getattr(raw, "server_tool_use", None) is not None:
        raise ProviderResponseProtocolError("unsupported_content")

    def token(name: str, *, required: bool = False) -> int:
        value = getattr(raw, name, None)
        if value is None:
            if required:
                raise ProviderResponseProtocolError("invalid_response")
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProviderResponseProtocolError("invalid_response")
        return value

    return Usage(
        prompt_tokens=(
            token("input_tokens", required=True)
            + token("cache_creation_input_tokens")
            + token("cache_read_input_tokens")
        ),
        completion_tokens=token("output_tokens", required=True),
    )


def _decode_tool_input(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderResponseProtocolError("invalid_response")
    return dict(cast("Mapping[str, Any]", value))


def _decode_tool_json(raw: str) -> dict[str, Any]:
    try:
        decoded: object = json.loads(raw or "{}")
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        return cast("dict[str, Any]", decoded)
    return mark_malformed_arguments(raw)


def _unsupported_stop_reason(reason: object) -> ProviderResponseProtocolError | None:
    if reason == "max_tokens":
        return ProviderResponseProtocolError("output_truncated")
    if reason == "model_context_window_exceeded":
        return ProviderResponseProtocolError("context_window_exceeded")
    if reason == "refusal":
        return ProviderResponseProtocolError("content_refused")
    if reason == "pause_turn":
        return ProviderResponseProtocolError("continuation_unsupported")
    if reason not in {"end_turn", "stop_sequence", "tool_use"}:
        return ProviderResponseProtocolError("invalid_response")
    return None


def _completion_from_message(response: object) -> Completion:
    reason = getattr(response, "stop_reason", None)
    unsupported = _unsupported_stop_reason(reason)
    if unsupported is not None:
        raise unsupported
    text: list[str] = []
    tool_calls: list[ToolCall] = []
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        raise ProviderResponseProtocolError("invalid_response")
    for block in cast("list[object]", content):
        block_type = getattr(block, "type", None)
        if block_type == "text":
            citations = getattr(block, "citations", None)
            if citations not in (None, []):
                raise ProviderResponseProtocolError("unsupported_content")
            value = getattr(block, "text", None)
            if not isinstance(value, str):
                raise ProviderResponseProtocolError("invalid_response")
            text.append(value)
        elif block_type == "tool_use":
            caller = getattr(block, "caller", None)
            if (caller is not None and getattr(caller, "type", None) != "direct") or getattr(
                block, "toolset_name", None
            ) is not None:
                raise ProviderResponseProtocolError("unsupported_content")
            tool_id = getattr(block, "id", None)
            name = getattr(block, "name", None)
            if not isinstance(tool_id, str) or not isinstance(name, str):
                raise ProviderResponseProtocolError("invalid_response")
            tool_calls.append(
                ToolCall(
                    id=tool_id,
                    name=name,
                    arguments=_decode_tool_input(getattr(block, "input", None)),
                )
            )
        else:
            raise ProviderResponseProtocolError("unsupported_content")
    if len({call.id for call in tool_calls}) != len(tool_calls):
        raise ProviderResponseProtocolError("invalid_response")
    if (reason == "tool_use") != bool(tool_calls):
        raise ProviderResponseProtocolError("invalid_response")
    return Completion(
        text="".join(text),
        tool_calls=tool_calls or None,
        usage=_usage(getattr(response, "usage", None)),
    )


class AnthropicMessagesModel:
    """One resolved native Messages deployment; no purpose or routing policy."""

    def __init__(self, config: AnthropicMessagesConfig) -> None:
        self._config = config
        self._client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )

    def _prepare_request(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None,
    ) -> _PreparedMessagesRequest:
        prepared_messages, system = _prepare_messages(messages)
        return _PreparedMessagesRequest(
            messages=prepared_messages,
            system=system or omit,
            tools=_prepare_tools(tools) if tools else omit,
        )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        request = self._prepare_request(messages, tools=tools)
        try:
            response = await self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_output_tokens,
                messages=request.messages,
                system=request.system,
                tools=request.tools,
            )
        except APIError as exc:
            raise _normalize_anthropic_failure(exc) from exc
        return _completion_from_message(response)

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        request = self._prepare_request(messages, tools=tools)
        try:
            raw_stream = await self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_output_tokens,
                messages=request.messages,
                system=request.system,
                tools=request.tools,
                stream=True,
            )
        except APIError as exc:
            raise _normalize_anthropic_failure(exc) from exc

        stream = cast("Any", raw_stream)
        blocks: dict[int, _StreamBlock] = {}
        input_usage = Usage()
        output_tokens = 0
        final_output_usage_seen = False
        stop_reason: object = None
        message_started = False
        message_stopped = False
        response_started = False
        primary_error: BaseException | None = None
        try:
            try:
                async for event in stream:
                    response_started = True
                    event_type = getattr(event, "type", None)
                    if message_stopped:
                        raise ProviderStreamProtocolError("Anthropic message_stop 之后仍收到事件")
                    if event_type == "message_start":
                        if message_started or blocks:
                            raise ProviderStreamProtocolError("Anthropic stream 重复 message_start")
                        message_started = True
                        message = event.message
                        if (
                            getattr(message, "content", None) != []
                            or getattr(message, "stop_reason", None) is not None
                            or getattr(message, "stop_sequence", None) is not None
                        ):
                            raise ProviderStreamProtocolError(
                                "Anthropic message_start 初始状态非法"
                            )
                        input_usage = _usage(getattr(message, "usage", None))
                        output_tokens = input_usage.completion_tokens
                        continue
                    if not message_started:
                        raise ProviderStreamProtocolError("Anthropic stream 缺少 message_start")
                    if event_type == "content_block_start":
                        index = getattr(event, "index", None)
                        if not isinstance(index, int) or index < 0 or index in blocks:
                            raise ProviderStreamProtocolError("Anthropic content block index 非法")
                        content_block = getattr(event, "content_block", None)
                        block_type = getattr(content_block, "type", None)
                        if block_type == "text":
                            citations = getattr(content_block, "citations", None)
                            if citations not in (None, []):
                                raise ProviderResponseProtocolError("unsupported_content")
                            initial = getattr(content_block, "text", None)
                            if not isinstance(initial, str):
                                raise ProviderStreamProtocolError("Anthropic text block 非法")
                            block = _StreamBlock(kind="text", text=[initial] if initial else [])
                            blocks[index] = block
                            if initial:
                                yield TextDelta(text=initial)
                            continue
                        if block_type == "tool_use":
                            caller = getattr(content_block, "caller", None)
                            if (
                                caller is not None and getattr(caller, "type", None) != "direct"
                            ) or getattr(content_block, "toolset_name", None) is not None:
                                raise ProviderResponseProtocolError("unsupported_content")
                            tool_id = getattr(content_block, "id", None)
                            name = getattr(content_block, "name", None)
                            initial_input = getattr(content_block, "input", None)
                            if (
                                not isinstance(tool_id, str)
                                or not isinstance(name, str)
                                or initial_input != {}
                            ):
                                raise ProviderStreamProtocolError("Anthropic tool block 非法")
                            blocks[index] = _StreamBlock(
                                kind="tool_use",
                                tool_id=tool_id,
                                tool_name=name,
                            )
                            continue
                        raise ProviderResponseProtocolError("unsupported_content")
                    if event_type == "content_block_delta":
                        index = getattr(event, "index", None)
                        block = blocks.get(index) if isinstance(index, int) else None
                        if block is None or block.closed:
                            raise ProviderStreamProtocolError(
                                "Anthropic content delta 生命周期非法"
                            )
                        delta = getattr(event, "delta", None)
                        delta_type = getattr(delta, "type", None)
                        if block.kind == "text" and delta_type == "text_delta":
                            text = getattr(delta, "text", None)
                            if not isinstance(text, str):
                                raise ProviderStreamProtocolError("Anthropic text delta 非法")
                            block.text.append(text)
                            if text:
                                yield TextDelta(text=text)
                            continue
                        if block.kind == "tool_use" and delta_type == "input_json_delta":
                            partial_json = getattr(delta, "partial_json", None)
                            if not isinstance(partial_json, str):
                                raise ProviderStreamProtocolError("Anthropic tool delta 非法")
                            block.tool_json.append(partial_json)
                            continue
                        raise ProviderResponseProtocolError("unsupported_content")
                    if event_type == "content_block_stop":
                        index = getattr(event, "index", None)
                        block = blocks.get(index) if isinstance(index, int) else None
                        if block is None or block.closed:
                            raise ProviderStreamProtocolError("Anthropic content stop 生命周期非法")
                        block.closed = True
                        continue
                    if event_type == "message_delta":
                        if any(not block.closed for block in blocks.values()):
                            raise ProviderStreamProtocolError(
                                "Anthropic message delta 早于 block stop"
                            )
                        candidate_reason = getattr(
                            getattr(event, "delta", None),
                            "stop_reason",
                            None,
                        )
                        if candidate_reason is not None:
                            if stop_reason is not None and candidate_reason != stop_reason:
                                raise ProviderStreamProtocolError("Anthropic stop reason 发生变化")
                            stop_reason = candidate_reason
                        output = getattr(getattr(event, "usage", None), "output_tokens", None)
                        if output is not None:
                            if (
                                isinstance(output, bool)
                                or not isinstance(output, int)
                                or output < 0
                                or output < output_tokens
                            ):
                                raise ProviderStreamProtocolError("Anthropic output usage 非法")
                            output_tokens = output
                            final_output_usage_seen = True
                        continue
                    if event_type == "message_stop":
                        if (
                            any(not block.closed for block in blocks.values())
                            or stop_reason is None
                        ):
                            raise ProviderStreamProtocolError("Anthropic message_stop 终态非法")
                        message_stopped = True
                        continue
                    raise ProviderStreamProtocolError("Anthropic stream 事件类型未支持")
            except APIError as exc:
                primary_error = exc
                raise _normalize_anthropic_failure(
                    exc,
                    response_started=response_started,
                ) from exc
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    await cast("Callable[[], Awaitable[object]]", close)()
                except Exception:
                    if primary_error is None:
                        raise

        if not message_stopped:
            raise ProviderStreamProtocolError("Anthropic stream 缺少 message_stop")
        unsupported = _unsupported_stop_reason(stop_reason)
        if unsupported is not None:
            raise unsupported
        if not final_output_usage_seen:
            raise ProviderStreamProtocolError("Anthropic stream 缺少终态 output usage")

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for index in sorted(blocks):
            block = blocks[index]
            if block.kind == "text":
                text_parts.extend(block.text)
            elif block.kind == "tool_use":
                assert block.tool_id is not None and block.tool_name is not None
                tool_calls.append(
                    ToolCall(
                        id=block.tool_id,
                        name=block.tool_name,
                        arguments=_decode_tool_json("".join(block.tool_json)),
                    )
                )
        if len({call.id for call in tool_calls}) != len(tool_calls):
            raise ProviderResponseProtocolError("invalid_response")
        if (stop_reason == "tool_use") != bool(tool_calls):
            raise ProviderResponseProtocolError("invalid_response")
        yield CompletionFinished(
            completion=Completion(
                text="".join(text_parts),
                tool_calls=tool_calls or None,
                usage=Usage(
                    prompt_tokens=input_usage.prompt_tokens,
                    completion_tokens=output_tokens,
                ),
            )
        )

    async def aclose(self) -> None:
        await self._client.close()

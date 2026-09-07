"""Native Anthropic Messages adapter conformance, entirely on local transports."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import cast

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

import grandquiz.providers.anthropic as anthropic_module
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import Tool, ToolRegistry
from grandquiz.providers.anthropic import AnthropicMessagesConfig, AnthropicMessagesModel
from grandquiz.providers.base import (
    CompletionFinished,
    Message,
    ProviderResponseProtocolError,
    ProviderStreamProtocolError,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from grandquiz.providers.failure import ProviderFailure, ProviderFailureCategory
from grandquiz.providers.model_replay import ModelCassette, RecordingModel, ReplayModel
from grandquiz.providers.models import ModelRuntime, with_identity
from grandquiz.providers.profiles import (
    ModelConfigurationError,
    ModelIdentity,
    parse_model_config,
)


def _message_response(
    *,
    content: list[dict[str, object]] | None = None,
    stop_reason: str = "end_turn",
) -> dict[str, object]:
    return {
        "id": "msg_fixture",
        "type": "message",
        "role": "assistant",
        "model": "claude-fixture",
        "content": [{"type": "text", "text": "answer"}] if content is None else content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 7,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 3,
            "output_tokens": 5,
        },
    }


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> list[httpx2.Request]:
    real_client = anthropic.AsyncAnthropic
    requests: list[httpx2.Request] = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    def create(
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
    ) -> anthropic.AsyncAnthropic:
        assert max_retries == 0
        return real_client(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(capture)),
        )

    monkeypatch.setattr(anthropic_module, "AsyncAnthropic", create)
    return requests


def _model() -> AnthropicMessagesModel:
    return AnthropicMessagesModel(
        AnthropicMessagesConfig(
            api_key="test-credential",
            base_url="https://api.anthropic.test",
            model="claude-fixture",
            max_output_tokens=2048,
        )
    )


def test_direct_config_rejects_non_executable_numeric_limits() -> None:
    with pytest.raises(ValueError):
        AnthropicMessagesConfig(
            api_key="test-credential",
            base_url="https://api.anthropic.test",
            model="claude-fixture",
            max_output_tokens=True,
        )
    with pytest.raises(ValueError):
        AnthropicMessagesConfig(
            api_key="test-credential",
            base_url="https://api.anthropic.test",
            model="claude-fixture",
            max_output_tokens=2048,
            timeout_seconds=float("nan"),
        )
    with pytest.raises(ValueError):
        AnthropicMessagesConfig(
            api_key="test-credential",
            base_url="https://api.anthropic.test",
            model="claude-fixture",
            max_output_tokens=2048,
            timeout_seconds=float("inf"),
        )


async def test_complete_preserves_system_tools_parallel_results_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(200, request=request, json=_message_response()),
    )
    model = _model()
    messages = [
        Message(role="system", content="system one"),
        Message(role="system", content="system two"),
        Message(role="user", content="question"),
        Message(
            role="assistant",
            content="checking",
            tool_calls=[
                ToolCall(id="toolu_1", name="lookup", arguments={"topic": "one"}),
                ToolCall(id="toolu_2", name="lookup", arguments={"topic": "two"}),
            ],
        ),
        Message(role="tool", tool_call_id="toolu_1", content="first"),
        Message(role="tool", tool_call_id="toolu_2", content="failed", tool_error=True),
    ]
    tools = [
        ToolSpec(
            name="lookup",
            description="look up a topic",
            parameters={
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        )
    ]

    completion = await model.complete(messages, tools=tools)
    await model.aclose()

    assert completion.text == "answer"
    assert completion.usage.prompt_tokens == 12
    assert completion.usage.completion_tokens == 5
    sent = json.loads(requests[0].content)
    assert sent["model"] == "claude-fixture"
    assert sent["max_tokens"] == 2048
    assert sent["system"] == [
        {"type": "text", "text": "system one"},
        {"type": "text", "text": "system two"},
    ]
    assert sent["tools"] == [
        {
            "name": "lookup",
            "description": "look up a topic",
            "input_schema": tools[0].parameters,
        }
    ]
    assert sent["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "question"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup",
                    "input": {"topic": "one"},
                },
                {
                    "type": "tool_use",
                    "id": "toolu_2",
                    "name": "lookup",
                    "input": {"topic": "two"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "first"},
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_2",
                    "content": "failed",
                    "is_error": True,
                },
            ],
        },
    ]


async def test_complete_maps_parallel_tool_use_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _message_response(
        stop_reason="tool_use",
        content=[
            {"type": "text", "text": "I'll check."},
            {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": 1}},
            {"type": "tool_use", "id": "toolu_2", "name": "lookup", "input": {"q": 2}},
        ],
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(200, request=request, json=response),
    )
    model = _model()

    completion = await model.complete([Message(role="user", content="question")])
    await model.aclose()

    assert completion.text == "I'll check."
    assert completion.tool_calls == [
        ToolCall(id="toolu_1", name="lookup", arguments={"q": 1}),
        ToolCall(id="toolu_2", name="lookup", arguments={"q": 2}),
    ]


async def test_complete_rejects_missing_required_usage_instead_of_recording_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _message_response()
    usage = cast("dict[str, object]", response["usage"])
    del usage["output_tokens"]
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(200, request=request, json=response),
    )
    model = _model()

    with pytest.raises(ProviderResponseProtocolError) as caught:
        await model.complete([Message(role="user", content="question")])
    await model.aclose()

    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize(
    ("stop_reason", "expected_code"),
    [
        ("max_tokens", "output_truncated"),
        ("model_context_window_exceeded", "context_window_exceeded"),
        ("refusal", "content_refused"),
        ("pause_turn", "continuation_unsupported"),
    ],
)
async def test_complete_fails_closed_for_unsupported_terminal_semantics(
    monkeypatch: pytest.MonkeyPatch,
    stop_reason: str,
    expected_code: str,
) -> None:
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            json=_message_response(stop_reason=stop_reason),
        ),
    )
    model = _model()

    with pytest.raises(ProviderResponseProtocolError) as caught:
        await model.complete([Message(role="user", content="question")])
    await model.aclose()

    assert caught.value.code == expected_code


async def test_complete_rejects_thinking_instead_of_dropping_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _message_response(
        content=[{"type": "thinking", "thinking": "private", "signature": "sig"}],
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(200, request=request, json=response),
    )
    model = _model()

    with pytest.raises(ProviderResponseProtocolError) as caught:
        await model.complete([Message(role="user", content="question")])
    await model.aclose()

    assert caught.value.code == "unsupported_content"


async def test_invalid_tool_result_sequence_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(200, request=request, json=_message_response()),
    )
    model = _model()

    with pytest.raises(ProviderResponseProtocolError) as caught:
        await model.complete([Message(role="tool", tool_call_id="orphan", content="must not send")])
    await model.aclose()

    assert caught.value.code == "invalid_message_sequence"
    assert requests == []


def _stream_body(*events: tuple[str, dict[str, object]]) -> str:
    return "".join(
        f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        for event, data in events
    )


async def test_stream_assembles_text_tool_json_usage_and_one_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _stream_body(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **_message_response(content=[]),
                    "stop_reason": None,
                    "usage": {
                        "input_tokens": 7,
                        "cache_creation_input_tokens": 2,
                        "cache_read_input_tokens": 3,
                        "output_tokens": 1,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "checking"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup",
                    "input": {},
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"topic":'},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '"provider"}'},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 8},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=body,
        ),
    )
    model = _model()

    events = [
        event async for event in model.stream_complete([Message(role="user", content="question")])
    ]
    await model.aclose()

    assert events[0] == TextDelta(text="checking")
    assert sum(isinstance(event, CompletionFinished) for event in events) == 1
    terminal = cast("CompletionFinished", events[-1])
    assert terminal.completion.text == "checking"
    assert terminal.completion.tool_calls == [
        ToolCall(id="toolu_1", name="lookup", arguments={"topic": "provider"})
    ]
    assert terminal.completion.usage.prompt_tokens == 12
    assert terminal.completion.usage.completion_tokens == 8


async def test_stream_error_after_http_200_is_typed_and_has_no_success_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _stream_body(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **_message_response(content=[]),
                    "stop_reason": None,
                },
            },
        ),
        (
            "error",
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "SECRET-UPSTREAM"},
            },
        ),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=body,
        ),
    )
    model = _model()
    seen: list[object] = []

    with pytest.raises(ProviderFailure) as caught:
        async for event in model.stream_complete([Message(role="user", content="question")]):
            seen.append(event)
    await model.aclose()

    assert caught.value.category == ProviderFailureCategory.SERVER_ERROR
    assert caught.value.provider_code == "overloaded_error"
    assert caught.value.response_started is True
    assert caught.value.replay_safe is False
    assert "SECRET-UPSTREAM" not in repr(caught.value)
    assert not any(isinstance(event, CompletionFinished) for event in seen)


@pytest.mark.parametrize(
    ("vendor_code", "category"),
    [
        ("rate_limit_error", ProviderFailureCategory.RATE_LIMITED),
        ("timeout_error", ProviderFailureCategory.TIMEOUT),
    ],
)
async def test_stream_error_type_drives_category_even_after_http_200(
    monkeypatch: pytest.MonkeyPatch,
    vendor_code: str,
    category: ProviderFailureCategory,
) -> None:
    body = _stream_body(
        (
            "error",
            {
                "type": "error",
                "error": {"type": vendor_code, "message": "SECRET-UPSTREAM"},
            },
        )
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=body,
        ),
    )
    model = _model()

    with pytest.raises(ProviderFailure) as caught:
        async for _event in model.stream_complete([Message(role="user", content="question")]):
            pass
    await model.aclose()

    assert caught.value.category == category
    assert caught.value.response_started is False
    assert caught.value.replay_safe is True


async def test_adapter_disables_sdk_retries_and_normalizes_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            529,
            request=request,
            headers={"retry-after": "2"},
            json={
                "type": "error",
                "error": {"type": "overloaded_error", "message": "SECRET-UPSTREAM"},
                "request_id": "req_secret",
            },
        ),
    )
    model = _model()

    with pytest.raises(ProviderFailure) as caught:
        await model.complete([Message(role="user", content="question")])
    await model.aclose()

    assert len(requests) == 1
    assert caught.value.category == ProviderFailureCategory.SERVER_ERROR
    assert caught.value.status_code == 529
    assert caught.value.provider_code == "overloaded_error"
    assert caught.value.retry_after_seconds == 2
    assert "SECRET-UPSTREAM" not in repr(caught.value)


@pytest.mark.parametrize(
    ("status", "vendor_code", "category", "retryable"),
    [
        (400, "invalid_request_error", ProviderFailureCategory.INVALID_REQUEST, False),
        (401, "authentication_error", ProviderFailureCategory.AUTHENTICATION, False),
        (402, "billing_error", ProviderFailureCategory.QUOTA_EXHAUSTED, False),
        (403, "permission_error", ProviderFailureCategory.PERMISSION_DENIED, False),
        (404, "not_found_error", ProviderFailureCategory.NOT_FOUND, False),
        (409, "conflict_error", ProviderFailureCategory.CONFLICT, True),
        (413, "request_too_large", ProviderFailureCategory.INVALID_REQUEST, False),
        (429, "rate_limit_error", ProviderFailureCategory.RATE_LIMITED, True),
        (500, "api_error", ProviderFailureCategory.SERVER_ERROR, True),
        (504, "timeout_error", ProviderFailureCategory.TIMEOUT, True),
        (529, "overloaded_error", ProviderFailureCategory.SERVER_ERROR, True),
    ],
)
async def test_http_error_matrix_is_normalized_without_vendor_text(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    vendor_code: str,
    category: ProviderFailureCategory,
    retryable: bool,
) -> None:
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            status,
            request=request,
            json={
                "type": "error",
                "error": {"type": vendor_code, "message": "SECRET-UPSTREAM"},
                "request_id": "req_secret",
            },
        ),
    )
    model = _model()

    with pytest.raises(ProviderFailure) as caught:
        await model.complete([Message(role="user", content="question")])
    await model.aclose()

    assert caught.value.category == category
    assert caught.value.retryable is retryable
    assert caught.value.provider_code == vendor_code
    assert "SECRET-UPSTREAM" not in repr(caught.value)


async def test_stream_truncation_yields_no_success_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _stream_body(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **_message_response(content=[]),
                    "stop_reason": None,
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "partial"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens", "stop_sequence": None},
                "usage": {"output_tokens": 8},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=body,
        ),
    )
    model = _model()
    seen: list[object] = []

    with pytest.raises(ProviderResponseProtocolError) as caught:
        async for event in model.stream_complete([Message(role="user", content="question")]):
            seen.append(event)
    await model.aclose()

    assert caught.value.code == "output_truncated"
    assert seen == [TextDelta(text="partial")]
    assert not any(isinstance(event, CompletionFinished) for event in seen)


async def test_stream_rejects_decreasing_cumulative_output_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _stream_body(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **_message_response(content=[]),
                    "stop_reason": None,
                    "usage": {"input_tokens": 4, "output_tokens": 1},
                },
            },
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 2},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=body,
        ),
    )
    model = _model()

    with pytest.raises(ProviderStreamProtocolError):
        async for _event in model.stream_complete([Message(role="user", content="question")]):
            pass
    await model.aclose()


async def test_stream_rejects_prefilled_message_start_content_instead_of_dropping_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _stream_body(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **_message_response(),
                    "stop_reason": None,
                },
            },
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=body,
        ),
    )
    model = _model()

    with pytest.raises(ProviderStreamProtocolError):
        async for _event in model.stream_complete([Message(role="user", content="question")]):
            pass
    await model.aclose()


class _BlockingByteStream(httpx2.AsyncByteStream):
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _stream_body(
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        **_message_response(content=[]),
                        "stop_reason": None,
                    },
                },
            )
        ).encode()
        self.waiting.set()
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


async def test_stream_cancellation_propagates_and_closes_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _BlockingByteStream()
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            stream=body,
        ),
    )
    model = _model()

    async def consume() -> None:
        async for _event in model.stream_complete([Message(role="user", content="question")]):
            pass

    task = asyncio.create_task(consume())
    await body.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await model.aclose()

    assert body.closed is True


async def test_anthropic_completion_records_and_replays_with_model_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(200, request=request, json=_message_response()),
    )
    transport = _model()
    identity = ModelIdentity(
        purpose="chat",
        selection_source="default",
        configuration_fingerprint="a" * 64,
        policy_fingerprint="b" * 64,
    )
    path = tmp_path / "anthropic-cassette.json"
    recorder = RecordingModel(
        with_identity(transport, identity),
        ModelCassette(),
        checkpoint_path=path,
    )
    messages = [Message(role="user", content="question")]

    recorded = await recorder.complete(messages)
    await transport.aclose()
    replayed = await ReplayModel(ModelCassette.load(path), identity).complete(messages)

    assert recorded == replayed


ANTHROPIC_PROFILE_CONFIG = """
schema_version = "model-config.v1"
default_profile = "claude"

[connections.anthropic]
base_url = "https://api.anthropic.test"
api_key_env = "ANTHROPIC_TEST_KEY"
wire_api = "anthropic_messages"

[profiles.claude]
connection = "anthropic"
model = "claude-fixture"
max_output_tokens = 2048

[profiles.claude.capabilities]
tools = "supported"
native_streaming = "supported"
structured_output = "unknown"
reasoning = "unsupported"
"""


def test_profile_selects_anthropic_wire_explicitly_and_requires_output_limit() -> None:
    config = parse_model_config(ANTHROPIC_PROFILE_CONFIG, purposes={"chat"})

    resolved = config.resolve("chat")
    assert resolved.connection.wire_api == "anthropic_messages"
    assert resolved.profile.max_output_tokens == 2048

    with pytest.raises(ModelConfigurationError):
        parse_model_config(
            ANTHROPIC_PROFILE_CONFIG.replace("max_output_tokens = 2048\n", ""),
            purposes={"chat"},
        )
    with pytest.raises(ModelConfigurationError):
        parse_model_config(
            ANTHROPIC_PROFILE_CONFIG.replace(
                'model = "claude-fixture"',
                'model = "claude-fixture"\nthinking_mode = "enabled"',
            ),
            purposes={"chat"},
        )
    with pytest.raises(ModelConfigurationError):
        parse_model_config(
            ANTHROPIC_PROFILE_CONFIG.replace(
                'reasoning = "unsupported"',
                'reasoning = "supported"',
            ),
            purposes={"chat"},
        )
    with pytest.raises(ModelConfigurationError):
        parse_model_config(
            ANTHROPIC_PROFILE_CONFIG.replace(
                'structured_output = "unknown"',
                'structured_output = "supported"',
            ),
            purposes={"chat"},
        )


class _LookupParams(BaseModel):
    topic: str


def _tool_stream(*, final_text: str | None = None) -> str:
    if final_text is not None:
        return _stream_body(
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        **_message_response(content=[]),
                        "stop_reason": None,
                        "usage": {"input_tokens": 4, "output_tokens": 1},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": final_text},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 2},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
    return _stream_body(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **_message_response(content=[]),
                    "stop_reason": None,
                    "usage": {"input_tokens": 4, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_lookup",
                    "name": "lookup",
                    "input": {},
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": '{"topic":"provider"}',
                },
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


async def test_native_messages_runs_the_existing_runner_tool_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [_tool_stream(), _tool_stream(final_text="done")]
    requests = _install_transport(
        monkeypatch,
        lambda request: httpx2.Response(
            200,
            request=request,
            headers={"content-type": "text/event-stream"},
            content=responses.pop(0),
        ),
    )
    config = parse_model_config(ANTHROPIC_PROFILE_CONFIG, purposes={"chat"})
    runtime = ModelRuntime.from_configuration(
        config,
        environment={"ANTHROPIC_TEST_KEY": "test-credential"},
    )
    tool_calls: list[str] = []

    async def lookup(params: _LookupParams) -> str:
        tool_calls.append(params.topic)
        return f"found:{params.topic}"

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="lookup",
            description="look up a topic",
            params=_LookupParams,
            handler=lookup,
        )
    )
    event_list: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(event_list.append)
    runner = Runner(
        provider=runtime.bindings.for_purpose("chat"),
        emitter=EventEmitter(sink, ManualClock(), trace_id="anthropic-runner"),
        tools=registry,
    )
    try:
        answer = await runner.run_agent_turn("question")
    finally:
        await runtime.aclose()

    assert answer == "done"
    assert tool_calls == ["provider"]
    assert len(requests) == 2
    second_request = json.loads(requests[1].content)
    assert second_request["messages"][-2]["content"][0] == {
        "type": "tool_use",
        "id": "toolu_lookup",
        "name": "lookup",
        "input": {"topic": "provider"},
    }
    assert second_request["messages"][-1]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_lookup",
            "content": "found:provider",
        }
    ]
    assert any(event.type == EventType.TOOL_CALL_ENDED for event in event_list)

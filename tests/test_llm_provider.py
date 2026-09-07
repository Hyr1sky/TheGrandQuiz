"""OpenAICompatProvider 测试——mock 掉 AsyncOpenAI，确定性、零网络、不烧 token。

真实连通性由 scripts/smoke_llm.py 手动验（那才碰活 API）；这里只钉住可确定化的行为：
env 缺变量即报错、messages / response 映射、BYOK provider pin 与按方言组装 thinking 扩展字段。
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    omit,
)
from pydantic import BaseModel

import grandquiz.providers.llm as llm_mod
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import Tool, ToolRegistry
from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    Role,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
    malformed_arguments_raw,
)
from grandquiz.providers.failure import (
    ProviderFailure,
    ProviderFailureCategory,
    provider_failure_payload,
)
from grandquiz.providers.legacy import LegacyPurposeProvider
from grandquiz.providers.llm import OpenAICompatProvider, RoleConfig, RoleOverrides
from grandquiz.providers.models import with_identity
from grandquiz.providers.profiles import ModelIdentity


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.message = _FakeMessage(content, tool_calls)


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(
        self,
        content: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        tool_calls: list[_FakeToolCall] | None = None,
    ) -> None:
        self.choices = [_FakeChoice(content, tool_calls)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


class _FakeCompletions:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._response


class _FailingCompletions:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def create(self, **_kwargs: object) -> _FakeResponse:
        raise self._error


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self.chat = _FakeChat(_FailingCompletions(error))  # type: ignore[arg-type]

    async def close(self) -> None:
        return None


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.chat = _FakeChat(_FakeCompletions(response))

    async def close(self) -> None:
        return None


class _FakeStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[object]:
        for chunk in self._chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def close(self) -> None:
        self.closed = True


class _FakeDelta:
    def __init__(
        self,
        content: str | None,
        tool_calls: list[object] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeStreamChoice:
    def __init__(
        self,
        content: str | None,
        tool_calls: list[object] | None = None,
    ) -> None:
        self.delta = _FakeDelta(content, tool_calls)


class _FakeDeltaFunction:
    def __init__(
        self,
        name: str | None,
        arguments: str | None,
    ) -> None:
        self.name = name
        self.arguments = arguments


class _FakeDeltaToolCall:
    def __init__(
        self,
        index: int,
        *,
        id: str | None,
        name: str | None,
        arguments: str | None,
    ) -> None:
        self.index = index
        self.id = id
        self.function = _FakeDeltaFunction(name, arguments)


class _FakeChunk:
    def __init__(
        self,
        content: str | None = None,
        *,
        tool_calls: list[object] | None = None,
        usage: _FakeUsage | None = None,
    ) -> None:
        self.choices = (
            []
            if content is None and tool_calls is None
            else [_FakeStreamChoice(content, tool_calls)]
        )
        self.usage = usage


class _FakeStreamingCompletions:
    def __init__(self, chunks: list[object]) -> None:
        self.stream = _FakeStream(chunks)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.stream


class _FakeStreamingClient:
    def __init__(self, chunks: list[object]) -> None:
        self.chat = _FakeChat(_FakeStreamingCompletions(chunks))  # type: ignore[arg-type]

    async def close(self) -> None:
        return None


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, response: _FakeResponse
) -> dict[str, _FakeClient]:
    """把 llm 模块里的 AsyncOpenAI 换成返回 _FakeClient 的工厂，捕获构造出的客户端。"""
    captured: dict[str, _FakeClient] = {}

    def _factory(**_kwargs: object) -> _FakeClient:
        client = _FakeClient(response)
        captured["client"] = client
        return client

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _factory)
    return captured


def _patch_failing_client(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def _factory(**_kwargs: object) -> _FailingClient:
        return _FailingClient(error)

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _factory)


def _patch_streaming_client(
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[object],
) -> dict[str, _FakeStreamingClient]:
    captured: dict[str, _FakeStreamingClient] = {}

    def _factory(**_kwargs: object) -> _FakeStreamingClient:
        client = _FakeStreamingClient(chunks)
        captured["client"] = client
        return client

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _factory)
    return captured


def _set_openrouter_role_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "LLM_API_KEY": "basic-key",
        "LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "LLM_MODEL": "deepseek/deepseek-v4-flash",
        "LLM_ONLY_PROVIDER": "deepseek",
        "ENRICH_LLM_API_KEY": "enrich-key",
        "ENRICH_LLM_BASE_URL": "https://openrouter.ai/api/v1",
        "ENRICH_LLM_MODEL": "qwen/qwen3.7-plus",
        "ENRICH_LLM_ONLY_PROVIDER": "alibaba",
    }.items():
        monkeypatch.setenv(name, value)


def test_from_env_raises_on_missing_required_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError):
        OpenAICompatProvider.from_env()


def test_openai_sdk_hidden_retries_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    def _factory(**kwargs: object) -> _FakeClient:
        captured.append(kwargs)
        return _FakeClient(_FakeResponse("ok", prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _factory)
    OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="https://api.example.test/v1",
                model="m",
            )
        }
    )

    assert captured[0]["max_retries"] == 0


def test_from_env_applies_non_secret_basic_role_experiment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_openrouter_role_env(monkeypatch)
    provider = OpenAICompatProvider.from_env(
        role_overrides={
            "basic": RoleOverrides(
                model="deepseek-v4-pro",
                thinking_mode="enabled",
                reasoning_effort="max",
                api_dialect="deepseek",
            )
        }
    )

    execution = provider.execution_config_for_role["basic"]
    assert execution.model == "deepseek-v4-pro"
    assert execution.provider == "deepseek"
    assert execution.thinking_mode == "enabled"
    assert execution.reasoning_effort == "max"


async def test_from_env_pins_basic_provider_without_shared_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[_FakeClient] = []

    def _factory(**_kwargs: object) -> _FakeClient:
        client = _FakeClient(_FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
        clients.append(client)
        return client

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _factory)
    _set_openrouter_role_env(monkeypatch)

    provider = OpenAICompatProvider.from_env()
    await provider.complete([Message(role="user", content="hi")], role="basic")

    called_client = next(client for client in clients if client.chat.completions.calls)
    call = called_client.chat.completions.calls[0]
    extra_body = cast("dict[str, object]", call["extra_body"])
    assert extra_body["provider"] == {
        "only": ["deepseek"],
        "allow_fallbacks": False,
    }


async def test_from_env_pins_enrich_provider_for_streaming_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[_FakeStreamingClient] = []

    def _factory(**_kwargs: object) -> _FakeStreamingClient:
        client = _FakeStreamingClient(
            [_FakeChunk(usage=_FakeUsage(prompt_tokens=1, completion_tokens=1))]
        )
        clients.append(client)
        return client

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _factory)
    _set_openrouter_role_env(monkeypatch)

    provider = OpenAICompatProvider.from_env()
    events = [
        event
        async for event in provider.stream_complete(
            [Message(role="user", content="hi")],
            role="enrich",
        )
    ]

    called_client = next(client for client in clients if client.chat.completions.calls)
    call = called_client.chat.completions.calls[0]
    assert events == [
        CompletionFinished(
            completion=Completion(
                text="",
                usage=Usage(prompt_tokens=1, completion_tokens=1),
            )
        )
    ]
    extra_body = cast("dict[str, object]", call["extra_body"])
    assert extra_body["provider"] == {
        "only": ["alibaba"],
        "allow_fallbacks": False,
    }


async def test_complete_maps_messages_and_response_and_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_client(
        monkeypatch, _FakeResponse("连通正常", prompt_tokens=11, completion_tokens=3)
    )
    provider = OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-v4-flash",
                api_dialect="deepseek",
                thinking_mode="disabled",
            )
        }
    )

    reply = await provider.complete([Message(role="user", content="hi")], role="basic")

    assert reply.text == "连通正常"
    assert reply.usage.prompt_tokens == 11
    assert reply.usage.completion_tokens == 3
    call = captured["client"].chat.completions.calls[0]
    assert call["model"] == "deepseek-v4-flash"
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}


async def test_complete_normalizes_provider_quota_failure_without_raw_response_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://api.example.test/chat/completions")
    response = httpx.Response(403, request=request)
    upstream = PermissionDeniedError(
        "Free quota exhausted; request_id=SECRET-REQUEST-ID",
        response=response,
        body={
            "error": {
                "message": "Free quota exhausted; request_id=SECRET-REQUEST-ID",
                "code": "AllocationQuota.FreeTierOnly",
            }
        },
    )
    _patch_failing_client(monkeypatch, upstream)
    provider = OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="https://api.example.test/v1",
                model="m",
            )
        }
    )

    with pytest.raises(ProviderFailure) as caught:
        await provider.complete([Message(role="user", content="hi")], role="basic")

    failure = caught.value
    assert failure.category is ProviderFailureCategory.QUOTA_EXHAUSTED
    assert failure.status_code == 403
    assert failure.provider_code == "AllocationQuota.FreeTierOnly"
    assert failure.retryable is False
    assert failure.public_reason_code == "provider_quota_exhausted"
    assert "SECRET-REQUEST-ID" not in str(failure)
    assert failure.__cause__ is upstream


async def test_dashscope_token_quota_throttling_remains_retryable_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    response = httpx.Response(429, request=request)
    upstream = RateLimitError(
        "Allocated quota exceeded; tenant=SECRET-TENANT",
        response=response,
        body={
            "error": {
                "message": "Allocated quota exceeded; tenant=SECRET-TENANT",
                "code": "Throttling.AllocationQuota",
            }
        },
    )
    _patch_failing_client(monkeypatch, upstream)
    provider = OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-plus",
                api_dialect="dashscope",
            )
        }
    )

    with pytest.raises(ProviderFailure) as caught:
        await provider.complete([Message(role="user", content="hi")], role="basic")

    failure = caught.value
    assert failure.category is ProviderFailureCategory.RATE_LIMITED
    assert failure.provider_code == "Throttling.AllocationQuota"
    assert failure.retryable is True
    assert "SECRET-TENANT" not in repr(failure)


@pytest.mark.parametrize(
    ("header", "seconds", "timestamp", "invalid"),
    [
        ("12", 12.0, None, False),
        ("Sun, 06 Nov 1994 08:49:37 GMT", None, 784111777.0, False),
        ("invalid SECRET-RETRY-HEADER", None, None, True),
    ],
)
async def test_complete_normalizes_retry_after_without_retaining_raw_header(
    monkeypatch: pytest.MonkeyPatch,
    header: str,
    seconds: float | None,
    timestamp: float | None,
    invalid: bool,
) -> None:
    request = httpx.Request("POST", "https://api.example.test/chat/completions")
    response = httpx.Response(429, request=request, headers={"Retry-After": header})
    upstream = RateLimitError(
        "SECRET-UPSTREAM-MESSAGE",
        response=response,
        body={"error": {"message": "SECRET-UPSTREAM-MESSAGE", "code": "rate_limit"}},
    )
    _patch_failing_client(monkeypatch, upstream)
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="https://api.example.test/v1", model="m")}
    )

    with pytest.raises(ProviderFailure) as caught:
        await provider.complete([Message(role="user", content="hi")], role="basic")

    failure = caught.value
    assert failure.retry_after_seconds == seconds
    assert failure.retry_after_at == timestamp
    assert failure.retry_after_invalid is invalid
    assert header not in repr(failure)
    if invalid:
        assert header not in repr(provider_failure_payload(failure))


async def test_stream_complete_normalizes_provider_failure_at_request_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://api.example.test/chat/completions")
    response = httpx.Response(403, request=request)
    upstream = PermissionDeniedError(
        "Access forbidden; tenant=SECRET-TENANT",
        response=response,
        body={"error": {"message": "Access forbidden", "code": "AccessDenied"}},
    )
    _patch_failing_client(monkeypatch, upstream)
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="https://api.example.test/v1", model="m")}
    )

    with pytest.raises(ProviderFailure) as caught:
        async for _event in provider.stream_complete(
            [Message(role="user", content="hi")], role="basic"
        ):
            pass

    failure = caught.value
    assert failure.category is ProviderFailureCategory.PERMISSION_DENIED
    assert failure.provider_code == "AccessDenied"
    assert failure.retryable is False
    assert "SECRET-TENANT" not in repr(failure)


async def test_stream_failure_after_first_upstream_chunk_is_not_replay_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://api.example.test/chat/completions")
    response = httpx.Response(503, request=request)
    upstream = InternalServerError(
        "SECRET-MIDSTREAM-ERROR",
        response=response,
        body={"error": {"message": "SECRET-MIDSTREAM-ERROR", "code": "overloaded"}},
    )
    captured = _patch_streaming_client(
        monkeypatch,
        [_FakeChunk("prefix"), upstream],
    )
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="https://api.example.test/v1", model="m")}
    )

    with pytest.raises(ProviderFailure) as caught:
        async for _event in provider.stream_complete(
            [Message(role="user", content="hi")], role="basic"
        ):
            pass

    failure = caught.value
    assert failure.response_started is True
    assert failure.replay_safe is False
    stream_completions = cast(
        _FakeStreamingCompletions,
        captured["client"].chat.completions,
    )
    assert stream_completions.stream.closed is True
    assert "SECRET-MIDSTREAM-ERROR" not in repr(failure)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_provider_failure_rejects_unsafe_retry_after_numbers(value: float) -> None:
    failure = ProviderFailure(
        category=ProviderFailureCategory.RATE_LIMITED,
        retryable=True,
        retry_after_seconds=value,
    )

    assert failure.retry_after_seconds is None
    assert failure.retry_after_invalid is True
    assert value.__repr__() not in repr(provider_failure_payload(failure))


@pytest.mark.parametrize(
    ("error_type", "status", "category", "retryable"),
    [
        (AuthenticationError, 401, ProviderFailureCategory.AUTHENTICATION, False),
        (BadRequestError, 400, ProviderFailureCategory.INVALID_REQUEST, False),
        (RateLimitError, 429, ProviderFailureCategory.RATE_LIMITED, True),
        (InternalServerError, 503, ProviderFailureCategory.SERVER_ERROR, True),
    ],
)
async def test_complete_normalizes_status_failures_and_retryability(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[APIStatusError],
    status: int,
    category: ProviderFailureCategory,
    retryable: bool,
) -> None:
    request = httpx.Request("POST", "https://api.example.test/chat/completions")
    response = httpx.Response(status, request=request)
    upstream = error_type(
        "SECRET-UPSTREAM-MESSAGE",
        response=response,
        body={"error": {"message": "SECRET-UPSTREAM-MESSAGE", "code": "safe_code"}},
    )
    _patch_failing_client(monkeypatch, upstream)
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="https://api.example.test/v1", model="m")}
    )

    with pytest.raises(ProviderFailure) as caught:
        await provider.complete([Message(role="user", content="hi")], role="basic")

    assert caught.value.category is category
    assert caught.value.status_code == status
    assert caught.value.retryable is retryable
    assert "SECRET-UPSTREAM-MESSAGE" not in str(caught.value)


@pytest.mark.parametrize(
    ("upstream", "category"),
    [
        (
            APITimeoutError(httpx.Request("POST", "https://api.example.test/chat/completions")),
            ProviderFailureCategory.TIMEOUT,
        ),
        (
            APIConnectionError(
                request=httpx.Request("POST", "https://api.example.test/chat/completions")
            ),
            ProviderFailureCategory.CONNECTION,
        ),
    ],
)
async def test_complete_normalizes_transport_failures_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
    upstream: Exception,
    category: ProviderFailureCategory,
) -> None:
    _patch_failing_client(monkeypatch, upstream)
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="https://api.example.test/v1", model="m")}
    )

    with pytest.raises(ProviderFailure) as caught:
        await provider.complete([Message(role="user", content="hi")], role="basic")

    assert caught.value.category is category
    assert caught.value.status_code is None
    assert caught.value.retryable is True


def test_provider_failure_drops_non_identifier_vendor_code_from_events() -> None:
    failure = ProviderFailure(
        category=ProviderFailureCategory.UNKNOWN,
        provider_code="https://provider.example/error?token=SECRET",
        retryable=False,
    )

    assert failure.provider_code is None
    assert "provider_code" not in provider_failure_payload(failure)
    assert "SECRET" not in repr(failure)


async def test_deepseek_thinking_mode_and_effort_use_the_official_request_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-v4-pro",
                api_dialect="deepseek",
                thinking_mode="enabled",
                reasoning_effort="high",
            )
        }
    )

    await provider.complete([Message(role="user", content="hi")], role="basic")

    call = captured["client"].chat.completions.calls[0]
    assert call["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    execution = provider.execution_config_for_role["basic"]
    assert execution.model == "deepseek-v4-pro"
    assert execution.thinking_mode == "enabled"
    assert execution.replay_identity.endswith("thinking=enabled|effort=high")


async def test_dashscope_keeps_its_distinct_thinking_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider(
        {
            "enrich": RoleConfig(
                api_key="k",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen3.7-plus",
                api_dialect="dashscope",
                thinking_mode="disabled",
            )
        }
    )

    await provider.complete([Message(role="user", content="hi")], role="enrich")

    call = captured["client"].chat.completions.calls[0]
    assert call["extra_body"] == {"enable_thinking": False}


async def test_stream_complete_yields_text_deltas_and_authoritative_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_streaming_client(
        monkeypatch,
        [
            _FakeChunk("正"),
            _FakeChunk("考级"),
            _FakeChunk(
                usage=_FakeUsage(
                    prompt_tokens=11,
                    completion_tokens=3,
                )
            ),
        ],
    )
    provider = OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="u",
                model="m-basic",
            )
        }
    )

    events = [
        event
        async for event in provider.stream_complete(
            [Message(role="user", content="hi")],
            role="basic",
        )
    ]

    assert events[:2] == [
        TextDelta(text="正"),
        TextDelta(text="考级"),
    ]
    assert events[2] == CompletionFinished(
        completion=Completion(
            text="正考级",
            usage=Usage(
                prompt_tokens=11,
                completion_tokens=3,
            ),
        )
    )
    call = captured["client"].chat.completions.calls[0]
    assert call["stream"] is True
    assert call["stream_options"] == {"include_usage": True}
    streaming = cast("_FakeStreamingCompletions", captured["client"].chat.completions)
    assert streaming.stream.closed is True


async def test_stream_complete_assembles_tool_argument_fragments_inside_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_streaming_client(
        monkeypatch,
        [
            _FakeChunk(
                tool_calls=[
                    _FakeDeltaToolCall(
                        0,
                        id="call_1",
                        name="echo",
                        arguments='{"text":',
                    )
                ]
            ),
            _FakeChunk(
                tool_calls=[
                    _FakeDeltaToolCall(
                        0,
                        id=None,
                        name=None,
                        arguments='"hi"}',
                    )
                ]
            ),
        ],
    )
    provider = OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="u",
                model="m-basic",
            )
        }
    )

    events = [
        event
        async for event in provider.stream_complete(
            [Message(role="user", content="hi")],
            role="basic",
        )
    ]

    assert events == [
        CompletionFinished(
            completion=Completion(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="echo",
                        arguments={"text": "hi"},
                    )
                ],
            )
        )
    ]


async def test_stream_complete_preserves_text_that_precedes_a_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_streaming_client(
        monkeypatch,
        [
            _FakeChunk("我先查一下。"),
            _FakeChunk(
                tool_calls=[
                    _FakeDeltaToolCall(
                        0,
                        id="call_1",
                        name="echo",
                        arguments='{"text":"hi"}',
                    )
                ]
            ),
        ],
    )
    provider = OpenAICompatProvider(
        {
            "basic": RoleConfig(
                api_key="k",
                base_url="u",
                model="m-basic",
            )
        }
    )

    events = [
        event
        async for event in provider.stream_complete(
            [Message(role="user", content="hi")],
            role="basic",
        )
    ]

    assert events == [
        TextDelta(text="我先查一下。"),
        CompletionFinished(
            completion=Completion(
                text="我先查一下。",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="echo",
                        arguments={"text": "hi"},
                    )
                ],
            )
        ),
    ]


async def test_complete_omits_extra_body_when_thinking_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})

    await provider.complete([Message(role="user", content="hi")], role="basic")

    call = captured["client"].chat.completions.calls[0]
    assert call["extra_body"] is None


async def test_complete_uses_greedy_temperature_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    # issue 01 决策 4：出题（enrich）必须贪心解码——温度采样会让同一 message 每次录出不同题、毁掉
    # record/replay 可复现（真机跨轮漂移的根因之一）。删掉 llm.py 的 temperature=0 → 本测试红。
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider({"enrich": RoleConfig(api_key="k", base_url="u", model="m")})

    await provider.complete([Message(role="user", content="hi")], role="enrich")

    call = captured["client"].chat.completions.calls[0]
    assert call["temperature"] == 0


# --------------------------------------------------------------------------- #
# R1-S5：function-calling 接线——发 tools / 解析 tool_calls / assistant+tool 消息映射
# --------------------------------------------------------------------------- #


async def test_complete_sends_tools_as_openai_function_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tools 非空 → 映射成 OpenAI tools=[{"type":"function","function":{...}}]。删掉 llm.py 发 tools
    # 的分支 → 本测试红（真机 bug 的复现门：provider 从不发 tools）。
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    await provider.complete(
        [Message(role="user", content="hi")],
        role="basic",
        tools=[ToolSpec(name="echo", description="回声 text", parameters=schema)],
    )

    call = captured["client"].chat.completions.calls[0]
    assert call["tools"] == [
        {
            "type": "function",
            "function": {"name": "echo", "description": "回声 text", "parameters": schema},
        }
    ]


async def test_complete_omits_tools_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # 向后兼容：不传 tools → tools 走 omit 哨兵（等价"线上不带该参数"），既有纯文本路径不变。
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})

    await provider.complete([Message(role="user", content="hi")], role="basic")

    call = captured["client"].chat.completions.calls[0]
    assert call["tools"] is omit


async def test_complete_parses_tool_calls_from_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # response 带 tool_calls（arguments 是 JSON 串）→ Completion.tool_calls（arguments 转回 dict）。
    _patch_client(
        monkeypatch,
        _FakeResponse(
            None,
            prompt_tokens=5,
            completion_tokens=2,
            tool_calls=[_FakeToolCall("call_1", "echo", '{"text": "hi"}')],
        ),
    )
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})

    reply = await provider.complete([Message(role="user", content="hi")], role="basic")

    assert reply.text == ""  # tool_calls 分支下 content 常为 None → 归一到空串
    assert reply.tool_calls is not None
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].id == "call_1"
    assert reply.tool_calls[0].name == "echo"
    assert reply.tool_calls[0].arguments == {"text": "hi"}  # JSON 字符串 → dict（边界解码）
    assert reply.usage.prompt_tokens == 5


async def test_complete_tolerates_malformed_tool_call_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # dogfood（trace 762884ba）："神了" → 模型吐 tool_call 但 arguments 是畸形 JSON（缺右括号）。
    # 此前 _parse_tool_calls 直接 json.loads → JSONDecodeError 裸抛、炸整场 react 会话。现容错：
    # 不抛裸异常，把畸形参数表示成"参数非法"的可恢复态（保留 sentinel key），交 kernel dispatch 走
    # ModelRetry(DEGRADED) 恢复路径。删掉 llm.py 的 try/except → 本测试红（JSONDecodeError 冒出）。
    _patch_client(
        monkeypatch,
        _FakeResponse(
            None,
            prompt_tokens=5,
            completion_tokens=2,
            tool_calls=[_FakeToolCall("call_1", "echo", '{"text": "hi"')],  # 缺右括号 → 畸形
        ),
    )
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})

    reply = await provider.complete([Message(role="user", content="hi")], role="basic")

    assert reply.tool_calls is not None
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "echo"
    # 畸形参数被标记为"参数非法"，不当合法入参（原始畸形串留痕，供回灌诊断）。
    assert malformed_arguments_raw(reply.tool_calls[0].arguments) == '{"text": "hi"'


async def test_complete_marks_non_object_json_arguments_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # JSON 合法但不是对象（如裸数组 / 裸标量）——同样非法入参：dispatch 无从按 pydantic 对象 schema
    # 校验，故也标记为"参数非法"走同一 DEGRADED 恢复路径（不让非 dict 值漏进 dispatch 炸 dict()）。
    _patch_client(
        monkeypatch,
        _FakeResponse(
            None,
            prompt_tokens=5,
            completion_tokens=2,
            tool_calls=[_FakeToolCall("call_1", "echo", "[1, 2, 3]")],  # 合法 JSON、但非对象
        ),
    )
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})

    reply = await provider.complete([Message(role="user", content="hi")], role="basic")

    assert reply.tool_calls is not None
    assert malformed_arguments_raw(reply.tool_calls[0].arguments) == "[1, 2, 3]"


async def test_complete_returns_text_when_no_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    # 无 tool_calls → 走旧路径取 .content，tool_calls 为 None（纯文本 completion 不变）。
    _patch_client(monkeypatch, _FakeResponse("纯文本", prompt_tokens=2, completion_tokens=2))
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})

    reply = await provider.complete([Message(role="user", content="hi")], role="basic")

    assert reply.text == "纯文本"
    assert reply.tool_calls is None


async def test_complete_maps_assistant_tool_calls_and_tool_result_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 出栈消息映射：assistant 带 tool_calls（内部 dict → JSON 串）；role="tool" 结果消息
    # → {"role":"tool","tool_call_id","content"}。content 为空的 assistant 归一到 None。
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider({"basic": RoleConfig(api_key="k", base_url="u", model="m")})

    messages = [
        Message(role="system", content="sys"),
        Message(role="user", content="q"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
        ),
        Message(role="tool", content="echoed:hi", tool_call_id="call_1"),
    ]
    await provider.complete(messages, role="basic")

    sent = cast("list[dict[str, Any]]", captured["client"].chat.completions.calls[0]["messages"])
    assert sent[0] == {"role": "system", "content": "sys"}
    assert sent[1] == {"role": "user", "content": "q"}
    assert sent[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "echo", "arguments": json.dumps({"text": "hi"})},
            }
        ],
    }
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": "echoed:hi"}


# --------------------------------------------------------------------------- #
# R1-S5：ToolRegistry.tool_specs() —— pydantic 入参 schema → 通用 ToolSpec
# --------------------------------------------------------------------------- #


class _EchoParams(BaseModel):
    text: str


def _echo_tool() -> Tool:
    async def handler(params: _EchoParams) -> str:
        return f"echoed:{params.text}"

    return Tool(name="echo", description="回声 text", params=_EchoParams, handler=handler)


def test_tool_specs_generates_from_pydantic_schema() -> None:
    registry = ToolRegistry()
    registry.register(_echo_tool())

    specs = registry.tool_specs()

    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, ToolSpec)
    assert spec.name == "echo"
    assert spec.description == "回声 text"
    # parameters 直接来自 pydantic model_json_schema()——含 properties.text 与 required。
    assert spec.parameters == _EchoParams.model_json_schema()
    assert spec.parameters["properties"]["text"]["type"] == "string"


def test_tool_specs_empty_registry_is_empty_list() -> None:
    assert ToolRegistry().tool_specs() == []


# --------------------------------------------------------------------------- #
# R1-S5：run_agent_turn 把 tool_specs 传给 provider + MODEL_STARTED 记 role（修 trace 空 role）
# --------------------------------------------------------------------------- #


class _CapturingProvider(LegacyPurposeProvider):
    """记下最后一次 complete 收到的 tools / role；给回 final 文本（无 tool_calls → 终止）。"""

    def __init__(self) -> None:
        self.tools_seen: Sequence[ToolSpec] | None = None
        self.role_seen: Role | None = None

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: object = None,
    ) -> Completion:
        self.tools_seen = tools  # type: ignore[assignment]
        self.role_seen = role
        return Completion(text="done")


class _TypedFailureProvider(LegacyPurposeProvider):
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        raise ProviderFailure(
            category=ProviderFailureCategory.RATE_LIMITED,
            status_code=429,
            provider_code="rate_limit_exceeded",
            retryable=True,
        )


def _events_emitter() -> tuple[EventEmitter, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    return EventEmitter(sink, ManualClock(), trace_id="t"), events


async def test_run_agent_turn_forwards_tool_specs_to_provider() -> None:
    provider = _CapturingProvider()
    emitter, _ = _events_emitter()
    registry = ToolRegistry()
    registry.register(_echo_tool())
    runner = Runner(provider=provider, emitter=emitter, tools=registry)

    await runner.run_agent_turn("q")

    assert provider.tools_seen is not None
    names = [s.name for s in provider.tools_seen]
    assert names == ["echo"]


async def test_run_agent_turn_records_bound_identity_in_model_started_payload() -> None:
    provider = _CapturingProvider()
    identity = ModelIdentity(
        purpose="chat",
        selection_source="legacy",
        configuration_fingerprint="1" * 64,
        policy_fingerprint="2" * 64,
    )
    emitter, events = _events_emitter()
    runner = Runner(
        provider=with_identity(provider.for_purpose("chat"), identity),
        emitter=emitter,
    )

    await runner.run_agent_turn("q")

    started = [e for e in events if e.type == EventType.MODEL_STARTED]
    assert len(started) == 1
    assert started[0].payload["model_identity"] == identity.model_dump()
    assert "role" not in started[0].payload
    # Legacy adapter 内部仍把 chat 明确翻译到原 basic 槽；Runner 不再知道该角色。
    assert provider.role_seen == "basic"


async def test_runner_projects_typed_provider_failure_into_model_events() -> None:
    emitter, events = _events_emitter()
    runner = Runner(provider=_TypedFailureProvider(), emitter=emitter)

    with pytest.raises(ProviderFailure):
        await runner.run_agent_turn("q")

    ended = next(event for event in events if event.type == EventType.MODEL_ENDED)
    assert ended.payload["provider_failure_category"] == "rate_limited"
    assert ended.payload["provider_failure_code"] == "provider_rate_limited"
    assert ended.payload["provider_status_code"] == 429
    assert ended.payload["provider_code"] == "rate_limit_exceeded"
    assert ended.payload["provider_retryable"] is True

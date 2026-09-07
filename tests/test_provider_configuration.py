"""Provider configuration through real SDK serialization and offline HTTP."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

import grandquiz.providers.llm as llm_mod
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.events import AgentEvent
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import CompletionFinished, Message, Role, TextDelta
from grandquiz.providers.llm import OpenAICompatProvider, RoleConfig, RoleOverrides
from grandquiz.providers.replay import Cassette, RecordingProvider, ReplayMiss, ReplayProvider


@pytest.fixture
def default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("LLM_", "ENRICH_LLM_")):
            monkeypatch.delenv(name)
    for name, value in {
        "LLM_API_KEY": "test-default-credential",
        "LLM_BASE_URL": "https://default.example.test/v1",
        "LLM_MODEL": "default-model",
        "LLM_API_DIALECT": "deepseek",
        "LLM_THINKING_MODE": "enabled",
        "LLM_REASONING_EFFORT": "high",
        "LLM_ONLY_PROVIDER": "vendor-a",
        "LLM_TIMEOUT_SECONDS": "12.5",
    }.items():
        monkeypatch.setenv(name, value)


@dataclass
class _HTTPRecorder:
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])
    clients: list[httpx.AsyncClient] = field(default_factory=list[httpx.AsyncClient])

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if json.loads(request.content).get("stream"):
            chunk = {
                "id": "completion-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "default-model",
                "choices": [{"index": 0, "delta": {"content": "reply"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "object": "chat.completion",
                "created": 0,
                "model": "default-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "reply"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    def client(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
    ) -> AsyncOpenAI:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.clients.append(client)
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=client,
        )


@pytest.fixture
def http_recorder(monkeypatch: pytest.MonkeyPatch) -> _HTTPRecorder:
    recorder = _HTTPRecorder()
    monkeypatch.setattr(llm_mod, "AsyncOpenAI", recorder.client)
    return recorder


@pytest.mark.usefixtures("default_env")
async def test_single_default_config_serves_both_slots_with_all_request_options(
    http_recorder: _HTTPRecorder,
) -> None:
    provider = OpenAICompatProvider.from_env()
    try:
        basic = await provider.complete([Message(role="user", content="hi")], role="basic")
        enrich = await provider.complete([Message(role="user", content="hi")], role="enrich")
        assert basic.text == enrich.text == "reply"
        assert basic.usage.total_tokens == enrich.usage.total_tokens == 5
        assert len(http_recorder.requests) == 2
        for request in http_recorder.requests:
            assert str(request.url) == "https://default.example.test/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-default-credential"
            assert json.loads(request.content) == {
                "messages": [{"role": "user", "content": "hi"}],
                "model": "default-model",
                "temperature": 0,
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
                "provider": {"only": ["vendor-a"], "allow_fallbacks": False},
            }
            assert request.extensions["timeout"] == {
                "connect": 12.5,
                "read": 12.5,
                "write": 12.5,
                "pool": 12.5,
            }
    finally:
        await provider.aclose()
    assert http_recorder.clients and all(client.is_closed for client in http_recorder.clients)


@pytest.mark.usefixtures("default_env")
async def test_three_required_default_fields_are_sufficient(
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
) -> None:
    for suffix in (
        "API_DIALECT",
        "THINKING_MODE",
        "REASONING_EFFORT",
        "ONLY_PROVIDER",
        "TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(f"LLM_{suffix}")
    provider = OpenAICompatProvider.from_env()
    try:
        for role in ("basic", "enrich"):
            await provider.complete([Message(role="user", content="hi")], role=role)
        assert len(http_recorder.requests) == 2
        for request in http_recorder.requests:
            assert json.loads(request.content) == {
                "messages": [{"role": "user", "content": "hi"}],
                "model": "default-model",
                "temperature": 0,
            }
            assert request.extensions["timeout"]["read"] == 60
    finally:
        await provider.aclose()


@pytest.mark.usefixtures("default_env")
@pytest.mark.parametrize("prefix", ["LLM_", "ENRICH_LLM_"])
@pytest.mark.parametrize("suffix", ["API_KEY", "BASE_URL", "MODEL"])
def test_blank_required_values_fail_locally_without_allocating_clients(
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
    prefix: str,
    suffix: str,
) -> None:
    for key, value in {
        "API_KEY": "test-enrich-credential",
        "BASE_URL": "https://enrich.example.test/v1",
        "MODEL": "enrich-model",
    }.items():
        monkeypatch.setenv(f"ENRICH_LLM_{key}", value)
    monkeypatch.setenv(f"{prefix}{suffix}", " \t ")

    with pytest.raises(RuntimeError, match=f"{prefix}{suffix}"):
        OpenAICompatProvider.from_env()

    assert http_recorder.clients == []


@pytest.mark.usefixtures("default_env")
@pytest.mark.parametrize("value", ["test-secret-in-timeout", "nan", "inf", "0", "-1"])
def test_invalid_timeout_is_rejected_without_echoing_its_value(
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
    value: str,
) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="LLM_TIMEOUT_SECONDS 必须是有限正数") as caught:
        OpenAICompatProvider.from_env()
    assert "test-secret" not in repr(caught.value)
    assert http_recorder.clients == []


def test_role_config_repr_does_not_expose_credentials() -> None:
    config = RoleConfig(
        api_key="test-private-key-sentinel", base_url="https://api.example.test/v1", model="m"
    )
    assert "test-private-key-sentinel" not in repr(config)


@pytest.mark.usefixtures("default_env")
@pytest.mark.parametrize("explicit_enrich", [False, True])
def test_settings_and_diagnostics_reflect_configuration_source_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
    explicit_enrich: bool,
) -> None:
    if explicit_enrich:
        for suffix, value in {
            "API_KEY": "test-enrich-credential",
            "BASE_URL": "https://enrich.example.test/v1",
            "MODEL": "enrich-model",
        }.items():
            monkeypatch.setenv(f"ENRICH_LLM_{suffix}", value)
    provider = OpenAICompatProvider.from_env()
    store = TraceStore(tmp_path / "trace.db")
    store.record(AgentEvent(type="turn.ended", seq=0, ts=1, trace_id="config-trace", payload={}))
    store.close()
    app = create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db", trace_db_path=tmp_path / "trace.db"
        ),
        provider=provider,
        provider_close=provider.aclose,
    )
    with TestClient(app) as client:
        settings = client.get("/api/v1/settings")
        bundle = client.get("/api/v1/observability/traces/config-trace/diagnostic-bundle")
    assert settings.status_code == bundle.status_code == 200
    views = settings.json()["providers"][:2]
    identities = bundle.json()["config_identity"]["providers"][:2]
    for view, identity in zip(views, identities, strict=True):
        distinct = view["role"] == "enrich" and explicit_enrich
        prefix = "ENRICH_LLM_" if distinct else "LLM_"
        assert view["required_env_vars"] == [
            prefix + key for key in ("API_KEY", "BASE_URL", "MODEL")
        ]
        assert (
            view["model"] == identity["model"] == ("enrich-model" if distinct else "default-model")
        )
        assert (
            view["endpoint_host"]
            == identity["endpoint_host"]
            == ("enrich.example.test" if distinct else "default.example.test")
        )
    for output in (settings.text, bundle.text, repr(provider.execution_config_for_role)):
        assert "test-default-credential" not in output
        assert "test-enrich-credential" not in output
    assert "required_env_vars" not in bundle.text
    assert http_recorder.requests == []
    assert all(client.is_closed for client in http_recorder.clients)


@pytest.mark.usefixtures("default_env")
@pytest.mark.parametrize(
    "suffix,value",
    [
        ("API_KEY", "test-enrich-credential"),
        ("BASE_URL", "https://enrich.example.test/v1"),
        ("MODEL", "enrich-model"),
        ("TIMEOUT_SECONDS", "30"),
        ("ONLY_PROVIDER", "vendor-b"),
        ("API_DIALECT", "dashscope"),
        ("THINKING_MODE", "disabled"),
        ("REASONING_EFFORT", "high"),
        ("DISABLE_THINKING", "false"),
    ],
)
def test_partial_enrich_configuration_never_borrows_default_credentials(
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
    suffix: str,
    value: str,
) -> None:
    monkeypatch.setenv(f"ENRICH_LLM_{suffix}", value)
    with pytest.raises(RuntimeError, match="缺少环境变量 ENRICH_LLM_") as caught:
        OpenAICompatProvider.from_env()
    assert value not in str(caught.value)
    assert http_recorder.clients == []


@pytest.mark.usefixtures("default_env")
def test_enrich_only_configuration_cannot_replace_required_default(
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
) -> None:
    for suffix, value in {
        "API_KEY": "test-enrich-credential",
        "BASE_URL": "https://enrich.example.test/v1",
        "MODEL": "enrich-model",
    }.items():
        monkeypatch.delenv(f"LLM_{suffix}")
        monkeypatch.setenv(f"ENRICH_LLM_{suffix}", value)
    with pytest.raises(RuntimeError, match="缺少环境变量 LLM_BASE_URL"):
        OpenAICompatProvider.from_env()
    assert http_recorder.clients == []


@pytest.mark.usefixtures("default_env")
@pytest.mark.parametrize("overridden_role", ["basic", "enrich"])
async def test_overrides_after_inheritance_do_not_mutate_the_other_slot(
    http_recorder: _HTTPRecorder,
    overridden_role: Role,
) -> None:
    provider = OpenAICompatProvider.from_env(
        role_overrides={
            overridden_role: RoleOverrides(
                model="experiment-model",
                api_dialect="dashscope",
                thinking_mode="disabled",
                reasoning_effort="none",
            ),
        }
    )
    try:
        for role in ("basic", "enrich"):
            await provider.complete([Message(role="user", content="hi")], role=role)
        for role, request in zip(("basic", "enrich"), http_recorder.requests, strict=True):
            body = json.loads(request.content)
            if role == overridden_role:
                assert body["model"] == "experiment-model"
                assert body["enable_thinking"] is False
                assert "thinking" not in body
                assert "reasoning_effort" not in body
            else:
                assert body["model"] == "default-model"
                assert body["thinking"] == {"type": "enabled"}
                assert body["reasoning_effort"] == "high"
                assert "enable_thinking" not in body
            assert request.headers["authorization"] == "Bearer test-default-credential"
        identity = provider.execution_config_for_role[overridden_role]
        assert identity.model == "experiment-model"
        assert identity.replay_identity == (
            "experiment-model|provider=dashscope|thinking=disabled|effort=none"
        )
    finally:
        await provider.aclose()


@pytest.mark.usefixtures("default_env")
@pytest.mark.parametrize("same_endpoint", [False, True])
async def test_complete_legacy_enrich_configuration_remains_isolated(
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
    same_endpoint: bool,
) -> None:
    endpoint = "default.example.test" if same_endpoint else "enrich.example.test"
    for suffix, value in {
        "API_KEY": "test-enrich-credential",
        "BASE_URL": f"https://{endpoint}/v1",
        "MODEL": "enrich-model",
        "API_DIALECT": "deepseek" if same_endpoint else "dashscope",
        "TIMEOUT_SECONDS": "37",
        "DISABLE_THINKING": "true",
        "THINKING_MODE": "enabled",
        "ONLY_PROVIDER": "vendor-b",
    }.items():
        monkeypatch.setenv(f"ENRICH_LLM_{suffix}", value)
    provider = OpenAICompatProvider.from_env()
    try:
        for role in ("basic", "enrich"):
            await provider.complete([Message(role="user", content="hi")], role=role)
        basic, enrich = http_recorder.requests
        assert basic.headers["authorization"] == "Bearer test-default-credential"
        assert json.loads(basic.content)["model"] == "default-model"
        assert str(enrich.url) == f"https://{endpoint}/v1/chat/completions"
        assert enrich.headers["authorization"] == "Bearer test-enrich-credential"
        assert enrich.extensions["timeout"]["read"] == 37
        body = json.loads(enrich.content)
        assert body["model"] == "enrich-model"
        assert "reasoning_effort" not in body
        assert body["provider"] == {"only": ["vendor-b"], "allow_fallbacks": False}
        if same_endpoint:
            assert body["thinking"] == {"type": "enabled"}
        else:
            assert body["enable_thinking"] is True
        for role in ("basic", "enrich"):
            events = [
                event
                async for event in provider.stream_complete(
                    [Message(role="user", content="hi")], role=role
                )
            ]
            assert isinstance(events[-1], CompletionFinished)
        for complete, stream in zip(
            http_recorder.requests[:2], http_recorder.requests[2:], strict=True
        ):
            stream_body = json.loads(stream.content)
            assert stream_body.pop("stream") is True
            assert stream_body.pop("stream_options") == {"include_usage": True}
            assert stream_body == json.loads(complete.content)
            assert stream.url == complete.url
            assert stream.headers["authorization"] == complete.headers["authorization"]
    finally:
        await provider.aclose()


@pytest.mark.usefixtures("default_env")
async def test_same_model_keeps_legacy_replay_namespaces(
    http_recorder: _HTTPRecorder,
    tmp_path: Path,
) -> None:
    provider = OpenAICompatProvider.from_env()
    cassette = Cassette()
    recording = RecordingProvider(provider, cassette, provider.model_for_role, reuse_existing=True)
    messages = [Message(role="user", content="same prompt")]
    try:
        await recording.complete(messages, role="basic")
        with pytest.raises(ReplayMiss):
            await ReplayProvider(cassette, provider.model_for_role).complete(
                messages, role="enrich"
            )
        await recording.complete(messages, role="enrich")
        await recording.complete(messages, role="basic")
        assert len(http_recorder.requests) == 2
        path = tmp_path / "same-model.json"
        cassette.save(path)
        replay = ReplayProvider(Cassette.load(path), provider.model_for_role)
        assert (await replay.complete(messages, role="enrich")).text == "reply"
        assert (await replay.complete(messages, role="basic")).text == "reply"
        assert len(http_recorder.requests) == 2
    finally:
        await provider.aclose()


@pytest.mark.usefixtures("default_env")
async def test_all_blank_enrich_fields_inherit_default_for_streaming(
    monkeypatch: pytest.MonkeyPatch,
    http_recorder: _HTTPRecorder,
) -> None:
    for suffix in (
        "API_KEY",
        "BASE_URL",
        "MODEL",
        "TIMEOUT_SECONDS",
        "ONLY_PROVIDER",
        "API_DIALECT",
        "THINKING_MODE",
        "REASONING_EFFORT",
        "DISABLE_THINKING",
    ):
        monkeypatch.setenv(f"ENRICH_LLM_{suffix}", " \t ")
    provider = OpenAICompatProvider.from_env()
    try:
        for role in ("basic", "enrich"):
            events = [
                event
                async for event in provider.stream_complete(
                    [Message(role="user", content="hi")], role=role
                )
            ]
            assert events[0] == TextDelta(text="reply")
            assert len(events) == 2
            terminal = events[1]
            assert isinstance(terminal, CompletionFinished)
            assert terminal.completion.text == "reply"
            assert terminal.completion.usage.total_tokens == 5
        assert len(http_recorder.requests) == 2
        first, second = http_recorder.requests
        assert first.content == second.content
        assert first.url == second.url
        assert first.headers["authorization"] == second.headers["authorization"]
        body = json.loads(second.content)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        assert body["provider"] == {"only": ["vendor-a"], "allow_fallbacks": False}
        assert second.extensions["timeout"]["read"] == 12.5
    finally:
        await provider.aclose()
    assert all(client.is_closed for client in http_recorder.clients)

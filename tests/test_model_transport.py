"""A bound model speaks transport, not business purpose or legacy role."""

import json
from collections.abc import Iterator

import httpx
import pytest
from openai import AsyncOpenAI

import grandquiz.providers.llm as llm_module
from grandquiz.providers.base import Message
from grandquiz.providers.failure import ProviderFailure
from grandquiz.providers.llm import ChatModelConfig, OpenAIChatModel
from grandquiz.providers.models import ModelRuntime
from grandquiz.providers.profiles import ModelConfigurationError, parse_model_config


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "response",
                "created": 0,
                "model": "wire-model",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "answer"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def create(
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
    ) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )

    monkeypatch.setattr(llm_module, "AsyncOpenAI", create)
    yield requests


async def test_single_model_executes_only_its_resolved_transport_configuration(
    wire: list[httpx.Request],
) -> None:
    model = OpenAIChatModel(
        ChatModelConfig(
            api_key="test-wire-key",
            base_url="https://api.example.test/tenant/v1",
            model="writer",
            api_dialect="deepseek",
            thinking_mode="enabled",
            reasoning_effort="high",
        )
    )
    try:
        result = await model.complete([Message(role="user", content="hello")])
        assert result.text == "answer"
        assert len(wire) == 1
        assert str(wire[0].url) == "https://api.example.test/tenant/v1/chat/completions"
        assert json.loads(wire[0].content) == {
            "model": "writer",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        }
    finally:
        await model.aclose()


PROFILE_CONFIG = """
schema_version = "model-config.v1"
default_profile = "shared"
[connections.primary]
base_url = "https://api.example.test/tenant/v1"
api_key_env = "TEST_MODEL_KEY"
[profiles.shared]
connection = "primary"
model = "shared-model"
[profiles.writer]
connection = "primary"
model = "writer-model"
[purpose_overrides]
question_generation = "writer"
"""


async def test_bound_purposes_send_their_selected_model_and_keep_configuration_frozen(
    wire: list[httpx.Request],
) -> None:
    config = parse_model_config(PROFILE_CONFIG, purposes={"question_generation", "answer_grading"})
    environment = {"TEST_MODEL_KEY": "test-credential"}
    runtime = ModelRuntime.from_configuration(config, environment=environment)
    environment["TEST_MODEL_KEY"] = "changed-credential"
    try:
        writer = runtime.bindings.for_purpose("question_generation")
        grader = runtime.bindings.for_purpose("answer_grading")
        await writer.complete([Message(role="user", content="same")])
        await grader.complete([Message(role="user", content="same")])
        assert [json.loads(request.content)["model"] for request in wire] == [
            "writer-model",
            "shared-model",
        ]
        assert all(request.headers["authorization"] == "Bearer test-credential" for request in wire)
        assert runtime.bindings.identity_for("question_generation") == config.identity_for(
            "question_generation"
        )
        with pytest.raises(ModelConfigurationError):
            runtime.bindings.for_purpose("unknown")
    finally:
        await runtime.aclose()


def test_missing_credentials_fail_before_any_transport_is_allocated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = parse_model_config(PROFILE_CONFIG, purposes={"question_generation", "answer_grading"})

    def forbidden(**kwargs: object) -> None:
        raise AssertionError("must validate all credentials before allocating clients")

    monkeypatch.setattr(llm_module, "AsyncOpenAI", forbidden)
    with pytest.raises(ModelConfigurationError) as caught:
        ModelRuntime.from_configuration(config, environment={})
    assert caught.value.code == "missing_credential"
    assert "TEST_MODEL_KEY" not in str(caught.value)


async def test_single_model_disables_hidden_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            503,
            request=request,
            json={"error": {"message": "unavailable", "code": "server_error"}},
        )

    def create(
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int = 2,
    ) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )

    monkeypatch.setattr(llm_module, "AsyncOpenAI", create)
    model = OpenAIChatModel(
        ChatModelConfig(
            api_key="test-key",
            base_url="https://api.example.test/v1",
            model="test-model",
        )
    )
    try:
        with pytest.raises(ProviderFailure):
            await model.complete([Message(role="user", content="hello")])
    finally:
        await model.aclose()

    assert len(requests) == 1

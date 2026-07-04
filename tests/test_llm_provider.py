"""OpenAICompatProvider 测试——mock 掉 AsyncOpenAI，确定性、零网络、不烧 token。

真实连通性由 scripts/smoke_llm.py 手动验（那才碰活 API）；这里只钉住可确定化的行为：
env 缺变量即报错、messages / response 映射、disable_thinking → extra_body 的开关逻辑。
"""

import pytest

import grandquiz.providers.llm as llm_mod
from grandquiz.providers.base import Message
from grandquiz.providers.llm import OpenAICompatProvider, RoleConfig


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(self, content: str | None, prompt_tokens: int, completion_tokens: int) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


class _FakeCompletions:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.chat = _FakeChat(_FakeCompletions(response))

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


def test_from_env_raises_on_missing_required_var(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError):
        OpenAICompatProvider.from_env()


async def test_complete_maps_messages_and_response_and_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_client(
        monkeypatch, _FakeResponse("连通正常", prompt_tokens=11, completion_tokens=3)
    )
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="u", model="m-basic", disable_thinking=True)}
    )

    reply = await provider.complete([Message(role="user", content="hi")], role="basic")

    assert reply.text == "连通正常"
    assert reply.usage.prompt_tokens == 11
    assert reply.usage.completion_tokens == 3
    call = captured["client"].chat.completions.calls[0]
    assert call["model"] == "m-basic"
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert call["extra_body"] == {"enable_thinking": False}


async def test_complete_omits_extra_body_when_thinking_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_client(monkeypatch, _FakeResponse("ok", prompt_tokens=1, completion_tokens=1))
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="u", model="m", disable_thinking=False)}
    )

    await provider.complete([Message(role="user", content="hi")], role="basic")

    call = captured["client"].chat.completions.calls[0]
    assert call["extra_body"] is None

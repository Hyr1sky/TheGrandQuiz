"""OpenAICompatProvider 测试——mock 掉 AsyncOpenAI，确定性、零网络、不烧 token。

真实连通性由 scripts/smoke_llm.py 手动验（那才碰活 API）；这里只钉住可确定化的行为：
env 缺变量即报错、messages / response 映射、disable_thinking → extra_body 的开关逻辑。
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import pytest
from openai import omit
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
from grandquiz.providers.llm import OpenAICompatProvider, RoleConfig


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

    async def __aiter__(self) -> AsyncIterator[object]:
        for chunk in self._chunks:
            yield chunk


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
        self._stream = _FakeStream(chunks)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self._stream


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
    provider = OpenAICompatProvider(
        {"basic": RoleConfig(api_key="k", base_url="u", model="m", disable_thinking=False)}
    )

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


class _CapturingProvider:
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


async def test_run_agent_turn_records_role_in_model_started_payload() -> None:
    provider = _CapturingProvider()
    emitter, events = _events_emitter()
    runner = Runner(provider=provider, emitter=emitter)

    await runner.run_agent_turn("q")

    started = [e for e in events if e.type == EventType.MODEL_STARTED]
    assert len(started) == 1
    # 修 dogfood trace 里 role 为空：ReAct 生成显式 role="basic" 且落进 model.started payload。
    assert started[0].payload["role"] == "basic"
    assert provider.role_seen == "basic"

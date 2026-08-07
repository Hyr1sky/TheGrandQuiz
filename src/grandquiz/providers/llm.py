"""OpenAICompatProvider——OpenAI 兼容的真实 LLM provider（basic=deepseek / enrich=qwen）。

两个命名角色各自从 ``.env`` 读 base_url / api_key / model / timeout / dialect / thinking
mode；DeepSeek 与 Qwen 都提供 OpenAI 兼容端点，但 thinking 扩展字段不同，故共用
``AsyncOpenAI`` 客户端、在本边界按方言组装请求。密钥只经环境变量注入，绝不进代码 / git
（见 AGENTS.md 密钥纪律）。

实现 ``providers/base.py`` 的 ``Provider`` 协议——因此在 ingest / Reader 里可与 DemoEcho /
Record / Replay 互换：测试传假件、录制传 ``RecordingProvider(OpenAICompatProvider.from_env())``、
CI 回放传 ``ReplayProvider(cassette)``，调用方不变。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, cast
from urllib.parse import urlparse

from openai import AsyncOpenAI, Omit, omit
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    ProviderStreamEvent,
    ProviderStreamProtocolError,
    Role,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
    mark_malformed_arguments,
)

_TRUTHY = {"1", "true", "yes", "on"}
ProviderDialect = Literal["deepseek", "dashscope", "generic"]
ThinkingMode = Literal["provider_default", "enabled", "disabled"]
ReasoningEffort = Literal["high", "max"]


def _to_oai_messages(messages: Sequence[Message]) -> list[ChatCompletionMessageParam]:
    """本 runtime 的 ``Message`` → OpenAI 线上形状（provider 边界做内部 dict ⇄ JSON 串译码）。

    - assistant 带 ``tool_calls``：内部 ``arguments`` dict 在此转成 OpenAI 要求的 JSON **字符串**；
      ``content`` 空串归一到 ``None``（OpenAI 对工具请求消息的惯例）。
    - ``role="tool"`` 结果消息 → ``{"role":"tool","tool_call_id","content"}``。
    - 其余（system / user / 无工具 assistant）→ ``{"role","content"}``（旧形状不变）。
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        else:
            out.append({"role": m.role, "content": m.content})
    return cast("list[ChatCompletionMessageParam]", out)


def _to_oai_tools(tools: Sequence[ToolSpec]) -> list[ChatCompletionToolParam]:
    """``ToolSpec`` 列表 → OpenAI 原生 ``tools=[{"type":"function","function":{...}}]``。"""
    specs: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]
    return cast("list[ChatCompletionToolParam]", specs)


def _decode_tool_arguments(arguments_json: str) -> dict[str, Any]:
    """把厂商 JSON 参数归一为内部 dict；畸形值进入统一的可恢复标记态。"""
    try:
        decoded: Any = json.loads(arguments_json)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        return cast("dict[str, Any]", decoded)
    return mark_malformed_arguments(arguments_json)


def _parse_tool_calls(message: Any) -> list[ToolCall] | None:
    """OpenAI ``response.choices[0].message.tool_calls`` → 内部 ``ToolCall`` 列表（无则 None）。

    边界解码：每个 ``function.arguments`` 是 JSON **字符串**，在此转回内部 dict——与出栈映射对称。

    **对畸形参数鲁棒（dogfood 762884ba）**：LLM 乱吐坏 tool_call 是常态。``json.loads`` 抛
    ``JSONDecodeError``（串畸形）或解出非对象（裸数组 / 标量）时**不裸抛炸会话**，而是把原始畸形串
    裹进 ``mark_malformed_arguments`` 的"参数非法"标记态。该标记随 ``ToolCall.arguments`` 流到
    ``ToolRegistry.dispatch``：dispatch 认出它 → ``ModelRetry(DEGRADED)`` → 走 M6 RecoveryPolicy 与
    "合法但校验不过"**同一条**降级恢复路径（回灌错误让 LLM 下一轮改对）。合法对象参数照原样解码，
    既有路径逐字节不变（不影响 record/replay：本函数只在真 provider 边界跑，cassette 不经此路径）。
    """
    raw = getattr(message, "tool_calls", None)
    if not raw:
        return None
    parsed: list[ToolCall] = []
    for tc in raw:
        arguments_json: str = tc.function.arguments or "{}"
        parsed.append(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=_decode_tool_arguments(arguments_json),
            )
        )
    return parsed


@dataclass(frozen=True)
class RoleConfig:
    """一个命名角色的 LLM 配置（对应 .env 的一组 ``<PREFIX>*`` 变量）。"""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    # OpenRouter BYOK 可选约束：指定后只允许该 provider，且禁用共享端点 fallback。
    only_provider: str | None = None
    api_dialect: ProviderDialect = "generic"
    thinking_mode: ThinkingMode = "provider_default"
    reasoning_effort: ReasoningEffort | None = None


@dataclass(frozen=True)
class RoleOverrides:
    """Non-secret experiment overrides applied after role credentials are loaded."""

    model: str | None = None
    api_dialect: ProviderDialect | None = None
    thinking_mode: ThinkingMode | None = None
    reasoning_effort: ReasoningEffort | Literal["none"] | None = None


@dataclass(frozen=True)
class ProviderExecutionConfig:
    """Non-secret resolved request identity for audit and replay separation."""

    provider: ProviderDialect
    endpoint_host: str
    model: str
    thinking_mode: ThinkingMode
    reasoning_effort: ReasoningEffort | None
    replay_identity: str


@dataclass(frozen=True)
class _PreparedChatRequest:
    """complete 与 stream 共用的厂商请求准备结果。"""

    client: AsyncOpenAI
    model: str
    messages: list[ChatCompletionMessageParam]
    extra_body: dict[str, object] | None
    tools: list[ChatCompletionToolParam] | Omit


def _read_role(prefix: str) -> RoleConfig:
    def required(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(f"缺少环境变量 {name}（见 .env.example）")
        return value

    base_url = required(f"{prefix}BASE_URL")
    dialect_value = os.environ.get(f"{prefix}API_DIALECT", "").strip().casefold()
    if dialect_value:
        if dialect_value not in {"deepseek", "dashscope", "generic"}:
            raise ValueError(f"{prefix}API_DIALECT 必须是 deepseek/dashscope/generic")
        dialect = cast("ProviderDialect", dialect_value)
    else:
        host = (urlparse(base_url).hostname or "").casefold()
        dialect = (
            "deepseek"
            if host == "api.deepseek.com"
            else "dashscope"
            if host == "dashscope.aliyuncs.com"
            else "generic"
        )
    thinking_value = os.environ.get(f"{prefix}THINKING_MODE", "").strip().casefold()
    if thinking_value:
        if thinking_value not in {"provider_default", "enabled", "disabled"}:
            raise ValueError(f"{prefix}THINKING_MODE 必须是 provider_default/enabled/disabled")
        thinking_mode = cast("ThinkingMode", thinking_value)
    else:
        legacy_disabled = os.environ.get(f"{prefix}DISABLE_THINKING", "").strip().lower() in _TRUTHY
        thinking_mode = "disabled" if legacy_disabled else "provider_default"
    effort_value = os.environ.get(f"{prefix}REASONING_EFFORT", "").strip().casefold()
    if effort_value and effort_value not in {"high", "max"}:
        raise ValueError(f"{prefix}REASONING_EFFORT 必须是 high/max")
    return RoleConfig(
        api_key=required(f"{prefix}API_KEY"),
        base_url=base_url,
        model=required(f"{prefix}MODEL"),
        timeout_seconds=float(os.environ.get(f"{prefix}TIMEOUT_SECONDS", "60")),
        only_provider=os.environ.get(f"{prefix}ONLY_PROVIDER", "").strip() or None,
        api_dialect=dialect,
        thinking_mode=thinking_mode,
        reasoning_effort=cast("ReasoningEffort | None", effort_value or None),
    )


class OpenAICompatProvider:
    """OpenAI 兼容 provider：按角色路由到各自的 base_url / model。"""

    def __init__(self, role_configs: dict[Role, RoleConfig]) -> None:
        self._configs = role_configs
        self._clients: dict[Role, AsyncOpenAI] = {
            role: AsyncOpenAI(
                api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout_seconds
            )
            for role, cfg in role_configs.items()
        }

    @classmethod
    def from_env(
        cls,
        *,
        role_overrides: Mapping[Role, RoleOverrides] | None = None,
    ) -> OpenAICompatProvider:
        """从 .env 读两角色：``LLM_*`` → basic（deepseek）、``ENRICH_LLM_*`` → enrich（qwen）。"""

        configs: dict[Role, RoleConfig] = {
            "basic": _read_role("LLM_"),
            "enrich": _read_role("ENRICH_LLM_"),
        }
        for role, override in (role_overrides or {}).items():
            current = configs[role]
            configs[role] = replace(
                current,
                model=override.model or current.model,
                api_dialect=override.api_dialect or current.api_dialect,
                thinking_mode=override.thinking_mode or current.thinking_mode,
                reasoning_effort=(
                    None
                    if override.reasoning_effort == "none"
                    else override.reasoning_effort
                    if override.reasoning_effort is not None
                    else current.reasoning_effort
                ),
            )
        return cls(configs)

    @property
    def model_for_role(self) -> dict[Role, str]:
        """各角色解析后的 model id——喂 Recording/Replay 算 replay 键（防跨模型串键）。"""
        return {role: cfg.model for role, cfg in self._configs.items()}

    @property
    def execution_config_for_role(self) -> dict[Role, ProviderExecutionConfig]:
        """Return safe experiment identity; API keys never cross this Interface."""

        return {
            role: ProviderExecutionConfig(
                provider=cfg.api_dialect,
                endpoint_host=urlparse(cfg.base_url).hostname or "unknown",
                model=cfg.model,
                thinking_mode=cfg.thinking_mode,
                reasoning_effort=cfg.reasoning_effort,
                replay_identity=(
                    f"{cfg.model}|provider={cfg.api_dialect}|thinking={cfg.thinking_mode}|"
                    f"effort={cfg.reasoning_effort or 'none'}"
                ),
            )
            for role, cfg in self._configs.items()
        }

    def _prepare_request(
        self,
        messages: Sequence[Message],
        *,
        role: Role,
        tools: Sequence[ToolSpec] | None,
    ) -> _PreparedChatRequest:
        config = self._configs[role]
        extra_body: dict[str, object] = {}
        if config.api_dialect == "deepseek":
            if config.thinking_mode != "provider_default":
                extra_body["thinking"] = {"type": config.thinking_mode}
            if config.reasoning_effort is not None:
                if config.thinking_mode == "disabled":
                    raise ValueError("DeepSeek reasoning_effort 不能与 disabled thinking 同时使用")
                extra_body["reasoning_effort"] = config.reasoning_effort
        elif config.api_dialect == "dashscope":
            if config.thinking_mode != "provider_default":
                extra_body["enable_thinking"] = config.thinking_mode == "enabled"
            if config.reasoning_effort is not None:
                raise ValueError("DashScope 角色不支持 DeepSeek reasoning_effort 契约")
        elif config.thinking_mode != "provider_default":
            extra_body["enable_thinking"] = config.thinking_mode == "enabled"
        if config.only_provider is not None:
            extra_body["provider"] = {
                "only": [config.only_provider],
                "allow_fallbacks": False,
            }
        return _PreparedChatRequest(
            client=self._clients[role],
            model=config.model,
            messages=_to_oai_messages(messages),
            extra_body=extra_body or None,
            tools=_to_oai_tools(tools) if tools else omit,
        )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        request = self._prepare_request(messages, role=role, tools=tools)
        # tools 走 omit 哨兵：无工具 → 与"不传该参数"等价（线上请求逐字节不变），既有纯文本
        # completion 路径与 golden cassette 完全不受影响（replay_key 也不含 tools）。
        response = await request.client.chat.completions.create(
            model=request.model,
            messages=request.messages,
            # temperature=0：出题（enrich）必须贪心解码——温度采样会让同一 message 每次录出不同题，
            # 毁掉 record/replay 的可复现（replay_key 只按 message 算、不含温度，故这不改键、只稳定
            # 录制输出）；判卷 / ReAct（basic）同样设 0 求判决稳定。
            temperature=0,
            extra_body=request.extra_body,
            tools=request.tools,
        )
        message = response.choices[0].message
        tool_calls = _parse_tool_calls(message)
        text = message.content or ""
        usage = Usage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
        )
        return Completion(text=text, tool_calls=tool_calls, usage=usage)

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """把 OpenAI chunk 归一成文本增量，并在边界内组装完整 tool calls。"""
        request = self._prepare_request(messages, role=role, tools=tools)
        raw_stream = await request.client.chat.completions.create(
            model=request.model,
            messages=request.messages,
            temperature=0,
            extra_body=request.extra_body,
            tools=request.tools,
            stream=True,
            stream_options={"include_usage": True},
        )
        stream = cast("Any", raw_stream)

        text_parts: list[str] = []
        tool_fragments: dict[int, dict[str, str]] = {}
        prompt_tokens = 0
        completion_tokens = 0

        async for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                prompt_tokens = int(getattr(chunk_usage, "prompt_tokens", 0))
                completion_tokens = int(getattr(chunk_usage, "completion_tokens", 0))

            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = choices[0].delta
            content = getattr(delta, "content", None) or ""
            raw_tool_calls = cast(
                "list[Any]",
                getattr(delta, "tool_calls", None) or [],
            )

            if content:
                text_parts.append(content)
                yield TextDelta(text=content)

            for raw_tool_call in raw_tool_calls:
                index = int(raw_tool_call.index)
                fragment = tool_fragments.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                tool_call_id = getattr(raw_tool_call, "id", None)
                if tool_call_id:
                    fragment["id"] = str(tool_call_id)
                function = getattr(raw_tool_call, "function", None)
                if function is None:
                    continue
                name = getattr(function, "name", None)
                arguments = getattr(function, "arguments", None)
                if name:
                    fragment["name"] += str(name)
                if arguments:
                    fragment["arguments"] += str(arguments)

        tool_calls: list[ToolCall] | None = None
        if tool_fragments:
            tool_calls = []
            for index in sorted(tool_fragments):
                fragment = tool_fragments[index]
                if not fragment["id"] or not fragment["name"]:
                    raise ProviderStreamProtocolError(
                        f"tool call #{index} 缺少 id 或 function name"
                    )
                arguments_json = fragment["arguments"] or "{}"
                tool_calls.append(
                    ToolCall(
                        id=fragment["id"],
                        name=fragment["name"],
                        arguments=_decode_tool_arguments(arguments_json),
                    )
                )

        yield CompletionFinished(
            completion=Completion(
                text="".join(text_parts),
                tool_calls=tool_calls,
                usage=Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
            )
        )

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端（长生命周期 provider 退出时调用）。"""
        for client in self._clients.values():
            await client.close()

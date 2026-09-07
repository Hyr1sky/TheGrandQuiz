"""OpenAI-compatible 模型传输，以及旧双槽 Provider 的兼容 facade。

``OpenAIChatModel`` 接收一个已解析的 ``ChatModelConfig``，只执行 completion/stream 并在本边界
正规化错误；它不知道用途或学习领域。DeepSeek 与 DashScope 都提供 OpenAI-compatible endpoint，
但 thinking 扩展字段不同，故共用 ``AsyncOpenAI`` 客户端并在本边界按方言组装请求。

``OpenAICompatProvider`` 仅保留旧 ``basic/enrich`` 配置、录制与测试资产的显式入口；新的生产装配经
``interfaces/model_config.py`` 创建用途绑定的 ``ModelRuntime``。密钥只由环境引用解析，绝不进入代码、
git、执行身份或公共诊断。
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast
from urllib.parse import urlparse

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    Omit,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
    omit,
)
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
from grandquiz.providers.failure import (
    ProviderFailure,
    ProviderFailureCategory,
    RetryAfter,
    parse_retry_after,
    safe_provider_code,
)
from grandquiz.providers.legacy import LegacyPurposeProvider

_TRUTHY = {"1", "true", "yes", "on"}
ProviderDialect = Literal["deepseek", "dashscope", "generic"]
ThinkingMode = Literal["provider_default", "enabled", "disabled"]
ReasoningEffort = Literal["high", "max"]
RoleEnvPrefix = Literal["LLM_", "ENRICH_LLM_"]

_NON_RETRYABLE_QUOTA_CODES = frozenset({"allocationquota.freetieronly"})

_ROLE_ENV_SUFFIXES = (
    "API_KEY",
    "BASE_URL",
    "MODEL",
    "TIMEOUT_SECONDS",
    "ONLY_PROVIDER",
    "API_DIALECT",
    "THINKING_MODE",
    "REASONING_EFFORT",
    "DISABLE_THINKING",
)


def _provider_code(exc: APIError) -> str | None:
    direct = safe_provider_code(getattr(exc, "code", None))
    if direct is not None:
        return direct
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return None
    body_mapping = cast("Mapping[str, object]", body)
    error = body_mapping.get("error")
    if isinstance(error, Mapping):
        return safe_provider_code(cast("Mapping[str, object]", error).get("code"))
    return safe_provider_code(body_mapping.get("code"))


def _is_quota_exhausted(exc: APIError, provider_code: str | None) -> bool:
    # DashScope also uses quota-shaped codes such as Throttling.AllocationQuota for
    # temporary TPS/TPM limits. Only codes with verified terminal semantics belong
    # here; every other 429 must retain the SDK's retryable rate-limit category.
    if provider_code is not None and provider_code.casefold() in _NON_RETRYABLE_QUOTA_CODES:
        return True
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return False
    body_mapping = cast("Mapping[str, object]", body)
    error = body_mapping.get("error")
    message = (
        cast("Mapping[str, object]", error).get("message")
        if isinstance(error, Mapping)
        else body_mapping.get("message")
    )
    return isinstance(message, str) and "free quota exhausted" in message.casefold()


def _retry_after(exc: APIError) -> RetryAfter:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return RetryAfter()
    value = cast("Mapping[str, object]", headers).get("retry-after")
    return RetryAfter() if value is None else parse_retry_after(value)


def _normalize_openai_failure(
    exc: APIError,
    *,
    response_started: bool = False,
) -> ProviderFailure:
    provider_code = _provider_code(exc)
    retry_after = _retry_after(exc)
    status_code = getattr(exc, "status_code", None)
    status = status_code if isinstance(status_code, int) else None

    if _is_quota_exhausted(exc, provider_code):
        category = ProviderFailureCategory.QUOTA_EXHAUSTED
        retryable = False
    elif isinstance(exc, APITimeoutError):
        category = ProviderFailureCategory.TIMEOUT
        retryable = True
    elif isinstance(exc, APIConnectionError):
        category = ProviderFailureCategory.CONNECTION
        retryable = True
    elif isinstance(exc, AuthenticationError):
        category = ProviderFailureCategory.AUTHENTICATION
        retryable = False
    elif isinstance(exc, PermissionDeniedError):
        category = ProviderFailureCategory.PERMISSION_DENIED
        retryable = False
    elif isinstance(exc, (BadRequestError, UnprocessableEntityError)):
        category = ProviderFailureCategory.INVALID_REQUEST
        retryable = False
    elif isinstance(exc, NotFoundError):
        category = ProviderFailureCategory.NOT_FOUND
        retryable = False
    elif isinstance(exc, ConflictError):
        category = ProviderFailureCategory.CONFLICT
        retryable = True
    elif isinstance(exc, RateLimitError):
        category = ProviderFailureCategory.RATE_LIMITED
        retryable = True
    elif isinstance(exc, APIStatusError) and status is not None and status >= 500:
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
class ChatModelConfig:
    """一个命名角色的 LLM 配置（对应 .env 的一组 ``<PREFIX>*`` 变量）。"""

    api_key: str = field(repr=False)
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    # OpenRouter BYOK 可选约束：指定后只允许该 provider，且禁用共享端点 fallback。
    only_provider: str | None = None
    api_dialect: ProviderDialect = "generic"
    thinking_mode: ThinkingMode = "provider_default"
    reasoning_effort: ReasoningEffort | None = None
    env_prefix: RoleEnvPrefix | None = None


# Explicit legacy configuration name; new transports accept ChatModelConfig.
RoleConfig = ChatModelConfig


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
    env_prefix: RoleEnvPrefix | None = None


@dataclass(frozen=True)
class _PreparedChatRequest:
    """complete 与 stream 共用的厂商请求准备结果。"""

    client: AsyncOpenAI
    model: str
    messages: list[ChatCompletionMessageParam]
    extra_body: dict[str, object] | None
    tools: list[ChatCompletionToolParam] | Omit


def _read_role(
    prefix: RoleEnvPrefix,
    *,
    environment: Mapping[str, str] | None = None,
) -> RoleConfig:
    env = os.environ if environment is None else environment

    def required(name: str) -> str:
        value = env.get(name)
        if not value or not value.strip():
            raise RuntimeError(f"缺少环境变量 {name}（见 .env.example）")
        return value

    base_url = required(f"{prefix}BASE_URL")
    dialect_value = env.get(f"{prefix}API_DIALECT", "").strip().casefold()
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
    thinking_value = env.get(f"{prefix}THINKING_MODE", "").strip().casefold()
    if thinking_value:
        if thinking_value not in {"provider_default", "enabled", "disabled"}:
            raise ValueError(f"{prefix}THINKING_MODE 必须是 provider_default/enabled/disabled")
        thinking_mode = cast("ThinkingMode", thinking_value)
    else:
        legacy_disabled = env.get(f"{prefix}DISABLE_THINKING", "").strip().lower() in _TRUTHY
        thinking_mode = "disabled" if legacy_disabled else "provider_default"
    effort_value = env.get(f"{prefix}REASONING_EFFORT", "").strip().casefold()
    if effort_value and effort_value not in {"high", "max"}:
        raise ValueError(f"{prefix}REASONING_EFFORT 必须是 high/max")
    try:
        timeout_seconds = float(env.get(f"{prefix}TIMEOUT_SECONDS", "60"))
    except ValueError:
        raise ValueError(f"{prefix}TIMEOUT_SECONDS 必须是有限正数") from None
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(f"{prefix}TIMEOUT_SECONDS 必须是有限正数")
    return RoleConfig(
        api_key=required(f"{prefix}API_KEY"),
        base_url=base_url,
        model=required(f"{prefix}MODEL"),
        timeout_seconds=timeout_seconds,
        only_provider=env.get(f"{prefix}ONLY_PROVIDER", "").strip() or None,
        api_dialect=dialect,
        thinking_mode=thinking_mode,
        reasoning_effort=cast("ReasoningEffort | None", effort_value or None),
        env_prefix=prefix,
    )


def read_legacy_role_configs(environment: Mapping[str, str]) -> dict[Role, RoleConfig]:
    """Explicit legacy importer, sharing PCP-01 validation without allocating SDK clients."""
    default = _read_role("LLM_", environment=environment)
    has_enrich = any(
        environment.get(f"ENRICH_LLM_{suffix}", "").strip() for suffix in _ROLE_ENV_SUFFIXES
    )
    return {
        "basic": default,
        "enrich": _read_role("ENRICH_LLM_", environment=environment) if has_enrich else default,
    }


class OpenAICompatProvider(LegacyPurposeProvider):
    """OpenAI 兼容 provider：按角色路由到各自的 base_url / model。"""

    def __init__(self, role_configs: dict[Role, RoleConfig]) -> None:
        # Legacy façade owns its two single-model transports.
        self._configs = role_configs
        self._models = {role: OpenAIChatModel(cfg) for role, cfg in role_configs.items()}

    @classmethod
    def from_env(
        cls,
        *,
        role_overrides: Mapping[Role, RoleOverrides] | None = None,
    ) -> OpenAICompatProvider:
        """读取默认配置与可选完整 enrich 配置，再独立应用各槽的非密钥覆盖参数。

        ENRICH 全空才继承；任何已支持字段非空都要求该组完整凭证，绝不跨组拼接。
        所有环境配置先解析完毕，再创建客户端；此处继承不代表失败后自动 fallback。
        """

        configs = read_legacy_role_configs(os.environ)
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
                env_prefix=cfg.env_prefix,
                replay_identity=(
                    f"{cfg.model}|provider={cfg.api_dialect}|thinking={cfg.thinking_mode}|"
                    f"effort={cfg.reasoning_effort or 'none'}"
                ),
            )
            for role, cfg in self._configs.items()
        }

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return await self._models[role].complete(messages, tools=tools)

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        async for event in self._models[role].stream_complete(messages, tools=tools):
            yield event

    async def aclose(self) -> None:
        for model in self._models.values():
            await model.aclose()


class OpenAIChatModel:
    """One resolved OpenAI-compatible model; no role or business selection."""

    def __init__(self, config: ChatModelConfig) -> None:
        self._config = config
        self._client = AsyncOpenAI(
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
    ) -> _PreparedChatRequest:
        config = self._config
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
            client=self._client,
            model=config.model,
            messages=_to_oai_messages(messages),
            extra_body=extra_body or None,
            tools=_to_oai_tools(tools) if tools else omit,
        )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        request = self._prepare_request(messages, tools=tools)
        # tools 走 omit 哨兵：无工具 → 与"不传该参数"等价（线上请求逐字节不变），既有纯文本
        # completion 路径与 golden cassette 完全不受影响（replay_key 也不含 tools）。
        try:
            response = await request.client.chat.completions.create(
                model=request.model,
                messages=request.messages,
                # temperature=0：结构化生成必须贪心解码。温度采样会让同一 message
                # 每次录出不同题，毁掉 record/replay 可复现性；判卷 / ReAct 同样取 0。
                temperature=0,
                extra_body=request.extra_body,
                tools=request.tools,
            )
        except APIError as exc:
            raise _normalize_openai_failure(exc) from exc
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
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        """把 OpenAI chunk 归一成文本增量，并在边界内组装完整 tool calls。"""
        request = self._prepare_request(messages, tools=tools)
        try:
            raw_stream = await request.client.chat.completions.create(
                model=request.model,
                messages=request.messages,
                temperature=0,
                extra_body=request.extra_body,
                tools=request.tools,
                stream=True,
                stream_options={"include_usage": True},
            )
        except APIError as exc:
            raise _normalize_openai_failure(exc) from exc
        stream = cast("Any", raw_stream)

        text_parts: list[str] = []
        tool_fragments: dict[int, dict[str, str]] = {}
        prompt_tokens = 0
        completion_tokens = 0

        primary_error: BaseException | None = None
        response_started = False
        try:
            async for chunk in stream:
                response_started = True
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
        except APIError as exc:
            primary_error = exc
            raise _normalize_openai_failure(exc, response_started=response_started) from exc
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
        await self._client.close()

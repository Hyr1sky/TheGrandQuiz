"""Provider 协议 + 所有 LLM provider 共享的 message / usage 类型。"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, computed_field

Role = Literal["basic", "enrich"]

# 畸形 tool_call 参数的**线上契约**（provider 边界 ⇄ kernel dispatch 共用）：真实 provider 解析
# ``function.arguments``（OpenAI 给的 JSON 串）时，若串畸形 / 非对象，无法产出合法入参 dict——不在
# provider 边界裸抛 ``JSONDecodeError`` 炸会话，而是把原始畸形串裹进这个保留 key 标成"参数非法"的
# 可恢复态。``ToolCall.arguments`` 是 ``dict[str, Any]``，故此标记随 ToolCall 一路流到
# ``ToolRegistry.dispatch``：dispatch 认出它 → 抛 ``ModelRetry(DEGRADED)`` → 走 M6 RecoveryPolicy
# 与"合法但校验不过"**同一条**降级恢复路径（回灌"参数非法，请重试"让 LLM 改对）。key 用极不可能与
# 真实入参撞名的前缀，避免误判合法参数。常量住 base.py（``ToolCall`` 定义处）：kernel→providers 是
# 合法依赖方向，dispatch import 它即可，反向（providers→kernel）被分层守卫禁止。
MALFORMED_TOOL_ARGUMENTS_KEY = "__grandquiz_malformed_tool_arguments__"


def mark_malformed_arguments(raw: str) -> dict[str, Any]:
    """把原始畸形 arguments 串裹成"参数非法"标记 dict（provider 边界解析失败时用）。"""
    return {MALFORMED_TOOL_ARGUMENTS_KEY: raw}


def malformed_arguments_raw(arguments: Mapping[str, Any]) -> str | None:
    """若 ``arguments`` 是"参数非法"标记态，返回原始畸形串；否则 ``None``（合法入参，照常校验）。

    dispatch 用它判定是否走 ``ModelRetry`` 降级——单一判定入口，sentinel key 不散落各处。
    """
    value = arguments.get(MALFORMED_TOOL_ARGUMENTS_KEY)
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的**通用**声明——喂给 provider 让 LLM 知道有哪些工具可调。

    刻意 provider 中立（不含 kernel 的 ``Tool`` / pydantic 语义）：``kernel.ToolRegistry`` 从每个
    ``Tool`` 的 pydantic 入参模型 ``model_json_schema()`` 生成 ``ToolSpec`` 列表，OpenAI 兼容
    provider 再把它译成 ``tools=[{"type":"function","function":{...}}]``。``parameters`` 是一份 JSON
    Schema dict（对象 schema），直接落进 OpenAI function 的 ``parameters`` 字段。
    """

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """OpenAI 兼容的 function-calling 形状：一次工具调用请求。

    ``arguments`` 存已解析的 dict（不是 OpenAI 的 JSON 字符串）——本 runtime 内部形状，进 messages
    经确定性 JSON 参与 ``replay_key``，故 dict 即可稳定 hash；对接真实 OpenAI 时在 provider 边界译。
    """

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """一条对话消息。

    - ``assistant`` 消息可携 ``tool_calls``（本轮 LLM 请求调的工具）；此时 ``content`` 常为空串。
    - ``role="tool"`` 是工具**结果**消息，用 ``tool_call_id`` 回指所答的那次 ``ToolCall``。
    - 纯文本消息（system / user / 无工具 assistant）两字段皆 None——``replay_key`` 用
      ``exclude_none`` 序列化，故其 hash 与加 tool 字段前逐字节一致（既有 cassette 不失效）。
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    # computed_field 而非裸 property：pydantic v2 的 model_dump() 不序列化普通 property，
    # 而 usage 要经 MODEL_ENDED payload 落 trace——total 必须进 dict，下游（Span.tokens /
    # M8 eval 成本列）才拿得到。反序列化时它作为 computed 输入被忽略、由字段重算，往返一致。
    @computed_field
    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Completion(BaseModel):
    """一次生成的结果——**要么** final 文本（``tool_calls is None``）、**要么**一批待执行的
    ``tool_calls``（此时 ``text`` 常为空）。tool 选择即本次 completion 的输出，故与文本走同一条
    record/replay 路径、被同一 ``replay_key`` 覆盖。"""

    text: str
    tool_calls: list[ToolCall] | None = None
    usage: Usage = Field(default_factory=Usage)


class TextDelta(BaseModel):
    """厂商无关的可展示文本增量。"""

    kind: Literal["text_delta"] = "text_delta"
    text: str


class CompletionFinished(BaseModel):
    """流的唯一终点；携带与 ``complete()`` 相同的权威结果。"""

    kind: Literal["completion_finished"] = "completion_finished"
    completion: Completion


type ProviderStreamEvent = TextDelta | CompletionFinished


class ProviderStreamProtocolError(RuntimeError):
    """上游流违反归一化契约，不能安全组装成一次 Completion。"""


class Provider(Protocol):
    """两个命名角色（basic / enrich）对应 .env 的两套 LLM 配置；角色间路由后续再加。

    ``tools`` 默认 ``None`` → 向后兼容：既有调用方（纯文本 completion）不传即无工具。传非空则
    provider 把它作为可调工具集告知 LLM（OpenAI 兼容 provider 译成原生 ``tools`` 字段）。
    不认识 function-calling 的 provider（echo / record / replay）忽略或透传该参数、行为不变。
    """

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion: ...


@runtime_checkable
class StreamingProvider(Provider, Protocol):
    """可选的原生流能力；completion-only provider 仍只需实现 ``Provider``。"""

    def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]: ...

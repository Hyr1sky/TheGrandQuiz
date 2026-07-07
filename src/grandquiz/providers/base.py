"""Provider 协议 + 所有 LLM provider 共享的 message / usage 类型。"""

from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, computed_field

Role = Literal["basic", "enrich"]


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


class Provider(Protocol):
    """两个命名角色（basic / enrich）对应 .env 的两套 LLM 配置；角色间路由后续再加。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic"
    ) -> Completion: ...

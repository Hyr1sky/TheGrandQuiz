"""Provider 协议 + 所有 LLM provider 共享的 message / usage 类型。"""

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, Field, computed_field

Role = Literal["basic", "enrich"]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


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
    text: str
    usage: Usage = Field(default_factory=Usage)


class Provider(Protocol):
    """两个命名角色（basic / enrich）对应 .env 的两套 LLM 配置；角色间路由后续再加。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic"
    ) -> Completion: ...

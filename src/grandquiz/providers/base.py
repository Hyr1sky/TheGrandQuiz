"""Provider 协议 + 所有 LLM provider 共享的 message / usage 类型。"""

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, Field

Role = Literal["basic", "enrich"]


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

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

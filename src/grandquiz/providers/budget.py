"""完整 Provider 请求预算装饰器。"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    Provider,
    ProviderStreamEvent,
    Role,
    StreamingProvider,
    TextDelta,
    ToolSpec,
)


class TokenEstimator(Protocol):
    def count(self, text: str) -> int: ...


class ProviderRequestBudgetExceeded(RuntimeError):
    def __init__(self, *, messages: int, tools: int, ceiling: int) -> None:
        self.messages = messages
        self.tools = tools
        self.used = messages + tools
        self.ceiling = ceiling
        super().__init__(
            f"Provider 请求 {self.used} tokens 超过硬上限 {ceiling} "
            f"(messages={messages}, tools={tools})"
        )


@dataclass(frozen=True)
class BudgetedProvider:
    inner: Provider
    counter: TokenEstimator
    ceiling: int

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self._ensure_within_budget(messages, tools)
        return await self.inner.complete(messages, role=role, tools=tools)

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        self._ensure_within_budget(messages, tools)
        if isinstance(self.inner, StreamingProvider):
            async for event in self.inner.stream_complete(
                messages,
                role=role,
                tools=tools,
            ):
                yield event
            return

        completion = await self.inner.complete(
            messages,
            role=role,
            tools=tools,
        )
        if completion.text:
            yield TextDelta(text=completion.text)
        yield CompletionFinished(completion=completion)

    def _ensure_within_budget(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
    ) -> None:
        message_tokens = self.counter.count(
            json.dumps(
                [message.model_dump(exclude_none=True) for message in messages],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        tool_tokens = self.counter.count(
            json.dumps(
                [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    }
                    for tool in tools or ()
                ],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        if message_tokens + tool_tokens > self.ceiling:
            raise ProviderRequestBudgetExceeded(
                messages=message_tokens,
                tools=tool_tokens,
                ceiling=self.ceiling,
            )

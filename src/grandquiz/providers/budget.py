"""完整 Provider 请求预算装饰器。"""

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from grandquiz.providers.base import (
    Completion,
    CompletionFinished,
    Message,
    Model,
    Provider,
    ProviderStreamEvent,
    Role,
    StreamingModel,
    StreamingProvider,
    TextDelta,
    ToolSpec,
)
from grandquiz.providers.legacy import LegacyPurposeProvider
from grandquiz.providers.models import (
    ModelFallbackPlan,
    ModelSource,
    bind_model,
    fallback_plan_of,
    identities_of,
    identity_of,
    retry_runtime_of,
    select_model,
    selection_options_of,
)
from grandquiz.providers.profiles import (
    ModelIdentity,
    ModelRequestRequirements,
    ModelSelection,
    ModelSelectionOption,
)
from grandquiz.providers.retry import RetryRuntime


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
class BudgetedModel:
    """Role-free request budget at the resolved Model seam."""

    inner: Model
    counter: TokenEstimator
    ceiling: int

    @property
    def identity(self) -> ModelIdentity | None:
        return identity_of(self.inner)

    @property
    def retry_runtime(self) -> RetryRuntime | None:
        return retry_runtime_of(self.inner)

    @property
    def fallback_plan(self) -> ModelFallbackPlan | None:
        plan = fallback_plan_of(self.inner)
        return (
            None
            if plan is None
            else plan.map_models(
                lambda model: budget_model(model, counter=self.counter, ceiling=self.ceiling)
            )
        )

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self._ensure_within_budget(messages, tools)
        return await self.inner.complete(messages, tools=tools)

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


@dataclass(frozen=True)
class _BudgetedStreamingModel(BudgetedModel):
    inner: StreamingModel

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        self._ensure_within_budget(messages, tools)
        async for event in self.inner.stream_complete(messages, tools=tools):
            yield event


def budget_model(
    model: Model,
    *,
    counter: TokenEstimator,
    ceiling: int,
) -> Model:
    if isinstance(model, StreamingModel):
        return _BudgetedStreamingModel(inner=model, counter=counter, ceiling=ceiling)
    return BudgetedModel(inner=model, counter=counter, ceiling=ceiling)


@dataclass(frozen=True)
class BudgetedModels:
    """Apply the same local request limit after each purpose is resolved."""

    inner: ModelSource
    counter: TokenEstimator
    ceiling: int

    def for_purpose(self, purpose: str) -> Model:
        return budget_model(
            bind_model(self.inner, purpose),
            counter=self.counter,
            ceiling=self.ceiling,
        )

    def model_identities(self) -> tuple[ModelIdentity, ...]:
        return identities_of(self.inner)

    def select_for_purpose(
        self,
        purpose: str,
        selection: ModelSelection,
        *,
        requirements: ModelRequestRequirements | None = None,
    ) -> Model:
        return budget_model(
            select_model(
                self.inner,
                purpose,
                selection,
                requirements=requirements,
            ),
            counter=self.counter,
            ceiling=self.ceiling,
        )

    def selection_options(self, purpose: str) -> tuple[ModelSelectionOption, ...]:
        return selection_options_of(self.inner, purpose)


def budget_models(
    models: ModelSource,
    *,
    counter: TokenEstimator,
    ceiling: int,
) -> BudgetedModels:
    return BudgetedModels(inner=models, counter=counter, ceiling=ceiling)


@dataclass(frozen=True)
class BudgetedProvider(LegacyPurposeProvider):
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

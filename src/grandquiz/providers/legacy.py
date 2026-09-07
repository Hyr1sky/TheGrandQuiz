"""Explicit adapter for role-based fixtures and v1/v2 cassettes, never a v3 fallback."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from grandquiz.providers.base import (
    Completion,
    Message,
    Model,
    Provider,
    ProviderStreamEvent,
    Role,
    StreamingProvider,
    ToolSpec,
)
from grandquiz.providers.profiles import ModelConfigurationError

_PURPOSE_ROLES: dict[str, Role] = {
    "chat": "basic",
    "question_generation": "enrich",
    "answer_grading": "basic",
    "distractor_review": "basic",
    "material_reading": "basic",
    "grounded_answer": "basic",
    "summarization": "basic",
    "eval_quality": "basic",
}


@dataclass(frozen=True)
class LegacyBoundModel:
    inner: Provider
    role: Role

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return await self.inner.complete(messages, role=self.role, tools=tools)


@dataclass(frozen=True)
class _LegacyBoundStreamingModel(LegacyBoundModel):
    inner: StreamingProvider

    async def stream_complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[ProviderStreamEvent]:
        async for event in self.inner.stream_complete(
            messages,
            role=self.role,
            tools=tools,
        ):
            yield event


class LegacyPurposeProvider:
    """Opt-in mixin for the explicitly legacy Provider implementations and test fixtures."""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        raise NotImplementedError

    def for_purpose(self, purpose: str) -> Model:
        role = _PURPOSE_ROLES.get(purpose)
        if role is None:
            raise ModelConfigurationError("unknown_purpose")
        if isinstance(self, StreamingProvider):
            return _LegacyBoundStreamingModel(self, role)
        return LegacyBoundModel(self, role)

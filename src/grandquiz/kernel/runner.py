"""Agent runner——M1：一个最小、无工具的 turn 循环，发射事件流。

工具 / subagent / ReAct 在后续里程碑加入；按 ADR-0004，核心考核循环将是确定性
workflow 而非自由 ReAct。
"""

from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Provider


class Runner:
    def __init__(
        self,
        provider: Provider,
        emitter: EventEmitter,
        *,
        system_prompt: str | None = None,
    ) -> None:
        self._provider = provider
        self._emitter = emitter
        self._system_prompt = system_prompt
        self._history: list[Message] = []

    def _messages(self) -> list[Message]:
        messages: list[Message] = []
        if self._system_prompt is not None:
            messages.append(Message(role="system", content=self._system_prompt))
        messages.extend(self._history)
        return messages

    async def run_turn(self, user_message: str) -> str:
        turn_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.TURN_STARTED,
            span_id=turn_span,
            payload={"user_message": user_message},
        )
        self._history.append(Message(role="user", content=user_message))

        model_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.MODEL_STARTED,
            span_id=model_span,
            parent_span_id=turn_span,
            payload={"messages": [m.model_dump() for m in self._messages()]},
        )
        try:
            completion: Completion = await self._provider.complete(self._messages(), role="basic")
        except Exception as exc:
            self._emitter.emit(
                EventType.ERROR,
                span_id=model_span,
                parent_span_id=turn_span,
                payload={"error": repr(exc)},
            )
            self._emitter.emit(EventType.TURN_ENDED, span_id=turn_span, payload={"ok": False})
            raise

        self._emitter.emit(
            EventType.MODEL_ENDED,
            span_id=model_span,
            parent_span_id=turn_span,
            payload={"output": completion.text, "usage": completion.usage.model_dump()},
        )
        # 跨轮裁剪（架构约束）：历史只保留每轮最终 assistant 回答——M1 无工具中间步，故平凡。
        self._history.append(Message(role="assistant", content=completion.text))
        self._emitter.emit(EventType.TURN_ENDED, span_id=turn_span, payload={"ok": True})
        return completion.text

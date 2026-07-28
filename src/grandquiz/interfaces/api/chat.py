"""有状态 ReAct session 的进程内 owner；持有单个 Runner 实例并投影 AgentEvent 为 chat UI 事件。"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, field_validator

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.context import learner_context_provider
from grandquiz.domain.learning.ingest.fetch import ALLOW_ANY_DOMAIN
from grandquiz.domain.learning.ingest.web_fetch import create_http_source
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.summarizer import LLMSummarizer
from grandquiz.domain.learning.tools import register_learning_tools
from grandquiz.interfaces.api.navigation_tools import (
    NAVIGATION_REQUESTED,
    register_navigation_tools,
)
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.cli.composition import (
    _HISTORY_MAX_TURNS,
    _MEMORY_PARTITION_BUDGET,
    _SYSTEM_PARTITION_BUDGET,
    _TOTAL_BUDGET,
    budget_provider,
)
from grandquiz.kernel.clock import SystemClock
from grandquiz.kernel.context import (
    BudgetCompressionPolicy,
    ContextBuilder,
    HeuristicTokenCounter,
    Partition,
    SummarizingHistoryCompressor,
)
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import ToolRegistry
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider

ChatSessionStatus = Literal["idle", "running", "closed"]

_REACT_PROMPT_NAME = "react_system"
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_ACTIVE_RESOURCE_PARTITION_BUDGET = 160


class ActiveResourceNotFoundError(LookupError):
    """Web turn 声明了不存在的当前资源。"""


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)
    active_resource_id: str | None = None

    @field_validator("text")
    @classmethod
    def text_is_not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text 不能为空")
        return normalized


class SessionView(BaseModel):
    session_id: str
    trace_id: str


class MessageAccepted(BaseModel):
    turn_id: str


class ChatUiEvent(BaseModel):
    sequence: int = Field(ge=1)
    type: str
    session_id: str
    data: dict[str, object] = Field(default_factory=dict)


def _empty_chat_events() -> list[ChatUiEvent]:
    return []


@dataclass
class _ActiveResourceContext:
    """每轮动态读取的受信 Web workspace context；只暴露已验证的 exact id。"""

    resource_id: str | None = None

    def __call__(self) -> str:
        if self.resource_id is None:
            return ""
        return (
            "【Web 工作区上下文】\n"
            f"active_resource_id={self.resource_id}\n"
            "当用户说“当前材料”“本文”时，只能指向这个 exact resource_id；"
            "不得扩大到其他资源。"
        )


@dataclass
class _ChatSession:
    session_id: str
    trace_id: str
    runner: Runner
    emitter: EventEmitter
    sink: EventSink
    active_resource_context: _ActiveResourceContext
    status: ChatSessionStatus = "idle"
    events: list[ChatUiEvent] = field(default_factory=_empty_chat_events)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    current_task: asyncio.Task[None] | None = None
    current_turn_id: str | None = None


class ChatManager:
    """持有单个 Runner 实例并管理 ReAct session 生命周期。"""

    def __init__(
        self,
        *,
        persistence: LearningPersistence,
        provider: Provider,
        trace_store: TraceStore,
        trace_observatory: TraceObservatory | None = None,
    ) -> None:
        self._persistence = persistence
        self._provider = provider
        self._trace_store = trace_store
        self._trace_observatory = trace_observatory
        self._session: _ChatSession | None = None

    def create_session(self) -> SessionView:
        if self._session is not None:
            self._destroy_session_sync()

        session_id = uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        if self._trace_observatory is not None:
            self._trace_observatory.register_trace(trace_id)

        sink = EventSink()
        sink.register_durable(self._trace_store)
        if self._trace_observatory is not None:
            sink.register(self._trace_observatory)
        emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)

        provider = budget_provider(self._provider)
        registry = ToolRegistry()
        source = create_http_source()
        register_learning_tools(
            registry,
            source=source,
            provider=provider,
            store=self._persistence.store,
            approval=ScriptedApprovalGate(keep=lambda _item: True),
            memory=self._persistence.memory,
            max_bytes=_DEFAULT_MAX_BYTES,
            allowed_domains=ALLOW_ANY_DOMAIN,
            preferences=self._persistence.preferences,
            asked_questions=self._persistence.asked_questions,
            difficulty=self._persistence.difficulty,
        )
        register_navigation_tools(registry)

        prompt = load_prompt(_REACT_PROMPT_NAME)
        counter = HeuristicTokenCounter()
        active_resource_context = _ActiveResourceContext()
        context_builder = ContextBuilder(
            [
                Partition(name="system", provider=prompt.text, budget=_SYSTEM_PARTITION_BUDGET),
                Partition(
                    name="active_resource",
                    provider=active_resource_context,
                    budget=_ACTIVE_RESOURCE_PARTITION_BUDGET,
                ),
                Partition(
                    name="memory",
                    provider=learner_context_provider(
                        store=self._persistence.store,
                        memory=self._persistence.memory,
                        preferences=self._persistence.preferences,
                    ),
                    budget=_MEMORY_PARTITION_BUDGET,
                ),
            ],
            policy=BudgetCompressionPolicy(counter),
            counter=counter,
            total_budget=_TOTAL_BUDGET,
            history_compressor=SummarizingHistoryCompressor(
                LLMSummarizer(provider, emitter), max_turns=_HISTORY_MAX_TURNS
            ),
        )

        runner = Runner(
            provider=provider,
            emitter=emitter,
            prompt_version=prompt.version,
            tools=registry,
            context_builder=context_builder,
        )

        session = _ChatSession(
            session_id=session_id,
            trace_id=trace_id,
            runner=runner,
            emitter=emitter,
            sink=sink,
            active_resource_context=active_resource_context,
        )
        sink.subscribe(lambda event: self._project_event(session, event))
        self._session = session
        return SessionView(session_id=session_id, trace_id=trace_id)

    def get_session(self, session_id: str) -> _ChatSession | None:
        session = self._session
        if session is None or session.session_id != session_id:
            return None
        return session

    def send_message(
        self,
        session_id: str,
        text: str,
        *,
        active_resource_id: str | None = None,
    ) -> MessageAccepted:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        if (
            active_resource_id is not None
            and self._persistence.store.get_resource(active_resource_id) is None
        ):
            raise ActiveResourceNotFoundError(active_resource_id)
        session.active_resource_context.resource_id = active_resource_id
        turn_id = uuid.uuid4().hex
        session.current_turn_id = turn_id
        session.status = "running"
        session.current_task = asyncio.create_task(
            self._run_turn(session, text, turn_id),
            name=f"grandquiz-chat-turn:{session_id}:{turn_id}",
        )
        return MessageAccepted(turn_id=turn_id)

    async def iter_events(self, session_id: str, *, after: int = 0) -> AsyncIterator[ChatUiEvent]:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        cursor = after
        while True:
            fresh = [e for e in session.events if e.sequence > cursor]
            for event in fresh:
                cursor = event.sequence
                yield event
            terminal_types = {e.type for e in session.events if e.sequence == cursor}
            if terminal_types & {"chat.turn_ended", "chat.error"}:
                return
            session.changed.clear()
            if any(e.sequence > cursor for e in session.events):
                continue
            await session.changed.wait()

    async def _run_turn(self, session: _ChatSession, text: str, turn_id: str) -> None:
        try:
            output = await session.runner.run_agent_turn(text)
            self._append_event(
                session,
                "chat.turn_ended",
                {"turn_id": turn_id, "output": output},
            )
        except Exception as exc:
            self._append_event(
                session,
                "chat.error",
                {"turn_id": turn_id, "error": type(exc).__name__},
            )
        finally:
            session.status = "idle"

    @staticmethod
    def _append_event(
        session: _ChatSession,
        event_type: str,
        data: Mapping[str, object] | None = None,
    ) -> None:
        session.events.append(
            ChatUiEvent(
                sequence=len(session.events) + 1,
                type=event_type,
                session_id=session.session_id,
                data={} if data is None else dict(data),
            )
        )
        session.changed.set()

    def _project_event(self, session: _ChatSession, event: AgentEvent) -> None:
        turn_id = session.current_turn_id or ""
        if event.type == EventType.AGENT_TURN_STARTED:
            self._append_event(
                session,
                "chat.turn_started",
                {"turn_id": turn_id},
            )
        elif event.type == EventType.TOOL_CALL_STARTED:
            self._append_event(
                session,
                "chat.tool_call",
                self._tool_call_data(event.payload, turn_id),
            )
        elif event.type == EventType.TOOL_CALL_ENDED:
            result = event.payload.get("result")
            self._append_event(
                session,
                "chat.tool_result",
                {
                    "turn_id": turn_id,
                    "ok": event.payload.get("ok", False),
                    "result": result if isinstance(result, str) else "",
                },
            )
        elif event.type == NAVIGATION_REQUESTED:
            target = event.payload.get("target")
            params = event.payload.get("params")
            self._append_event(
                session,
                "chat.navigation",
                {
                    "turn_id": turn_id,
                    "target": target if isinstance(target, str) else "",
                    "params": dict(cast("dict[str, object]", params))
                    if isinstance(params, dict)
                    else {},
                },
            )

    @staticmethod
    def _tool_call_data(payload: Mapping[str, Any], turn_id: str) -> dict[str, object]:
        name = payload.get("tool_name")
        arguments = payload.get("arguments")
        return {
            "turn_id": turn_id,
            "name": name if isinstance(name, str) else "",
            "arguments": dict(cast("dict[str, object]", arguments))
            if isinstance(arguments, dict)
            else {},
        }

    def _destroy_session_sync(self) -> None:
        session = self._session
        if session is None:
            return
        task = session.current_task
        if task is not None and not task.done():
            task.cancel()
        session.status = "closed"
        self._session = None

    async def aclose(self) -> None:
        session = self._session
        if session is None:
            return
        task = session.current_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await session.runner.aclose()
        session.status = "closed"
        self._session = None

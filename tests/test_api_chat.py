"""ChatManager + ReAct session endpoint 的 TestClient 验证。"""

import asyncio
import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from grandquiz.domain.learning.models import LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.events import EventType
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, ToolCall, ToolSpec, Usage


class _EchoProvider:
    """直接回显用户消息的 fake provider；不触发工具调用。"""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        user_text = ""
        for msg in reversed(messages):
            if msg.role == "user":
                user_text = msg.content
                break
        return Completion(
            text=f"echo: {user_text}",
            usage=Usage(prompt_tokens=50, completion_tokens=10),
        )


class _ToolCallingProvider:
    """第一次调用返回带参导航 tool_call，第二次返回 final 文本。"""

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self._call_count += 1
        if tools is not None and self._call_count == 1:
            return Completion(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tc_1",
                        name="open_article",
                        arguments={"resource_id": "private-resource-id"},
                    )
                ],
                usage=Usage(prompt_tokens=80, completion_tokens=20),
            )
        return Completion(
            text="agent final answer after tool call",
            usage=Usage(prompt_tokens=100, completion_tokens=15),
        )


class _HistoryAwareProvider:
    """回显消息数量和最后一条 user 消息，用于验证多轮上下文承接。"""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        user_messages = [m for m in messages if m.role == "user"]
        user_count = len(user_messages)
        last_user = user_messages[-1].content if user_messages else ""
        assistant_messages = [m for m in messages if m.role == "assistant"]
        assistant_count = len(assistant_messages)
        return Completion(
            text=f"users={user_count} assistants={assistant_count} last={last_user}",
            usage=Usage(prompt_tokens=80, completion_tokens=20),
        )


class _FailingProvider:
    """所有调用都抛异常。"""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        raise RuntimeError("provider boom")


class _ActiveResourceAwareProvider:
    """只通过公开 messages 判断 Web 当前材料是否进入受信 system context。"""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        system_context = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        return Completion(
            text=f"context={system_context} user={user_message}",
            usage=Usage(prompt_tokens=50, completion_tokens=10),
        )


class _BlockingActiveResourceProvider:
    """让第一轮保持 running，以验证并发请求不能覆盖 exact scope。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del role, tools
        system_context = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        active_scope = next(
            (
                line
                for line in system_context.splitlines()
                if line.startswith("active_resource_id=")
            ),
            "",
        )
        self.started.set()
        await asyncio.to_thread(self.release.wait)
        return Completion(
            text=active_scope,
            usage=Usage(prompt_tokens=50, completion_tokens=10),
        )


class _CancellableThenEchoProvider:
    """第一轮等待取消，第二轮正常返回，用于验证取消后的 session 仍可复用。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self._call_count = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        self._call_count += 1
        if self._call_count == 1:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return Completion(
            text="second turn completed",
            usage=Usage(prompt_tokens=50, completion_tokens=10),
        )


class _EchoThenCancellableProvider:
    """第一轮完成、第二轮等待取消，用于锁住 stale turn 的取消边界。"""

    def __init__(self) -> None:
        self.second_started = threading.Event()
        self.second_cancelled = threading.Event()
        self._call_count = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        self._call_count += 1
        if self._call_count == 1:
            return Completion(text="first completed")
        self.second_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.second_cancelled.set()
            raise
        raise AssertionError("unreachable")


def _app(tmp_path: Path, provider: Provider | None = None):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=provider or _EchoProvider(),
    )


def _wait_for_events(
    client: TestClient,
    session_id: str,
    *,
    terminal_type: str = "chat.turn_ended",
    after: int = 0,
    max_polls: int = 80,
) -> list[dict[str, Any]]:
    for _ in range(max_polls):
        response = client.get(
            f"/api/v1/chat/sessions/{session_id}/events",
            params={"after": after},
        )
        assert response.status_code == 200
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        if any(e["type"] in {terminal_type, "chat.error"} for e in events):
            return events
        time.sleep(0.02)
    return []


def test_chat_status_separates_real_usage_from_estimated_context_budget(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        before = client.get(f"/api/v1/chat/sessions/{session['session_id']}/status")
        client.post(
            f"/api/v1/chat/sessions/{session['session_id']}/messages",
            json={"text": "hello"},
        )
        _wait_for_events(client, session["session_id"])
        after = client.get(f"/api/v1/chat/sessions/{session['session_id']}/status")

    assert before.status_code == 200
    assert before.json()["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    assert before.json()["context"]["budget_tokens"] == 20_000
    assert before.json()["context"]["estimation"] == "heuristic"
    assert before.json()["context"]["remaining_tokens"] > 0
    assert after.json()["usage"] == {
        "prompt_tokens": 50,
        "completion_tokens": 10,
        "total_tokens": 60,
    }


def test_create_session_returns_session_id(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.post("/api/v1/chat/sessions")

    assert response.status_code == 201
    payload = response.json()
    assert "session_id" in payload
    assert isinstance(payload["session_id"], str)
    assert len(payload["session_id"]) > 0


def test_create_session_replaces_old_session(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        first = client.post("/api/v1/chat/sessions").json()
        second = client.post("/api/v1/chat/sessions").json()

    assert first["session_id"] != second["session_id"]


def test_send_message_to_unknown_session_returns_404(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/chat/sessions/nonexistent/messages",
            json={"text": "hello"},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "session_not_found"


def test_send_message_returns_202_with_turn_id(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        response = client.post(
            f"/api/v1/chat/sessions/{session['session_id']}/messages",
            json={"text": "hello"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert "turn_id" in payload
    assert isinstance(payload["turn_id"], str)


def test_blank_message_is_rejected(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        response = client.post(
            f"/api/v1/chat/sessions/{session['session_id']}/messages",
            json={"text": "   "},
        )

    assert response.status_code == 422


def test_message_active_resource_becomes_trusted_turn_context(tmp_path: Path) -> None:
    persistence = LearningPersistence(tmp_path / "learning.db")
    resource = LearningResource.create(url="file://local/active.md").model_copy(
        update={"topic": "Active material", "status": "read"}
    )
    persistence.store.add_resource(resource)
    persistence.close()

    with TestClient(_app(tmp_path, _ActiveResourceAwareProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        response = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={
                "text": "请基于当前材料考我",
                "active_resource_id": resource.resource_id,
            },
        )
        events = _wait_for_events(client, sid)

    assert response.status_code == 202
    ended = next(event for event in events if event["type"] == "chat.turn_ended")
    output = str(ended["data"]["output"])
    assert f"active_resource_id={resource.resource_id}" in output
    assert "user=请基于当前材料考我" in output


def test_message_unknown_active_resource_fails_closed(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _ActiveResourceAwareProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        response = client.post(
            f"/api/v1/chat/sessions/{session['session_id']}/messages",
            json={"text": "当前材料是什么", "active_resource_id": "missing"},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"


def test_concurrent_message_is_rejected_without_overwriting_active_resource(
    tmp_path: Path,
) -> None:
    persistence = LearningPersistence(tmp_path / "learning.db")
    first_resource = LearningResource.create(url="file://local/first.md").model_copy(
        update={"topic": "First", "status": "read"}
    )
    second_resource = LearningResource.create(url="file://local/second.md").model_copy(
        update={"topic": "Second", "status": "read"}
    )
    persistence.store.add_resource(first_resource)
    persistence.store.add_resource(second_resource)
    persistence.close()
    provider = _BlockingActiveResourceProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        first = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={
                "text": "first turn",
                "active_resource_id": first_resource.resource_id,
            },
        )
        assert provider.started.wait(timeout=1)
        second = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={
                "text": "second turn",
                "active_resource_id": second_resource.resource_id,
            },
        )
        provider.release.set()
        events = _wait_for_events(client, sid)

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "turn_in_progress"
    assert second.json()["retryable"] is True
    ended = next(event for event in events if event["type"] == "chat.turn_ended")
    output = str(ended["data"]["output"])
    assert f"active_resource_id={first_resource.resource_id}" in output
    assert second_resource.resource_id not in output


def test_cancel_active_turn_is_idempotent_and_session_accepts_next_turn(
    tmp_path: Path,
) -> None:
    provider = _CancellableThenEchoProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        accepted = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "cancel me"},
        ).json()
        turn_id = accepted["turn_id"]
        assert provider.started.wait(timeout=1)

        first_cancel = client.post(f"/api/v1/chat/sessions/{sid}/turns/{turn_id}/cancel")
        assert first_cancel.status_code == 200
        repeated_cancel = client.post(f"/api/v1/chat/sessions/{sid}/turns/{turn_id}/cancel")
        cancelled_events = _wait_for_events(
            client,
            sid,
            terminal_type="chat.turn_cancelled",
        )
        cancelled_snapshot = client.get(
            f"/api/v1/observability/traces/{session['trace_id']}"
        ).json()
        cancelled_cursor = max(event["sequence"] for event in cancelled_events)

        next_message = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "continue"},
        )
        next_events = _wait_for_events(client, sid, after=cancelled_cursor)

    assert first_cancel.json() == {"turn_id": turn_id, "status": "cancelled"}
    assert repeated_cancel.status_code == 200
    assert repeated_cancel.json() == {"turn_id": turn_id, "status": "cancelled"}
    assert provider.cancelled.wait(timeout=1)
    assert any(event["type"] == "chat.turn_cancelled" for event in cancelled_events)
    assert not any(event["type"] == "chat.turn_ended" for event in cancelled_events)
    assert cancelled_snapshot["status"] == "cancelled"
    assert next_message.status_code == 202
    assert any(
        event["type"] == "chat.turn_ended" and event["data"]["output"] == "second turn completed"
        for event in next_events
    )
    trace_store = TraceStore(tmp_path / "trace.db")
    trace_events = trace_store.events(session["trace_id"])
    trace_store.close()
    cancelled_ends = [
        event
        for event in trace_events
        if event.type in {EventType.MODEL_ENDED, EventType.AGENT_TURN_ENDED}
        and event.payload.get("cancelled") is True
    ]
    assert {event.type for event in cancelled_ends} == {
        EventType.MODEL_ENDED,
        EventType.AGENT_TURN_ENDED,
    }


def test_cancelling_stale_turn_does_not_cancel_newer_turn(
    tmp_path: Path,
) -> None:
    provider = _EchoThenCancellableProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        first_turn = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "first"},
        ).json()["turn_id"]
        first_events = _wait_for_events(client, sid)
        cursor = max(event["sequence"] for event in first_events)

        second_turn = client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "second"},
        ).json()["turn_id"]
        assert provider.second_started.wait(timeout=1)
        stale_cancel = client.post(f"/api/v1/chat/sessions/{sid}/turns/{first_turn}/cancel")
        current_cancel = client.post(f"/api/v1/chat/sessions/{sid}/turns/{second_turn}/cancel")
        second_events = _wait_for_events(
            client,
            sid,
            terminal_type="chat.turn_cancelled",
            after=cursor,
        )

    assert stale_cancel.status_code == 404
    assert stale_cancel.json()["code"] == "turn_not_found"
    assert current_cancel.status_code == 200
    assert provider.second_cancelled.wait(timeout=1)
    assert any(
        event["type"] == "chat.turn_cancelled" and event["data"]["turn_id"] == second_turn
        for event in second_events
    )


def test_sse_delivers_turn_started_and_ended_with_output(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _EchoProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "hello agent"})
        events = _wait_for_events(client, sid)

    types = [e["type"] for e in events]
    assert "chat.turn_started" in types
    assert "chat.message_delta" in types
    assert "chat.turn_ended" in types
    assert (
        "".join(
            str(event["data"]["text"]) for event in events if event["type"] == "chat.message_delta"
        )
        == "echo: hello agent"
    )
    ended = next(e for e in events if e["type"] == "chat.turn_ended")
    assert "echo: hello agent" in str(ended["data"]["output"])


def test_sse_events_have_monotonic_sequences(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _EchoProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "test"})
        events = _wait_for_events(client, sid)

    sequences = [e["sequence"] for e in events]
    assert sequences == list(range(1, len(sequences) + 1))


def test_sse_resume_from_known_sequence(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _EchoProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "test"})
        all_events = _wait_for_events(client, sid)
        assert len(all_events) >= 2
        resumed = _wait_for_events(client, sid, after=1)

    assert all(e["sequence"] > 1 for e in resumed)
    assert len(resumed) == len(all_events) - 1


def test_tool_call_projects_as_chat_tool_call_event(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _ToolCallingProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "open the article"},
        )
        events = _wait_for_events(client, sid)

    types = [e["type"] for e in events]
    assert "chat.tool_call" in types
    tool_event = next(e for e in events if e["type"] == "chat.tool_call")
    assert tool_event["data"]["name"] == "open_article"
    assert "arguments" not in tool_event["data"]
    assert "chat.tool_result" in types
    assert "chat.turn_ended" in types
    ended = next(e for e in events if e["type"] == "chat.turn_ended")
    assert ended["data"]["output"] == "agent final answer after tool call"


def test_multi_turn_context_carries_history(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _HistoryAwareProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "first"})
        events_1 = _wait_for_events(client, sid)
        ended_1 = next(e for e in events_1 if e["type"] == "chat.turn_ended")
        assert "users=1" in str(ended_1["data"]["output"])
        assert "assistants=0" in str(ended_1["data"]["output"])

        after_1 = max(e["sequence"] for e in events_1)
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "second"})
        events_2 = _wait_for_events(client, sid, after=after_1)
        ended_2 = next(e for e in events_2 if e["type"] == "chat.turn_ended")

    assert "users=2" in str(ended_2["data"]["output"])
    assert "assistants=1" in str(ended_2["data"]["output"])
    assert "last=second" in str(ended_2["data"]["output"])


def test_provider_failure_projects_as_chat_error(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _FailingProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "boom"})
        events = _wait_for_events(client, sid, terminal_type="chat.error")

    types = [e["type"] for e in events]
    assert "chat.error" in types
    error_event = next(e for e in events if e["type"] == "chat.error")
    assert error_event["data"]["error"] == "turn_failed"


def test_events_stream_for_unknown_session_returns_404(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/v1/chat/sessions/nonexistent/events")

    assert response.status_code == 404
    assert response.json()["code"] == "session_not_found"


def test_sse_content_type_is_event_stream(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _EchoProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "hello"})
        _wait_for_events(client, sid)
        response = client.get(f"/api/v1/chat/sessions/{sid}/events")

    assert response.headers["content-type"].startswith("text/event-stream")


def test_old_session_invalidated_after_new_creation(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        first = client.post("/api/v1/chat/sessions").json()
        client.post("/api/v1/chat/sessions")
        response = client.post(
            f"/api/v1/chat/sessions/{first['session_id']}/messages",
            json={"text": "hello"},
        )

    assert response.status_code == 404

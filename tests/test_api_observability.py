"""Local Web 的安全 trace projection REST/SSE 验证。"""

import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.kernel.events import AgentEvent
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Role, ToolSpec, Usage


class _EchoProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return Completion(
            text="safe echo",
            usage=Usage(prompt_tokens=12, completion_tokens=3),
        )


def _app(tmp_path: Path):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=_EchoProvider(),
    )


def test_chat_session_exposes_an_idle_observability_snapshot(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        response = client.get(f"/api/v1/observability/traces/{session['trace_id']}")

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["summary"] == {
        "trace_id": session["trace_id"],
        "status": "idle",
        "event_count": 0,
        "model_calls": 0,
        "tool_calls": 0,
        "error_count": 0,
        "recovery_count": 0,
        "total_tokens": 0,
        "started_at": None,
        "updated_at": None,
        "latency_ms": None,
    }
    assert snapshot["spans"] == []
    assert snapshot["events"] == []


def test_completed_turn_snapshot_contains_only_safe_runtime_metrics(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "secret user text"},
        )
        for _ in range(80):
            stream = client.get(f"/api/v1/chat/sessions/{sid}/events")
            chat_events = [
                json.loads(line.removeprefix("data: "))
                for line in stream.text.splitlines()
                if line.startswith("data: ")
            ]
            if any(event["type"] == "chat.turn_ended" for event in chat_events):
                break
            time.sleep(0.02)
        response = client.get(f"/api/v1/observability/traces/{session['trace_id']}")

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["summary"]["status"] == "completed"
    assert snapshot["summary"]["event_count"] == 4
    assert snapshot["summary"]["model_calls"] == 1
    assert snapshot["summary"]["tool_calls"] == 0
    assert snapshot["summary"]["error_count"] == 0
    assert snapshot["summary"]["total_tokens"] == 15
    assert [span["type"] for span in snapshot["spans"]] == ["agent_turn", "model"]
    assert [event["type"] for event in snapshot["events"]] == [
        "agent_turn.started",
        "model.started",
        "model.ended",
        "agent_turn.ended",
    ]
    serialized = response.text
    for forbidden in (
        "secret user text",
        "safe echo",
        "messages",
        "arguments",
        "output",
        "prompt_version",
    ):
        assert forbidden not in serialized


def test_observability_sse_resumes_after_known_sequence(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"text": "hello"})
        for _ in range(80):
            stream = client.get(f"/api/v1/chat/sessions/{sid}/events")
            if "event: chat.turn_ended" in stream.text:
                break
            time.sleep(0.02)
        response = client.get(
            f"/api/v1/observability/traces/{session['trace_id']}/events",
            params={"after": 2, "follow": "false"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    projected = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["sequence"] for event in projected] == [3, 4]
    assert [event["type"] for event in projected] == [
        "model.ended",
        "agent_turn.ended",
    ]
    assert "safe echo" not in response.text


async def test_observatory_live_iterator_wakes_on_agent_event(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "trace.db")
    observatory = TraceObservatory(store)
    trace_id = "live-trace"
    observatory.register_trace(trace_id)
    pending = asyncio.ensure_future(anext(observatory.iter_events(trace_id, after=0, follow=True)))
    await asyncio.sleep(0)
    event = AgentEvent(
        type="model.started",
        seq=0,
        ts=1.0,
        trace_id=trace_id,
        span_id="live-trace:s0",
        payload={"messages": [{"role": "user", "content": "secret"}]},
    )
    store.record(event)
    observatory.on_event(event)

    projected = await asyncio.wait_for(pending, timeout=1)
    store.close()

    assert projected.sequence == 1
    assert projected.type == "model.started"
    assert "secret" not in projected.model_dump_json()

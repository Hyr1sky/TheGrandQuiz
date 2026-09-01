"""Local Web 的安全 trace projection REST/SSE 验证。"""

import asyncio
import json
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
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
    assert snapshot["schema_version"] == 1
    assert snapshot["trace_id"] == session["trace_id"]
    assert snapshot["status"] == "idle"
    assert snapshot["started_at"] is None
    assert snapshot["ended_at"] is None
    assert snapshot["workflow_kind"] is None
    assert snapshot["summary"] == {
        "model_calls": 0,
        "retries": 0,
        "rejection_counts": [],
        "error_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "latency_ms": None,
        "headline": None,
        "recommended_action": None,
    }
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
    assert snapshot["status"] == "completed"
    assert snapshot["workflow_kind"] is None
    assert snapshot["summary"]["model_calls"] == 1
    assert snapshot["summary"]["error_count"] == 0
    assert snapshot["summary"]["prompt_tokens"] == 12
    assert snapshot["summary"]["completion_tokens"] == 3
    assert [event["operation"] for event in snapshot["events"]] == ["other"] * 6
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
        detail = client.get(f"/api/v1/observability/traces/{session['trace_id']}").json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    projected = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["sequence"] for event in projected] == [3, 4, 5, 6]
    assert projected == detail["events"][2:]
    assert [event["operation"] for event in projected] == ["other"] * 4
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
    assert projected.operation == "other"
    assert projected.phase == "started"
    assert "secret" not in projected.model_dump_json()


def test_observatory_recovers_historical_trace_after_store_restart(tmp_path: Path) -> None:
    trace_id = "historical-trace"
    path = tmp_path / "trace.db"
    first_store = TraceStore(path)
    first_store.record(
        AgentEvent(
            type="agent_turn.started",
            seq=0,
            ts=1.0,
            trace_id=trace_id,
            span_id=f"{trace_id}:s0",
            payload={},
        )
    )
    first_store.record(
        AgentEvent(
            type="agent_turn.ended",
            seq=1,
            ts=2.0,
            trace_id=trace_id,
            span_id=f"{trace_id}:s0",
            payload={"ok": True},
        )
    )
    first_store.close()

    restarted_store = TraceStore(path)
    restarted_observatory = TraceObservatory(restarted_store)
    try:
        assert restarted_observatory.exists(trace_id)
        snapshot = restarted_observatory.snapshot(trace_id)
    finally:
        restarted_store.close()

    assert snapshot.status == "completed"
    assert [event.operation for event in snapshot.events] == ["other", "other"]


def test_observability_detail_rebuilds_safe_semantics_after_store_restart(
    tmp_path: Path,
) -> None:
    trace_id = "historical-safe-trace"
    path = tmp_path / "trace.db"
    store = TraceStore(path)
    store.record(
        AgentEvent(
            type="learning.multiple_choice_generation.attempt_rejected",
            seq=0,
            ts=1.0,
            trace_id=trace_id,
            parent_span_id="generation",
            payload={
                "attempt": 2,
                "stage": "repair",
                "reason_code": "distractor_quality_unmet",
                "prompt": "SECRET-HISTORICAL-PROMPT",
            },
        )
    )
    store.record(
        AgentEvent(
            type="web.assessment_run.degraded",
            seq=1,
            ts=2.0,
            trace_id=trace_id,
            span_id="assessment",
            payload={
                "status": "degraded",
                "stage": "question_generation",
                "reason_code": "question_generation_exhausted",
                "answer": "SECRET-HISTORICAL-ANSWER",
            },
        )
    )
    store.close()

    with TestClient(_app(tmp_path)) as client:
        response = client.get(f"/api/v1/observability/traces/{trace_id}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["schema_version"] == 1
    assert detail["trace_id"] == trace_id
    assert detail["status"] == "waiting_input"
    assert detail["summary"]["retries"] == 1
    assert detail["summary"]["rejection_counts"] == [
        {"reason_code": "distractor_quality_unmet", "count": 1}
    ]
    assert [event["operation"] for event in detail["events"]] == [
        "multiple_choice_generation",
        "assessment_run",
    ]
    assert "SECRET-HISTORICAL-PROMPT" not in response.text
    assert "SECRET-HISTORICAL-ANSWER" not in response.text


def test_observability_lists_recent_runs_and_filters_status_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trace.db"
    store = TraceStore(path)
    fixtures: tuple[tuple[str, float, str, dict[str, object]], ...] = (
        ("trace-completed", 1.0, "assessment.ended", {"ok": True}),
        ("trace-waiting", 2.0, "learning.question_asked", {}),
        ("trace-failed", 3.0, "assessment.ended", {"ok": False}),
        ("trace-cancelled", 4.0, "assessment.ended", {"status": "cancelled"}),
        ("trace-running", 5.0, "assessment.started", {"status": "running"}),
    )
    for trace_id, timestamp, event_type, payload in fixtures:
        store.record(
            AgentEvent(
                type=event_type,
                seq=0,
                ts=timestamp,
                trace_id=trace_id,
                span_id=f"{trace_id}:assessment",
                payload=payload,
            )
        )
    store.close()

    with TestClient(_app(tmp_path)) as client:
        recent = client.get("/api/v1/observability/traces", params={"limit": 2})
        failed = client.get(
            "/api/v1/observability/traces",
            params={"status": "failed", "limit": 10},
        )
        invalid_status = client.get(
            "/api/v1/observability/traces",
            params={"status": "SECRET-INTERNAL-STATUS"},
        )
        excessive_limit = client.get(
            "/api/v1/observability/traces",
            params={"limit": 51},
        )

    assert recent.status_code == 200
    assert [run["trace_id"] for run in recent.json()] == [
        "trace-running",
        "trace-cancelled",
    ]
    assert [run["trace_id"] for run in failed.json()] == ["trace-failed"]
    assert failed.json()[0]["status"] == "failed"
    assert invalid_status.status_code == 422
    assert excessive_limit.status_code == 422

    with TestClient(_app(tmp_path)) as client:
        for status in ("running", "waiting_input", "completed", "failed", "cancelled"):
            response = client.get(
                "/api/v1/observability/traces",
                params={"status": status, "limit": 10},
            )
            assert response.status_code == 200
            assert [run["status"] for run in response.json()] == [status]


def test_observatory_status_filter_scans_history_in_bounded_pages() -> None:
    store = TraceStore(":memory:")
    store.record(
        AgentEvent(
            type="assessment.ended",
            seq=0,
            ts=1.0,
            trace_id="older-failed",
            payload={"ok": False},
        )
    )
    for index in range(55):
        store.record(
            AgentEvent(
                type="assessment.started",
                seq=0,
                ts=float(index + 2),
                trace_id=f"recent-running-{index:02d}",
                payload={"status": "running"},
            )
        )
    observatory = TraceObservatory(store)
    try:
        assert [run.trace_id for run in observatory.list_runs(status="failed", limit=1)] == [
            "older-failed"
        ]
        with pytest.raises(ValueError, match="limit"):
            observatory.list_runs(status="failed", limit=0)
    finally:
        store.close()


def test_observability_openapi_exposes_only_finite_semantic_event_fields(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        schema = client.get("/openapi.json").json()

    event_schema = schema["components"]["schemas"]["SafeTraceEventV1"]
    properties = event_schema["properties"]
    assert properties["operation"]["enum"] == [
        "assessment_run",
        "multiple_choice_generation",
        "distractor_judgement",
        "grading",
        "learning_commit",
        "other",
    ]
    assert properties["phase"]["enum"] == [
        "started",
        "attempt_rejected",
        "ended",
        "waiting_input",
        "event",
    ]
    assert properties["status"]["enum"] == [
        "running",
        "waiting_input",
        "completed",
        "failed",
        "event",
    ]
    assert properties["stage"]["anyOf"][0]["enum"] == [
        "question_generation",
        "generation",
        "repair",
        "model_call",
        "validation",
        "repair_validation",
        "distractor_quality",
        "grading",
        "learning_commit",
        "workflow",
        "other",
    ]
    assert properties["reason_code"]["anyOf"][0]["enum"] == [
        "invalid_json",
        "schema_invalid",
        "option_count_invalid",
        "answer_index_invalid",
        "duplicate_options",
        "meta_option",
        "length_outlier",
        "evidence_missing",
        "ghost_evidence",
        "question_repeated",
        "option_count_unmet",
        "distractor_quality_unmet",
        "repair_contract_violated",
        "question_generation_exhausted",
        "grading_exhausted",
        "workflow_degraded",
        "other",
    ]
    assert properties["quality_label"]["anyOf"][0]["enum"] == [
        "invalid",
        "weak",
        "reasonable",
    ]
    assert set(properties) == {
        "sequence",
        "timestamp",
        "span_id",
        "parent_span_id",
        "operation",
        "phase",
        "status",
        "attempt",
        "stage",
        "reason_code",
        "quality_label",
        "tokens",
        "latency_ms",
    }

"""Local Web 的首条 HTTP tracer bullet：资源 → 文档 → grounded question。"""

import asyncio
import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from grandquiz import __version__
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api import serve
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.events import EventType
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, ToolSpec, Usage


class _UnusedProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        raise AssertionError("health check 不应调用 provider")


class _GroundedProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        assert role == "basic"
        assert tools is None
        assert "事件是系统的数据脊柱" in messages[-1].content
        return Completion(
            text=(
                '{"answer":"事件承载系统各投影。",'
                '"citations":[{"node_key":"n0","quote":"事件是系统的数据脊柱"}]}'
            ),
            usage=Usage(prompt_tokens=100, completion_tokens=20),
        )


class _BlockingProvider:
    def __init__(self) -> None:
        self.started = Event()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.started.set()
        await asyncio.sleep(60)
        raise AssertionError("cancelled provider 不应自然返回")


class _NoEvidenceProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        return Completion(
            text='{"answer":"材料证据不足。","citations":[]}',
            usage=Usage(prompt_tokens=80, completion_tokens=10),
        )


class _FailingProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        raise RuntimeError("provider secret detail")


def _app(tmp_path: Path, provider: Provider | None = None):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=provider or _UnusedProvider(),
    )


def _seed_document(tmp_path: Path) -> LearningResource:
    content = "# Runtime\n\n## Events\n\n事件是系统的数据脊柱。\n"
    resource = LearningResource.create(url="file://local/runtime.md").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Agent Runtime",
            "trusted": True,
        }
    )
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        persistence.store.replace_snapshot(resource, [])
        stored = persistence.store.get_resource(resource.resource_id)
        assert stored is not None
        return stored


def test_health_exposes_versioned_api_without_touching_provider(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_version": "v1"}


def test_resources_list_projects_metadata_without_raw_document(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/v1/resources")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "items": [
            {
                "resource_id": resource.resource_id,
                "url": "file://local/runtime.md",
                "topic": "Agent Runtime",
                "status": "read",
                "trusted": True,
                "current_revision_id": payload["items"][0]["current_revision_id"],
            }
        ]
    }
    assert payload["items"][0]["current_revision_id"]
    assert "raw_content" not in payload["items"][0]
    assert "content_hash" not in payload["items"][0]


def test_resource_detail_uses_the_same_safe_projection(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.get(f"/api/v1/resources/{resource.resource_id}")

    assert response.status_code == 200
    assert response.json() == {
        "resource_id": resource.resource_id,
        "url": resource.url,
        "topic": resource.topic,
        "status": "read",
        "trusted": True,
        "current_revision_id": resource.current_revision_id,
    }
    assert "raw_content" not in response.json()


def test_missing_resource_uses_stable_error_envelope(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/v1/resources/missing")

    assert response.status_code == 404
    assert response.json() == {
        "code": "resource_not_found",
        "message": "资源不存在：missing",
        "retryable": False,
        "trace_id": None,
    }


def test_document_outline_projects_navigation_without_body(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.get(f"/api/v1/resources/{resource.resource_id}/outline")

    assert response.status_code == 200
    payload = response.json()
    assert [node["section_path"] for node in payload["nodes"]] == [
        "Runtime",
        "Runtime > Events",
    ]
    assert [node["title"] for node in payload["nodes"]] == ["Runtime", "Events"]
    assert all(node["kind"] == "section" for node in payload["nodes"])
    assert [node["start_offset"] for node in payload["nodes"]] == [0, 11]
    assert all(node["end_offset"] > node["start_offset"] for node in payload["nodes"])
    assert all("content" not in node for node in payload["nodes"])


def test_node_read_is_explicitly_bounded_and_marked_untrusted(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        outline = client.get(f"/api/v1/resources/{resource.resource_id}/outline").json()
        events_node = outline["nodes"][1]
        response = client.get(
            f"/api/v1/resources/{resource.resource_id}/nodes/{events_node['node_id']}",
            params={"max_chars": 12},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["resource_id"] == resource.resource_id
    assert payload["node_id"] == events_node["node_id"]
    assert payload["section_path"] == "Runtime > Events"
    assert payload["content"].startswith("## Events")
    assert len(payload["content"]) == 12
    assert payload["has_more"] is True
    assert payload["untrusted"] is True


def test_document_read_returns_one_exact_continuous_markdown_source(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.get(f"/api/v1/resources/{resource.resource_id}/document")

    assert response.status_code == 200
    assert response.json() == {
        "resource_id": resource.resource_id,
        "revision_id": resource.current_revision_id,
        "content": "# Runtime\n\n## Events\n\n事件是系统的数据脊柱。\n",
        "untrusted": False,
    }


def test_question_starts_an_identified_background_run(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path, _GroundedProvider())) as client:
        response = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["run_id"]
    assert payload["trace_id"]
    assert payload["run_id"] != payload["trace_id"]


def test_run_status_returns_grounded_answer_with_exact_citation(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path, _GroundedProvider())) as client:
        started = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        ).json()
        response = client.get(f"/api/v1/runs/{started['run_id']}")
        for _ in range(49):
            if response.json()["status"] == "succeeded":
                break
            time.sleep(0.01)
            response = client.get(f"/api/v1/runs/{started['run_id']}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["result"]["status"] == "answered"
    assert payload["result"]["answer"] == "事件承载系统各投影。"
    assert payload["result"]["citations"][0]["quote"] == "事件是系统的数据脊柱"
    assert payload["result"]["citations"][0]["section_path"] == "Runtime > Events"


def test_sse_projects_safe_ordered_ui_events_instead_of_raw_agent_payloads(
    tmp_path: Path,
) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path, _GroundedProvider())) as client:
        started = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        ).json()
        for _ in range(50):
            if client.get(f"/api/v1/runs/{started['run_id']}").json()["status"] == "succeeded":
                break
            time.sleep(0.01)
        response = client.get(f"/api/v1/runs/{started['run_id']}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["type"] for event in events] == [
        "run.queued",
        "run.started",
        "search.completed",
        "node.read",
        "citation.resolved",
        "answer.completed",
        "run.succeeded",
    ]
    assert "事件 数据脊柱" not in response.text
    assert '"messages"' not in response.text
    assert '"output"' not in response.text
    assert "事件是系统的数据脊柱" not in response.text


def test_sse_can_resume_from_a_known_ui_sequence(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path, _GroundedProvider())) as client:
        started = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        ).json()
        for _ in range(50):
            if client.get(f"/api/v1/runs/{started['run_id']}").json()["status"] == "succeeded":
                break
            time.sleep(0.01)
        response = client.get(
            f"/api/v1/runs/{started['run_id']}/events",
            params={"after": 4},
        )

    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["sequence"] for event in events] == [5, 6, 7]
    assert [event["type"] for event in events] == [
        "citation.resolved",
        "answer.completed",
        "run.succeeded",
    ]


def test_running_question_can_be_cancelled_idempotently(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)
    provider = _BlockingProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        started = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        ).json()
        assert provider.started.wait(timeout=1)

        first = client.post(f"/api/v1/runs/{started['run_id']}/cancel")
        second = client.post(f"/api/v1/runs/{started['run_id']}/cancel")
        events_response = client.get(f"/api/v1/runs/{started['run_id']}/events")

    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"
    assert "event: run.cancelled" in events_response.text


def test_request_validation_uses_the_common_error_envelope(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "  "},
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "message": "请求参数无效",
        "retryable": False,
        "trace_id": None,
    }


def test_no_evidence_is_a_successful_fail_safe_domain_result(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path, _NoEvidenceProvider())) as client:
        started = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        ).json()
        response = client.get(f"/api/v1/runs/{started['run_id']}")
        for _ in range(49):
            if response.json()["status"] == "succeeded":
                break
            time.sleep(0.01)
            response = client.get(f"/api/v1/runs/{started['run_id']}")

    assert response.json()["status"] == "succeeded"
    assert response.json()["result"]["status"] == "no_evidence"
    assert response.json()["result"]["citations"] == []


def test_provider_failure_is_redacted_but_traceable(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path, _FailingProvider())) as client:
        started = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        ).json()
        response = client.get(f"/api/v1/runs/{started['run_id']}")
        for _ in range(49):
            if response.json()["status"] == "failed":
                break
            time.sleep(0.01)
            response = client.get(f"/api/v1/runs/{started['run_id']}")

    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error"] == {
        "code": "run_failed",
        "message": "运行失败，请通过 trace_id 查看详情",
        "retryable": True,
        "trace_id": started["trace_id"],
    }
    assert "provider secret detail" not in response.text


def test_grounded_run_persists_full_internal_trace(tmp_path: Path) -> None:
    resource = _seed_document(tmp_path)

    with TestClient(_app(tmp_path, _GroundedProvider())) as client:
        started = client.post(
            f"/api/v1/resources/{resource.resource_id}/questions",
            json={"query": "事件 数据脊柱"},
        ).json()
        for _ in range(50):
            if client.get(f"/api/v1/runs/{started['run_id']}").json()["status"] == "succeeded":
                break
            time.sleep(0.01)

    trace = TraceStore(tmp_path / "trace.db")
    try:
        events = trace.events(started["trace_id"])
    finally:
        trace.close()
    assert [event.type for event in events] == [
        "interface.api_run.queued",
        "interface.api_run.started",
        LearningEvent.GROUNDED_ANSWER_STARTED,
        LearningEvent.DOCUMENT_NODES_SEARCHED,
        LearningEvent.DOCUMENT_NODE_READ,
        EventType.MODEL_STARTED,
        EventType.MODEL_ENDED,
        LearningEvent.CITATION_RESOLVED,
        LearningEvent.GROUNDED_ANSWER_ENDED,
        "interface.api_run.ended",
    ]
    assert events[5].payload["messages"]
    span_tree = TraceStore(tmp_path / "trace.db")
    try:
        roots = span_tree.span_tree(started["trace_id"])
    finally:
        span_tree.close()
    assert roots[0].type == "interface.api_run"
    assert roots[0].children[0].type == "learning.grounded_answer"


def test_openapi_exposes_the_versioned_article_workspace_contract(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        schema = client.get("/openapi.json").json()

    assert schema["info"]["version"] == __version__
    paths = schema["paths"]
    assert {
        "/api/v1/health",
        "/api/v1/resources",
        "/api/v1/resources/{resource_id}",
        "/api/v1/resources/{resource_id}/outline",
        "/api/v1/resources/{resource_id}/nodes/{node_id}",
        "/api/v1/resources/{resource_id}/questions",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/events",
        "/api/v1/runs/{run_id}/cancel",
    } <= set(paths)
    event_stream = paths["/api/v1/runs/{run_id}/events"]["get"]["responses"]["200"]["content"]
    assert event_stream["text/event-stream"]["schema"] == {"$ref": "#/components/schemas/UiEvent"}


def test_web_entrypoint_binds_loopback_and_uses_the_app_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        called["app"] = app
        called.update(kwargs)

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)

    serve.main()

    assert called == {
        "app": "grandquiz.interfaces.api.serve:create_default_app",
        "factory": True,
        "host": "127.0.0.1",
        "port": 8000,
    }

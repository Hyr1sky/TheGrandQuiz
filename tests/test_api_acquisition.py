"""Web Acquisition HTTP seam：上传、恢复审批、原子入库与稳定错误。"""

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Never, cast

import pytest
from fastapi.testclient import TestClient

from grandquiz.domain.learning.acquisition import AcquisitionLedger
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api import acquisitions as acquisition_mod
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, ToolSpec, Usage


class _ReaderProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        payload = json.loads(messages[-1].content)
        node = next(
            candidate
            for candidate in payload["untrusted_document_nodes"]
            if "事件是系统脊柱" in candidate["content"]
        )
        quote = "事件是系统脊柱"
        start = node["content"].index(quote)
        return Completion(
            text=json.dumps(
                {
                    "topic": "Agent Runtime",
                    "candidates": [
                        {
                            "concept": "事件脊柱",
                            "summary": "事件统一承载 trace、SSE 与回放。",
                            "confidence": 0.93,
                            "evidence": [
                                {
                                    "node_key": node["node_key"],
                                    "start_offset": start,
                                    "end_offset": start + len(quote),
                                    "quote": quote,
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=20, completion_tokens=10),
        )


class _BlockingReaderProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        await asyncio.sleep(60)
        raise AssertionError("取消后不应自然返回")


class _FailingReaderProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        raise RuntimeError("provider secret")


def _app(tmp_path: Path, provider: Provider | None = None):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=provider or _ReaderProvider(),
    )


def _wait_for_status(client: TestClient, run_id: str, status: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/acquisitions/{run_id}").json()
        if payload["status"] == status:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"acquisition 未进入 {status}")


def test_upload_can_resume_approval_after_service_restart(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/api/v1/acquisitions",
            json={
                "kind": "upload",
                "filename": "runtime.md",
                "content": "# Runtime\n\n事件是系统脊柱。",
            },
        )
        assert created.status_code == 201
        creation = created.json()
        pending = _wait_for_status(client, creation["run_id"], "needs_input")
        candidates = cast("list[dict[str, object]]", pending["candidates"])
        assert candidates[0]["concept"] == "事件脊柱"
        assert candidates[0]["evidence"] == ["事件是系统脊柱"]
        item_id = str(candidates[0]["item_id"])

        with LearningPersistence(tmp_path / "learning.db") as persistence:
            assert persistence.store.all_resources() == []

    with TestClient(_app(tmp_path)) as restarted:
        recovered = restarted.get(f"/api/v1/acquisitions/{creation['run_id']}").json()
        assert recovered["status"] == "needs_input"

        approved = restarted.post(
            f"/api/v1/acquisitions/{creation['run_id']}/approval",
            json={
                "resume_token": creation["resume_token"],
                "approved_item_ids": [item_id],
            },
        )

        assert approved.status_code == 200
        assert approved.json()["status"] == "succeeded"
        resource_id = approved.json()["resource_id"]
        resources = restarted.get("/api/v1/resources").json()["items"]
        assert [resource["resource_id"] for resource in resources] == [resource_id]
        observability = restarted.get(f"/api/v1/observability/traces/{creation['trace_id']}").json()
        assert observability["summary"]["status"] == "completed"

    trace = TraceStore(tmp_path / "trace.db")
    events = trace.events(creation["trace_id"])
    trace.close()
    assert [event.seq for event in events] == list(range(len(events)))
    assert len({event.span_id for event in events if event.span_id is not None}) == 3


def test_upload_rejects_unsupported_file_without_creating_run(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/acquisitions",
            json={"kind": "upload", "filename": "notes.pdf", "content": "binary"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "unsupported_upload_type"


def test_running_upload_can_be_cancelled_without_polluting_store(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _BlockingReaderProvider())) as client:
        creation = client.post(
            "/api/v1/acquisitions",
            json={
                "kind": "upload",
                "filename": "runtime.md",
                "content": "# Runtime\n\n事件是系统脊柱。",
            },
        ).json()
        _wait_for_status(client, creation["run_id"], "running")

        cancelled = client.post(
            f"/api/v1/acquisitions/{creation['run_id']}/cancel",
            json={"resume_token": creation["resume_token"]},
        )

        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        observability = client.get(f"/api/v1/observability/traces/{creation['trace_id']}").json()
        assert observability["summary"]["status"] == "cancelled"
        with LearningPersistence(tmp_path / "learning.db") as persistence:
            assert persistence.store.all_resources() == []

    trace = TraceStore(tmp_path / "trace.db")
    roots = trace.span_tree(creation["trace_id"])
    trace.close()
    assert len(roots) == 1
    assert roots[0].type == "ingest"
    assert roots[0].end_ts is not None
    assert roots[0].output == {"ok": False, "reason": "cancelled"}


def test_processing_error_is_sanitized_and_leaves_zero_pollution(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, _FailingReaderProvider())) as client:
        creation = client.post(
            "/api/v1/acquisitions",
            json={
                "kind": "upload",
                "filename": "runtime.md",
                "content": "# Runtime\n\n事件是系统脊柱。",
            },
        ).json()
        failed = _wait_for_status(client, creation["run_id"], "failed")
        observability = client.get(f"/api/v1/observability/traces/{creation['trace_id']}").json()

    assert failed["error_code"] == "processing_failed"
    assert observability["summary"]["status"] == "failed"
    error_message = failed["error_message"]
    assert isinstance(error_message, str)
    assert "provider secret" not in error_message
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        assert persistence.store.all_resources() == []


def test_url_import_rejects_non_http_locator(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/acquisitions",
            json={"kind": "url", "url": "file:///etc/passwd"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_url"


def test_url_import_can_complete_with_injected_offline_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline_source(_url: str) -> str:
        return "# Runtime\n\n事件是系统脊柱。"

    def source_factory() -> Callable[[str], str]:
        return offline_source

    monkeypatch.setattr(acquisition_mod, "create_http_source", source_factory)
    with TestClient(_app(tmp_path)) as client:
        creation = client.post(
            "/api/v1/acquisitions",
            json={"kind": "url", "url": "https://example.com/runtime"},
        ).json()
        pending = _wait_for_status(client, creation["run_id"], "needs_input")
        candidates = cast("list[dict[str, object]]", pending["candidates"])

        approved = client.post(
            f"/api/v1/acquisitions/{creation['run_id']}/approval",
            json={
                "resume_token": creation["resume_token"],
                "approved_item_ids": [str(candidates[0]["item_id"])],
            },
        )

        assert approved.status_code == 200
        assert approved.json()["status"] == "succeeded"


def test_commit_failure_rolls_back_without_success_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        creation = client.post(
            "/api/v1/acquisitions",
            json={
                "kind": "upload",
                "filename": "runtime.md",
                "content": "# Runtime\n\n事件是系统脊柱。",
            },
        ).json()
        pending = _wait_for_status(client, creation["run_id"], "needs_input")
        candidates = cast("list[dict[str, object]]", pending["candidates"])

        def fail_mark_succeeded(
            _ledger: AcquisitionLedger,
            run_id: str,
            *,
            resource_id: str,
            now: float,
        ) -> Never:
            del run_id, resource_id, now
            raise RuntimeError("injected ledger failure")

        monkeypatch.setattr(AcquisitionLedger, "mark_succeeded", fail_mark_succeeded)
        response = client.post(
            f"/api/v1/acquisitions/{creation['run_id']}/approval",
            json={
                "resume_token": creation["resume_token"],
                "approved_item_ids": [str(candidates[0]["item_id"])],
            },
        )

        assert response.status_code == 500
        assert response.json()["code"] == "acquisition_commit_failed"
        assert (
            client.get(f"/api/v1/acquisitions/{creation['run_id']}").json()["status"]
            == "needs_input"
        )

    with LearningPersistence(tmp_path / "learning.db") as persistence:
        assert persistence.store.all_resources() == []
        assert persistence.acquisitions.require(creation["run_id"]).token_used_at is None
    trace = TraceStore(tmp_path / "trace.db")
    event_types = [event.type for event in trace.events(creation["trace_id"])]
    trace.close()
    assert "approval.decided" not in event_types
    assert "learning.revision_committed" not in event_types
    assert "learning.resource_approved" not in event_types
    assert "learning.item_created" not in event_types
    assert "ingest.ended" not in event_types
    assert "acquisition.succeeded" not in event_types


def test_cancelling_terminal_run_does_not_emit_conflicting_terminal_event(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        creation = client.post(
            "/api/v1/acquisitions",
            json={
                "kind": "upload",
                "filename": "runtime.md",
                "content": "# Runtime\n\n事件是系统脊柱。",
            },
        ).json()
        pending = _wait_for_status(client, creation["run_id"], "needs_input")
        candidates = cast("list[dict[str, object]]", pending["candidates"])
        approved = client.post(
            f"/api/v1/acquisitions/{creation['run_id']}/approval",
            json={
                "resume_token": creation["resume_token"],
                "approved_item_ids": [str(candidates[0]["item_id"])],
            },
        )
        assert approved.json()["status"] == "succeeded"

        cancelled = client.post(
            f"/api/v1/acquisitions/{creation['run_id']}/cancel",
            json={"resume_token": creation["resume_token"]},
        )

        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "succeeded"

    trace = TraceStore(tmp_path / "trace.db")
    event_types = [event.type for event in trace.events(creation["trace_id"])]
    trace.close()
    assert "acquisition.cancelled" not in event_types

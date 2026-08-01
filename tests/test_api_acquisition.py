"""Web Acquisition HTTP seam：上传、恢复审批、原子入库与稳定错误。"""

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Never, cast

import pytest
from fastapi.testclient import TestClient

from grandquiz.domain.learning.acquisition import AcquisitionLedger
from grandquiz.domain.learning.ingest.fetch import FetchResult, FetchSource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api import acquisitions as acquisition_mod
from grandquiz.interfaces.api.acquisitions import AcquisitionManager
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import EventEmitter, EventSink
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
        payload = json.loads(
            next(message.content for message in messages if message.role == "user")
        )
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


class _InvalidEvidenceReaderProvider:
    """返回结构合法但无法定位的 Evidence，复现被泛化的领域失败。"""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        payload = json.loads(
            next(message.content for message in messages if message.role == "user")
        )
        node = payload["untrusted_document_nodes"][0]
        quote = "原文中不存在的证据"
        return Completion(
            text=json.dumps(
                {
                    "topic": "Agent Runtime",
                    "candidates": [
                        {
                            "concept": "无效证据",
                            "summary": "模型返回了无法定位的引用。",
                            "confidence": 0.8,
                            "evidence": [
                                {
                                    "node_key": node["node_key"],
                                    "start_offset": 0,
                                    "end_offset": len(quote),
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


class _StaticHttpSource:
    async def fetch(self, url: str, *, max_bytes: int) -> FetchResult:
        content = "# Runtime\n\n事件是系统脊柱。"
        return FetchResult(
            requested_url=url,
            final_url=url,
            content=content,
            content_type="text/markdown",
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
        )


def _app(
    tmp_path: Path,
    provider: Provider | None = None,
    http_source: FetchSource | None = None,
):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=provider or _ReaderProvider(),
        acquisition_http_source=http_source,
    )


def _wait_for_status(client: TestClient, run_id: str, status: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        payload = client.get(f"/api/v1/acquisitions/{run_id}").json()
        if payload["status"] == status:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"acquisition 未进入 {status}; last={payload}")


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

    with LearningPersistence(tmp_path / "learning.db") as persistence:
        history = persistence.classifications.history_for_item(item_id)
        assert len(history) == 1
        assert history[0].classified_by == "rule"
        assert history[0].review_status == "proposed"
        assert persistence.classifications.active_for_item(item_id) is None

    trace = TraceStore(tmp_path / "trace.db")
    events = trace.events(creation["trace_id"])
    trace.close()
    assert [event.seq for event in events] == list(range(len(events)))
    assert len({event.span_id for event in events if event.span_id is not None}) == 3


def test_reserved_url_activation_is_recovered_after_restart(tmp_path: Path) -> None:
    persistence = LearningPersistence(tmp_path / "learning.db")
    trace_store = TraceStore(tmp_path / "trace.db")
    manager = AcquisitionManager(
        persistence=persistence,
        provider=_ReaderProvider(),
        trace_store=trace_store,
        http_source=_StaticHttpSource(),
    )
    reserved = manager.reserve_url(
        url="https://example.com/reserved",
        control_token="r" * 32,
    )
    trace_store.close()
    persistence.close()

    with TestClient(_app(tmp_path, http_source=_StaticHttpSource())) as restarted:
        recovered = _wait_for_status(restarted, reserved.run_id, "needs_input")
        runs = restarted.get("/api/v1/acquisitions").json()["items"]

    assert recovered["run_id"] == reserved.run_id
    assert len([run for run in runs if run["run_id"] == reserved.run_id]) == 1


async def test_activation_failure_is_compensated_without_waiting_for_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persistence = LearningPersistence(tmp_path / "learning.db")
    trace_store = TraceStore(tmp_path / "trace.db")
    manager = AcquisitionManager(
        persistence=persistence,
        provider=_ReaderProvider(),
        trace_store=trace_store,
        http_source=_StaticHttpSource(),
    )
    reserved = manager.reserve_url(
        url="https://example.com/activation-failure",
        control_token="f" * 32,
    )

    def fail_trace_read(_trace_id: str) -> Never:
        raise RuntimeError("injected trace failure")

    monkeypatch.setattr(trace_store, "events", fail_trace_read)
    with pytest.raises(RuntimeError, match="injected trace failure"):
        manager.activate_reserved(reserved.run_id)

    failed = persistence.acquisitions.require(reserved.run_id)
    assert failed.status == "failed"
    assert failed.error_code == "processing_failed"
    assert failed.error_stage == "runtime"
    assert not persistence.acquisitions.activation_required(reserved.run_id)
    trace_store.close()
    persistence.close()


def test_interrupted_run_uses_complete_safe_failure_projection(tmp_path: Path) -> None:
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        persistence.acquisitions.create(
            run_id="run-interrupted",
            trace_id="trace-interrupted",
            kind="url",
            locator="https://example.com/runtime",
            display_name="example.com/runtime",
            request_payload={},
            token_hash="token-hash",
            token_expires_at=200.0,
            now=100.0,
        )

    with TestClient(_app(tmp_path)) as client:
        recovered = client.get("/api/v1/acquisitions/run-interrupted").json()
        stream = client.get("/api/v1/acquisitions/run-interrupted/events")

    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "interrupted"
    assert recovered["error_stage"] == "runtime"
    assert recovered["error_message"] == "服务在材料处理期间重启，请重试"
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    ]
    failed = next(event for event in events if event["type"] == "acquisition.failed")
    assert failed["data"] == {
        "code": "interrupted",
        "stage": "runtime",
        "reason": "服务在材料处理期间重启，请重试",
    }


def test_legacy_failure_event_cannot_leak_internal_values_to_sse(tmp_path: Path) -> None:
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        persistence.acquisitions.create(
            run_id="run-legacy-failure",
            trace_id="trace-legacy-failure",
            kind="url",
            locator="https://example.com/runtime",
            display_name="example.com/runtime",
            request_payload={},
            token_hash="token-hash",
            token_expires_at=200.0,
            now=100.0,
        )
        persistence.transaction_owner.connection.execute(
            """
            UPDATE acquisition_runs
            SET status = 'failed', error_code = 'acquisition_failed',
                error_message = '旧版安全文案'
            WHERE run_id = 'run-legacy-failure'
            """
        )
        persistence.transaction_owner.connection.commit()
    trace = TraceStore(tmp_path / "trace.db")
    sink = EventSink()
    sink.register_durable(trace)
    EventEmitter(
        sink,
        ManualClock(),
        trace_id="trace-legacy-failure",
    ).emit(
        "acquisition.failed",
        payload={
            "run_id": "run-legacy-failure",
            "code": "internal_sql_error",
            "stage": "database_password",
            "reason": "secret diagnostic",
            "status": "failed",
        },
    )
    trace.close()

    with TestClient(_app(tmp_path)) as client:
        stream = client.get("/api/v1/acquisitions/run-legacy-failure/events")

    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    ]
    failed = next(event for event in events if event["type"] == "acquisition.failed")
    assert failed["data"] == {
        "code": "ingest_failed",
        "stage": "reader",
        "reason": "材料读取或深读失败，请检查内容后重试",
    }
    assert "secret diagnostic" not in stream.text
    assert "database_password" not in stream.text


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


def test_domain_ingest_failure_keeps_safe_code_stage_reason_and_counts_error(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path, _InvalidEvidenceReaderProvider())) as client:
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

    assert failed["error_code"] == "quote_mismatch"
    assert failed["error_stage"] == "evidence_validation"
    assert failed["error_message"] == "Evidence 引文无法精确定位到原文节点"
    assert observability["summary"]["status"] == "failed"
    assert observability["summary"]["error_count"] == 1
    assert "原文中不存在的证据" not in json.dumps(observability, ensure_ascii=False)
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

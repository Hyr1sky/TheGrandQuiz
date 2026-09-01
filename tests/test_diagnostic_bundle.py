"""脱敏诊断包只组合公开 trace projector 与安全配置 identity。"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from fastapi.testclient import TestClient

from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.interfaces.api.diagnostics import DiagnosticBundleExporter
from grandquiz.interfaces.api.observability import TraceObservatory
from grandquiz.interfaces.api.settings import ProviderSettingView
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Role, ToolSpec

_SENTINELS = (
    "PROMPT_SENTINEL_DO_NOT_EXPORT",
    "ANSWER_SENTINEL_DO_NOT_EXPORT",
    "EVIDENCE_SENTINEL_DO_NOT_EXPORT",
    "KEY_SENTINEL_DO_NOT_EXPORT",
)


class _Provider:
    model_for_role: ClassVar[dict[str, str]] = {
        "basic": "safe-basic",
        "enrich": "safe-enrich",
    }

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        raise AssertionError("diagnostic export must not call the provider")


def _raw_event(trace_id: str) -> AgentEvent:
    return AgentEvent(
        type="learning.multiple_choice_generation.attempt_rejected",
        seq=0,
        ts=3.0,
        trace_id=trace_id,
        span_id=f"{trace_id}:s0",
        payload={
            "attempt": 2,
            "stage": "validation",
            "reason_code": "invalid_json",
            "prompt": _SENTINELS[0],
            "answer": _SENTINELS[1],
            "evidence": _SENTINELS[2],
            "api_key": _SENTINELS[3],
        },
    )


def _providers() -> list[ProviderSettingView]:
    return [
        ProviderSettingView(
            role="basic",
            configured=True,
            model="safe-basic",
            endpoint_host="api.example.test",
            required_env_vars=["LLM_API_KEY"],
        )
    ]


def test_bundle_is_allowlisted_and_repeatable_except_for_manifest_time(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "trace.db")
    trace_id = "trace-diagnostic-golden"
    store.record(_raw_event(trace_id))
    exporter = DiagnosticBundleExporter(
        observatory=TraceObservatory(store),
        provider_views=_providers,
        clock=ManualClock(start=100.0, tick=1.0),
        application_version="test-version",
    )

    first = exporter.export(trace_id).model_dump(mode="json")
    second = exporter.export(trace_id).model_dump(mode="json")
    store.close()

    assert first["schema_version"] == "diagnostic_bundle.v1"
    assert first["trace_id"] == trace_id
    assert first["config_identity"] == {
        "application_version": "test-version",
        "settings_schema_version": "settings.v1",
        "providers": [
            {
                "role": "basic",
                "configured": True,
                "model": "safe-basic",
                "endpoint_host": "api.example.test",
            }
        ],
    }
    assert first["summary"]["retries"] == 1
    assert first["events"][0]["reason_code"] == "invalid_json"
    assert first["manifest"] == {"created_at": 100.0}
    assert second["manifest"] == {"created_at": 101.0}
    first["manifest"].pop("created_at")
    second["manifest"].pop("created_at")
    first_bytes = json.dumps(
        first,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    second_bytes = json.dumps(
        second,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert first_bytes == second_bytes
    serialized = first_bytes.decode()
    for sentinel in _SENTINELS:
        assert sentinel not in serialized
    assert "required_env_vars" not in serialized


def _app(tmp_path: Path):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=_Provider(),
        clock=ManualClock(start=200.0, tick=1.0),
    )


def test_diagnostic_route_downloads_exact_safe_trace_as_json(tmp_path: Path) -> None:
    trace_id = "route-golden-trace"
    store = TraceStore(tmp_path / "trace.db")
    store.record(_raw_event(trace_id))
    store.close()
    with TestClient(_app(tmp_path)) as client:
        response = client.get(f"/api/v1/observability/traces/{trace_id}/diagnostic-bundle")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == (
        'attachment; filename="grandquiz-trace-diagnostic.json"'
    )
    assert response.json()["trace_id"] == trace_id
    for sentinel in _SENTINELS:
        assert sentinel not in response.text


def test_diagnostic_route_rejects_unknown_trace(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/v1/observability/traces/missing/diagnostic-bundle")

    assert response.status_code == 404
    assert response.json()["code"] == "trace_not_found"

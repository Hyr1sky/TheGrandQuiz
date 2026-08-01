"""FastAPI contract for v0.4 discovery and authorized local Eval promotion."""

from collections.abc import Sequence
from pathlib import Path

from fastapi.testclient import TestClient

from grandquiz.domain.learning.assessment.question import ExpectedPoint, QuestionSpec
from grandquiz.domain.learning.ingest.web_search import SearchResult
from grandquiz.evals.grading_calibration import GradingCalibrationSample
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.clock import ManualClock
from grandquiz.providers.base import Completion, Message, Role, ToolSpec, Usage


class _Provider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        return Completion(text="{}", usage=Usage())


class _SearchProvider:
    adapter_name = "scripted"

    async def search(
        self,
        query: str,
        *,
        limit: int,
        domains: tuple[str, ...] = (),
    ) -> list[SearchResult]:
        del query, limit, domains
        return [
            SearchResult(
                title="Agent memory guide",
                url="https://example.com/agent-memory",
                snippet="A sufficiently detailed preview of an Agent memory article.",
                adapter="scripted",
                rank=1,
            )
        ]


def _app(tmp_path: Path):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=_Provider(),
        search_provider=_SearchProvider(),
        clock=ManualClock(start=10.0),
    )


def _blind() -> GradingCalibrationSample:
    return GradingCalibrationSample(
        sample_id="blind-api-1",
        annotator="owner",
        blind_to_model_output=True,
        question=QuestionSpec(
            question="默认连接行为是什么？",
            expected_points=[
                ExpectedPoint(
                    point_id="close",
                    description="响应后关闭",
                    cited_evidence="默认在响应后关闭连接。",
                )
            ],
            reference_answer="响应后关闭。",
            cited_evidence=["默认在响应后关闭连接。"],
        ),
        learner_answer="响应后关闭。",
        human_verdict="对",
        human_matched_points=["close"],
        human_missing_points=[],
    )


def test_discovery_review_starts_one_existing_acquisition(tmp_path: Path) -> None:
    token = "d" * 32
    app = _app(tmp_path)
    with TestClient(app) as client:
        discovered = client.post(
            "/api/v1/discoveries",
            json={"topic": "Agent memory", "source_policy": {"limit": 3}},
        )
        assert discovered.status_code == 201
        candidate = discovered.json()["candidates"][0]

        payload = {
            "request_id": "review-material-1",
            "decision": "approved",
            "reason": "relevant",
            "control_token": token,
        }
        approved = client.post(
            f"/api/v1/discoveries/candidates/{candidate['candidate_id']}/review",
            json=payload,
        )
        replayed = client.post(
            f"/api/v1/discoveries/candidates/{candidate['candidate_id']}/review",
            json=payload,
        )

        assert approved.status_code == 200
        assert replayed.status_code == 200
        assert approved.json()["acquisition"]["run_id"] == replayed.json()["acquisition"]["run_id"]
        assert approved.json()["acquisition"]["resume_token"] == token
        assert client.get("/api/v1/resources").json()["items"] == []
        assert len(client.get("/api/v1/acquisitions").json()["items"]) == 1
        trace = client.get(f"/api/v1/observability/traces/{discovered.json()['trace_id']}")
        assert trace.status_code == 200
        assert candidate["url"] not in trace.text

        invalid = client.post(
            f"/api/v1/discoveries/candidates/{candidate['candidate_id']}/review",
            json={"request_id": "   ", "decision": "rejected"},
        )
        assert invalid.status_code == 422

        wrong_token = client.post(
            f"/api/v1/discoveries/candidates/{candidate['candidate_id']}/review",
            json={**payload, "control_token": "x" * 32},
        )
        assert wrong_token.status_code == 409


def test_discovery_reject_has_no_side_effect_and_conflicting_replay_fails(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        candidate = client.post(
            "/api/v1/discoveries",
            json={"topic": "Agent memory"},
        ).json()["candidates"][0]
        payload = {
            "request_id": "reject-material-1",
            "decision": "rejected",
            "reason": "not useful",
        }
        rejected = client.post(
            f"/api/v1/discoveries/candidates/{candidate['candidate_id']}/review",
            json=payload,
        )
        replayed = client.post(
            f"/api/v1/discoveries/candidates/{candidate['candidate_id']}/review",
            json=payload,
        )
        conflict = client.post(
            f"/api/v1/discoveries/candidates/{candidate['candidate_id']}/review",
            json={
                "request_id": "reject-material-1",
                "decision": "approved",
                "control_token": "a" * 32,
            },
        )

        assert rejected.json() == replayed.json()
        assert rejected.json()["acquisition"] is None
        assert conflict.status_code == 409
        assert client.get("/api/v1/acquisitions").json()["items"] == []
        assert client.get("/api/v1/resources").json()["items"] == []


def test_missing_search_provider_returns_stable_error_and_failed_history(
    tmp_path: Path,
) -> None:
    app = create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=_Provider(),
        clock=ManualClock(start=10.0),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/discoveries", json={"topic": "Agent memory"})
        history = client.get("/api/v1/discoveries").json()["items"]

        assert response.status_code == 503
        assert response.json()["code"] == "search_provider_unavailable"
        assert len(history) == 1
        assert history[0]["status"] == "failed"
        assert history[0]["error_code"] == "provider_unavailable"
        assert history[0]["candidates"] == []


def test_blind_eval_candidate_requires_review_before_snapshot(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        imported = client.post(
            "/api/v1/eval/candidates/blind-import",
            json={
                "request_id": "import-blind-api-1",
                "samples": [_blind().model_dump(mode="json")],
            },
        )
        assert imported.status_code == 201
        candidate = imported.json()["items"][0]

        blocked = client.post(
            "/api/v1/eval/snapshots",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )
        assert blocked.status_code == 409

        reviewed = client.post(
            f"/api/v1/eval/candidates/{candidate['candidate_id']}/review",
            json={
                "request_id": "review-eval-1",
                "decision": "approved",
                "reason": "local privacy review complete",
            },
        )
        snapshot = client.post(
            "/api/v1/eval/snapshots",
            json={"candidate_ids": [candidate["candidate_id"]]},
        )

        assert reviewed.status_code == 200
        assert snapshot.status_code == 201
        assert snapshot.json()["eligible_blind_count"] == 1
        restored = client.get(f"/api/v1/eval/snapshots/{snapshot.json()['snapshot_id']}")
        assert restored.json() == snapshot.json()

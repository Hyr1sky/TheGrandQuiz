"""Local Web 逐题考核：确定性 workflow 的可暂停 HTTP 投影。"""

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, ToolSpec, Usage

_QUOTE = "潜在记忆以隐式形式承载在模型内部表示中。"
_CORRECT = "模型内部表示"
_WRONG = "浏览器缓存"


class _AssessmentProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self.calls += 1
        assert role == "enrich"
        assert tools is None
        assert _QUOTE in messages[-1].content
        return Completion(
            text=json.dumps(
                {
                    "question": "潜在记忆主要承载在哪里？",
                    "options": [_CORRECT, _WRONG],
                    "answer_index": 0,
                    "cited_evidence": [_QUOTE],
                },
                ensure_ascii=False,
            ),
            usage=Usage(prompt_tokens=100, completion_tokens=30),
        )


class _OpenAssessmentProvider:
    def __init__(self) -> None:
        self.question_calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        assert tools is None
        if role == "enrich":
            self.question_calls += 1
            question = (
                "请解释潜在记忆的存储位置。"
                if self.question_calls == 1
                else "潜在记忆为什么不等于外部文件？"
            )
            payload = {"question": question, "cited_evidence": [_QUOTE]}
        else:
            payload = {
                "verdict": "错",
                "reason": "回答没有指出模型内部表示。",
                "cited_evidence": [_QUOTE],
            }
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=100, completion_tokens=30),
        )


class _FailingAssessmentProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        raise RuntimeError("assessment provider failed")


class _BlockingAssessmentProvider:
    def __init__(self) -> None:
        self.started = threading.Event()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        del messages, role, tools
        self.started.set()
        await asyncio.Event().wait()
        return Completion(text="{}", usage=Usage())


def _app(tmp_path: Path, provider: Provider | None = None):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=provider or _AssessmentProvider(),
    )


def _seed_item(tmp_path: Path) -> tuple[LearningResource, KnowledgeItem]:
    content = f"# Agent 记忆\n\n## 记忆存储形式\n\n{_QUOTE}\n"
    resource = LearningResource.create(url="file://local/agent-memory.md").model_copy(
        update={
            "raw_content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "status": "read",
            "topic": "Agent 记忆",
            "trusted": True,
        }
    )
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="潜在记忆",
        summary="潜在记忆存在于模型内部表示。",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.96,
    )
    snapshot = build_document_snapshot(resource)
    assert snapshot is not None
    item = ground_items(snapshot, [item])[0]
    with LearningPersistence(tmp_path / "learning.db") as persistence:
        persistence.store.replace_snapshot(resource, [item])
        stored = persistence.store.get_resource(resource.resource_id)
        assert stored is not None
        return stored, persistence.store.items_for_resource(resource.resource_id)[0]


def _wait_for_status(
    client: TestClient,
    session_id: str,
    expected: str,
) -> dict[str, Any]:
    payload = client.get(f"/api/v1/assessments/{session_id}").json()
    for _ in range(50):
        if payload["status"] == expected:
            return payload
        time.sleep(0.01)
        payload = client.get(f"/api/v1/assessments/{session_id}").json()
    raise AssertionError(f"考核未进入 {expected}：{payload}")


def test_selected_resource_starts_one_real_question_and_waits_for_answer(
    tmp_path: Path,
) -> None:
    resource, item = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "选择题",
            },
        )
        assert response.status_code == 202
        started = response.json()
        payload = _wait_for_status(client, started["session_id"], "awaiting_answer")

    assert payload["round_index"] == 1
    assert payload["rounds"] == 1
    assert payload["question"] == {
        "question_id": payload["question"]["question_id"],
        "item_id": item.item_id,
        "text": "潜在记忆主要承载在哪里？",
        "question_type": "选择题",
        "options": [_CORRECT, _WRONG],
        "evidence_revealed": False,
        "evidence": [],
    }
    assert payload["judgement"] is None
    assert payload["trace_id"]


def test_empty_selected_scope_is_refused_without_calling_the_model(tmp_path: Path) -> None:
    provider = _AssessmentProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": ["missing-resource"],
                "rounds": 1,
                "question_type": "选择题",
            },
        ).json()
        refused = _wait_for_status(client, started["session_id"], "refused")

    assert refused["error"] == "当前选择的材料中没有可用于考核的知识点。"
    assert refused["question"] is None
    assert provider.calls == 0


def test_evidence_reveal_is_explicit_idempotent_and_audited(tmp_path: Path) -> None:
    resource, _ = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "选择题",
            },
        ).json()
        waiting = _wait_for_status(client, started["session_id"], "awaiting_answer")
        question_id = waiting["question"]["question_id"]

        first = client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/evidence/reveal",
            json={"interaction": "click"},
        )
        second = client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/evidence/reveal",
            json={"interaction": "hover"},
        )

    assert first.status_code == 200
    assert first.json()["question"]["evidence_revealed"] is True
    assert first.json()["question"]["evidence"] == [_QUOTE]
    assert second.json()["question"]["evidence"] == [_QUOTE]

    trace = TraceStore(tmp_path / "trace.db")
    try:
        events = [
            event
            for event in trace.events(started["trace_id"])
            if event.type == LearningEvent.EVIDENCE_REVEALED
        ]
    finally:
        trace.close()
    assert len(events) == 1
    assert events[0].payload == {
        "question_id": question_id,
        "item_id": waiting["question"]["item_id"],
        "interaction": "click",
    }


def test_answer_submission_is_idempotent_and_records_one_judgement(tmp_path: Path) -> None:
    resource, _ = _seed_item(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "选择题",
            },
        ).json()
        waiting = _wait_for_status(client, started["session_id"], "awaiting_answer")
        question_id = waiting["question"]["question_id"]
        command = {"request_id": "answer-command-1", "answer": _CORRECT}

        first = client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/answers",
            json=command,
        )
        second = client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/answers",
            json=command,
        )
        completed = _wait_for_status(client, started["session_id"], "completed")
        trace_snapshot = client.get(f"/api/v1/observability/traces/{started['trace_id']}").json()

    assert first.status_code == 202
    assert second.status_code == 202
    assert completed["judgement"] == {
        "verdict": "对",
        "reason": "",
        "concept_state": None,
        "correct_answer": None,
    }
    assert trace_snapshot["summary"]["status"] == "completed"

    trace = TraceStore(tmp_path / "trace.db")
    try:
        judged = [
            event
            for event in trace.events(started["trace_id"])
            if event.type == LearningEvent.ANSWER_JUDGED
        ]
    finally:
        trace.close()
    assert len(judged) == 1


def test_assessment_failure_projects_a_failed_terminal_trace(tmp_path: Path) -> None:
    resource, _ = _seed_item(tmp_path)

    with TestClient(_app(tmp_path, _FailingAssessmentProvider())) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "选择题",
            },
        ).json()
        failed = _wait_for_status(client, started["session_id"], "failed")
        trace_snapshot = client.get(f"/api/v1/observability/traces/{started['trace_id']}").json()

    assert failed["error"] == "本轮考核失败，请通过 trace_id 查看详情"
    assert trace_snapshot["summary"]["status"] == "failed"
    assert trace_snapshot["summary"]["error_count"] == 1


def test_shutdown_projects_a_cancelled_terminal_trace(tmp_path: Path) -> None:
    resource, _ = _seed_item(tmp_path)
    provider = _BlockingAssessmentProvider()
    started: dict[str, Any]

    with TestClient(_app(tmp_path, provider)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "选择题",
            },
        ).json()
        assert provider.started.wait(timeout=1)

    trace = TraceStore(tmp_path / "trace.db")
    try:
        terminal = [
            event
            for event in trace.events(started["trace_id"])
            if event.type == "web.assessment_run.ended"
        ]
    finally:
        trace.close()

    assert len(terminal) == 1
    assert terminal[0].payload["status"] == "cancelled"


def test_open_question_wrong_answer_waits_for_explicit_next_round(tmp_path: Path) -> None:
    resource, _ = _seed_item(tmp_path)
    provider = _OpenAssessmentProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 2,
                "question_type": "简答题",
            },
        ).json()
        first = _wait_for_status(client, started["session_id"], "awaiting_answer")
        assert first["question"]["options"] == []
        first_question_id = first["question"]["question_id"]

        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{first_question_id}/answers",
            json={"request_id": "answer-open-1", "answer": "它放在文件里。"},
        )
        judged = _wait_for_status(client, started["session_id"], "judged")

        assert judged["round_index"] == 1
        assert judged["judgement"]["verdict"] == "错"
        assert judged["judgement"]["reason"] == "回答没有指出模型内部表示。"
        assert judged["judgement"]["concept_state"] == "薄弱"
        assert _QUOTE in judged["judgement"]["correct_answer"]

        next_response = client.post(
            f"/api/v1/assessments/{started['session_id']}/next",
            json={"request_id": "next-command-1"},
        )
        assert next_response.status_code == 202
        second = _wait_for_status(client, started["session_id"], "awaiting_answer")

    assert second["round_index"] == 2
    assert second["question"]["question_id"] != first_question_id
    assert second["question"]["text"] == "潜在记忆为什么不等于外部文件？"
    assert provider.question_calls == 2

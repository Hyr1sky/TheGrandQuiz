"""Local Web 逐题考核：确定性 workflow 的可暂停 HTTP 投影。"""

import asyncio
import hashlib
import json
import re
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from grandquiz.domain.learning.citations import ground_items
from grandquiz.domain.learning.document import build_document_snapshot
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import Evidence, KnowledgeItem, LearningResource
from grandquiz.domain.learning.persistence import LearningPersistence
from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.interfaces.api.assessment_runs import project_assessment_diagnosis
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Completion, Message, Provider, Role, ToolSpec, Usage

_QUOTE = "潜在记忆以隐式形式承载在模型内部表示中。"
_CORRECT = "模型内部表示"
_WRONG = "浏览器缓存"


def test_assessment_diagnosis_projection_has_a_finite_public_vocabulary() -> None:
    assert project_assessment_diagnosis("missing_key_point") == "missing_key_point"
    assert project_assessment_diagnosis("incorrect_choice") == "incorrect_choice"
    assert project_assessment_diagnosis("future.internal.diagnosis") is None
    assert project_assessment_diagnosis({"unexpected": "shape"}) is None


def _open_question_payload(question: str) -> dict[str, object]:
    return {
        "question": question,
        "expected_points": [
            {
                "point_id": "location",
                "description": "指出潜在记忆位于模型内部表示",
                "required_claims": ["指出潜在记忆位于模型内部表示"],
                "cited_evidence": _QUOTE,
            }
        ],
        "reference_answer": "潜在记忆以隐式形式承载在模型内部表示中。",
        "cited_evidence": [_QUOTE],
    }


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
    def __init__(self, *, accept_appeal: bool = False) -> None:
        self.question_calls = 0
        self.grading_calls = 0
        self.accept_appeal = accept_appeal

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        assert tools is None
        payload: dict[str, Any]
        if role == "enrich":
            self.question_calls += 1
            question = (
                "请解释潜在记忆的存储位置。"
                if self.question_calls == 1
                else "潜在记忆为什么不等于外部文件？"
            )
            payload = _open_question_payload(question)
        else:
            self.grading_calls += 1
            accepted = self.accept_appeal and self.grading_calls >= 2
            answer_unit_ids = re.findall(r"\[(v1e\d+_\d+)\]", messages[-1].content)
            payload = {
                "verdict": "对" if accepted else "错",
                "point_assessments": [
                    {
                        "point_id": "location",
                        "label": "matched" if accepted else "missing",
                        "answer_evidence_ids": [],
                        "claim_assessments": [
                            {
                                "claim_id": "location.claim_1",
                                "label": "matched" if accepted else "missing",
                                "answer_evidence_ids": ([answer_unit_ids[-1]] if accepted else []),
                                "reason": (
                                    "补充说明明确指出模型内部表示。"
                                    if accepted
                                    else "没有说明模型内部表示。"
                                ),
                            }
                        ],
                        "reason": (
                            "补充说明明确指出模型内部表示。"
                            if accepted
                            else "没有说明模型内部表示。"
                        ),
                    }
                ],
                "diagnosis": "complete" if accepted else "wrong_focus",
                "reason": (
                    "结合补充说明，回答已经覆盖位置要点。"
                    if accepted
                    else "回答没有指出模型内部表示。"
                ),
                "cited_evidence": [_QUOTE],
            }
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=100, completion_tokens=30),
        )


class _BlockingAppealProvider(_OpenAssessmentProvider):
    def __init__(self) -> None:
        super().__init__(accept_appeal=True)
        self.appeal_started = threading.Event()
        self.appeal_cancelled = threading.Event()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        if role == "basic" and self.grading_calls == 1:
            self.grading_calls += 1
            self.appeal_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.appeal_cancelled.set()
                raise
            raise AssertionError("阻塞的申诉 Provider 不应自然返回")
        return await super().complete(messages, role=role, tools=tools)


class _FailOnceAppealProvider(_OpenAssessmentProvider):
    def __init__(self) -> None:
        super().__init__(accept_appeal=True)
        self.failed_once = False

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        if role == "basic" and self.grading_calls == 1 and not self.failed_once:
            self.grading_calls += 1
            self.failed_once = True
            raise RuntimeError("temporary provider failure")
        return await super().complete(messages, role=role, tools=tools)


class _MixedPlanAssessmentProvider:
    def __init__(self) -> None:
        self.multiple_choice_calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        assert tools is None
        if role == "basic":
            answer_evidence_ids = re.findall(
                r"^- \[(v1e\d+_\d+)\]",
                messages[-1].content,
                flags=re.MULTILINE,
            )
            payload = {
                "verdict": "对",
                "point_assessments": [
                    {
                        "point_id": "location",
                        "label": "matched",
                        "answer_evidence_ids": [],
                        "claim_assessments": [
                            {
                                "claim_id": "location.claim_1",
                                "label": "matched",
                                "answer_evidence_ids": answer_evidence_ids,
                                "reason": "回答命中了模型内部表示。",
                            }
                        ],
                        "reason": "回答命中了模型内部表示。",
                    }
                ],
                "diagnosis": "complete",
                "reason": "回答命中了模型内部表示。",
                "cited_evidence": [_QUOTE],
            }
        elif "单项选择题" in messages[0].content:
            self.multiple_choice_calls += 1
            payload = {
                "question": f"潜在记忆位置选择题 {self.multiple_choice_calls}",
                "options": [_CORRECT, _WRONG],
                "answer_index": 0,
                "cited_evidence": [_QUOTE],
            }
        else:
            payload = _open_question_payload("请解释潜在记忆的存储位置。")
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


def _wait_for_appeal_status(
    client: TestClient,
    session_id: str,
    expected: str,
) -> dict[str, Any]:
    payload = client.get(f"/api/v1/assessments/{session_id}").json()
    for _ in range(50):
        appeal = payload.get("appeal")
        appeal_payload = (
            cast("Mapping[str, object]", appeal) if isinstance(appeal, Mapping) else None
        )
        if appeal_payload is not None and appeal_payload.get("status") == expected:
            return payload
        time.sleep(0.01)
        payload = client.get(f"/api/v1/assessments/{session_id}").json()
    raise AssertionError(f"申诉未进入 {expected}：{payload}")


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
        trace_snapshot = client.get(f"/api/v1/observability/traces/{started['trace_id']}").json()

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
    assert trace_snapshot["summary"]["status"] == "waiting_input"


def test_fastapi_consumes_mixed_question_type_plan_in_order(tmp_path: Path) -> None:
    resource, _ = _seed_item(tmp_path)

    with TestClient(_app(tmp_path, _MixedPlanAssessmentProvider())) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "question_type_plan": ["选择题", "选择题", "简答题"],
                "focus": "mixed",
            },
        ).json()
        observed: list[str] = []
        for round_index in range(1, 4):
            waiting = _wait_for_status(
                client,
                started["session_id"],
                "awaiting_answer",
            )
            observed.append(waiting["question"]["question_type"])
            client.post(
                (
                    f"/api/v1/assessments/{started['session_id']}/questions/"
                    f"{waiting['question']['question_id']}/answers"
                ),
                json={
                    "request_id": f"answer-mixed-{round_index}",
                    "answer": _CORRECT,
                },
            )
            terminal = "completed" if round_index == 3 else "judged"
            _wait_for_status(client, started["session_id"], terminal)
            if round_index < 3:
                client.post(
                    f"/api/v1/assessments/{started['session_id']}/next",
                    json={"request_id": f"next-mixed-{round_index}"},
                )

    assert observed == ["选择题", "选择题", "开放"]


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


def test_assessment_kind_filter_is_approved_only_and_fails_closed(tmp_path: Path) -> None:
    resource, item = _seed_item(tmp_path)
    provider = _AssessmentProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        unreviewed = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "选择题",
                "knowledge_kinds": ["method"],
            },
        ).json()
        refused = _wait_for_status(client, unreviewed["session_id"], "refused")
        assert provider.calls == 0

        client.post(
            f"/api/v1/learning/items/{item.item_id}/classifications",
            json={
                "request_id": "assessment-facet-approved",
                "primary_kind": "method",
                "orientations": ["practice"],
            },
        )
        reviewed = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "选择题",
                "knowledge_kinds": ["method"],
            },
        ).json()
        waiting = _wait_for_status(client, reviewed["session_id"], "awaiting_answer")

    assert refused["error"] == "当前筛选条件没有已审核、可用于考核的知识点。"
    assert waiting["question"]["item_id"] == item.item_id
    assert provider.calls == 1


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
        "diagnosis": "complete",
        "matched_points": [
            {
                "point_id": "correct_option",
                "description": f"选择正确选项：{_CORRECT}",
            }
        ],
        "missing_points": [],
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


def test_completed_attempt_survives_app_restart_and_trace_deletion(tmp_path: Path) -> None:
    resource, item = _seed_item(tmp_path)

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
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/answers",
            json={"request_id": "durable-attempt-1", "answer": _CORRECT},
        )
        _wait_for_status(client, started["session_id"], "completed")

        before_restart = client.get(
            "/api/v1/learning/attempts",
            params={"trace_id": started["trace_id"]},
        )

    assert before_restart.status_code == 200
    attempts_before_restart = before_restart.json()["items"]
    assert len(attempts_before_restart) == 1

    (tmp_path / "trace.db").unlink()

    with TestClient(_app(tmp_path)) as restarted:
        after_restart = restarted.get(
            "/api/v1/learning/attempts",
            params={"trace_id": started["trace_id"]},
        )
        single_attempt = restarted.get(
            f"/api/v1/learning/attempts/{attempts_before_restart[0]['attempt_id']}"
        )

    assert after_restart.status_code == 200
    assert after_restart.json()["items"] == attempts_before_restart
    assert single_attempt.status_code == 200
    assert single_attempt.json() == attempts_before_restart[0]
    attempt = attempts_before_restart[0]
    assert attempt["attempt_id"] == (f"{started['trace_id']}:{attempt['assessment_span_id']}")
    assert attempt["trace_id"] == started["trace_id"]
    assert attempt["item_id"] == item.item_id
    assert attempt["question_text"] == "潜在记忆主要承载在哪里？"
    assert attempt["answer_text"] == _CORRECT
    assert attempt["initial_verdict"] == "对"
    assert attempt["final_verdict"] == "对"


def test_attempt_records_route_grader_and_pre_answer_evidence(tmp_path: Path) -> None:
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
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/evidence/reveal",
            json={"interaction": "click"},
        )
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/answers",
            json={
                "request_id": "attempt-fidelity-1",
                "answer": _CORRECT,
                "input_modality": "text",
            },
        )
        _wait_for_status(client, started["session_id"], "completed")
        attempt = client.get(
            "/api/v1/learning/attempts",
            params={"trace_id": started["trace_id"]},
        ).json()["items"][0]

    assert attempt["adaptive_route"] == {
        "format": "multiple_choice",
        "strategy": "standard",
    }
    assert attempt["effective_route"] == {
        "format": "multiple_choice",
        "strategy": "standard",
    }
    assert attempt["routing_source"] == "user_override"
    assert attempt["input_modality"] == "text"
    assert attempt["answer_format"] == "choice"
    assert attempt["evidence_revealed_before_answer"] is True
    assert attempt["grading"] == {
        "kind": "deterministic",
        "version": "multiple-choice-exact.v1",
    }
    assert attempt["question_generation"]["kind"] == "model"
    assert attempt["question_generation"]["version"].startswith("question_multiple_choice@")
    assert attempt["source_event_cursor"]["first_seq"] < attempt["source_event_cursor"]["last_seq"]


def test_learner_projection_distinguishes_not_in_memory_from_never_attempted(
    tmp_path: Path,
) -> None:
    resource, item = _seed_item(tmp_path)

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
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/"
            f"{waiting['question']['question_id']}/answers",
            json={"request_id": "projection-1", "answer": _CORRECT},
        )
        _wait_for_status(client, started["session_id"], "completed")

        response = client.get(f"/api/v1/learning/projections/{item.item_id}")
        report = client.get("/api/v1/learning/report")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "learner-projection.v1",
        "taxonomy_version": "vocabulary.v1",
        "item_id": item.item_id,
        "attempt_count": 1,
        "closed_book_attempt_count": 1,
        "verdict_counts": {"对": 1, "勉强": 0, "错": 0},
        "learning_memory_state": "not_in_memory",
        "difficulty_tier": 3,
        "validated_demand_states": {},
    }
    assert report.json()["attempt_count"] == 1
    assert report.json()["projections"] == [response.json()]


def test_verdict_correction_preserves_initial_verdict_and_reconciles_state(
    tmp_path: Path,
) -> None:
    resource, item = _seed_item(tmp_path)

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
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/"
            f"{waiting['question']['question_id']}/answers",
            json={"request_id": "wrong-before-appeal", "answer": _WRONG},
        )
        completed = _wait_for_status(client, started["session_id"], "completed")
        attempt = client.get(
            "/api/v1/learning/attempts",
            params={"trace_id": started["trace_id"]},
        ).json()["items"][0]

        correction = client.post(
            f"/api/v1/learning/attempts/{attempt['attempt_id']}/verdict-corrections",
            json={
                "request_id": "appeal-1",
                "final_verdict": "对",
                "reason": "Evidence 证明原判决错误",
            },
        )
        correction_retry = client.post(
            f"/api/v1/learning/attempts/{attempt['attempt_id']}/verdict-corrections",
            json={
                "request_id": "appeal-1",
                "final_verdict": "对",
                "reason": "Evidence 证明原判决错误",
            },
        )
        corrected = client.get(
            "/api/v1/learning/attempts",
            params={"trace_id": started["trace_id"]},
        ).json()["items"][0]
        projection = client.get(f"/api/v1/learning/projections/{item.item_id}").json()
        candidates = client.get("/api/v1/learning/eval-candidates").json()["items"]

    assert correction.status_code == 200
    assert completed["attempt_id"] == attempt["attempt_id"]
    assert correction_retry.status_code == 200
    assert correction_retry.json() == correction.json()
    assert corrected["initial_verdict"] == "错"
    assert corrected["final_verdict"] == "对"
    assert corrected["appeal_status"] == "overturned"
    assert projection["verdict_counts"] == {"对": 1, "勉强": 0, "错": 0}
    assert projection["learning_memory_state"] == "not_in_memory"
    assert len(candidates) == 1
    assert candidates[0]["attempt_id"] == attempt["attempt_id"]
    assert candidates[0]["human_verdict"] == "对"
    assert candidates[0]["release_gate_eligible"] is False

    with LearningPersistence(tmp_path / "learning.db") as persistence:
        assert persistence.memory.state_of(item.item_id) is None
        correction_fact = persistence.learning_facts.facts(event_type="learning.verdict_corrected")[
            0
        ]
        assert correction_fact.payload["reconciliation"] == {
            "item_id": item.item_id,
            "learning_memory_state": "not_in_memory",
            "difficulty_tier": 3,
            "through_event_id": correction_fact.event_id,
        }

    correction_trace = TraceStore(tmp_path / "trace.db")
    try:
        published = correction_trace.events(correction_fact.trace_id)
    finally:
        correction_trace.close()
    assert any(
        event.type == "learning.verdict_corrected"
        and event.payload["event_id"] == correction_fact.event_id
        and event.payload["payload"]["reconciliation"]["through_event_id"]
        == correction_fact.event_id
        for event in published
    )


def test_only_approved_demand_validation_enters_learner_projection(
    tmp_path: Path,
) -> None:
    resource, item = _seed_item(tmp_path)

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
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/"
            f"{waiting['question']['question_id']}/answers",
            json={"request_id": "demand-answer-1", "answer": _CORRECT},
        )
        _wait_for_status(client, started["session_id"], "completed")
        attempt = client.get(
            "/api/v1/learning/attempts",
            params={"trace_id": started["trace_id"]},
        ).json()["items"][0]

        validation = client.post(
            f"/api/v1/learning/attempts/{attempt['attempt_id']}/demand-validations",
            json={
                "request_id": "demand-validation-1",
                "validated_demand": "apply",
                "validator_kind": "user",
                "rationale": "这道题要求把材料规则用于具体选项判断",
            },
        )
        validation_retry = client.post(
            f"/api/v1/learning/attempts/{attempt['attempt_id']}/demand-validations",
            json={
                "request_id": "demand-validation-1",
                "validated_demand": "apply",
                "validator_kind": "user",
                "rationale": "这道题要求把材料规则用于具体选项判断",
            },
        )
        forged_judge = client.post(
            f"/api/v1/learning/attempts/{attempt['attempt_id']}/demand-validations",
            json={
                "request_id": "forged-judge",
                "validated_demand": "design",
                "validator_kind": "calibrated_judge",
                "calibration_version": "totally-trusted-by-client",
                "rationale": "伪造校准身份",
            },
        )
        revised = client.post(
            f"/api/v1/learning/attempts/{attempt['attempt_id']}/demand-validations",
            json={
                "request_id": "demand-validation-2",
                "validated_demand": "explain",
                "validator_kind": "user",
                "rationale": "人工复核后改为解释",
            },
        )
        projection = client.get(f"/api/v1/learning/projections/{item.item_id}").json()

    assert validation.status_code == 201
    assert validation.json()["review_status"] == "approved"
    assert validation_retry.status_code == 201
    assert validation_retry.json() == validation.json()
    assert forged_judge.status_code == 422
    assert revised.json()["revision"] == 2
    assert revised.json()["supersedes_id"] == validation.json()["validation_id"]
    assert projection["validated_demand_states"] == {"explain": "passed"}


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


def test_user_cancel_is_idempotent_and_closes_the_trace(tmp_path: Path) -> None:
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
        _wait_for_status(client, started["session_id"], "awaiting_answer")

        first = client.delete(f"/api/v1/assessments/{started['session_id']}")
        second = client.delete(f"/api/v1/assessments/{started['session_id']}")
        trace_snapshot = client.get(f"/api/v1/observability/traces/{started['trace_id']}").json()

    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"
    assert trace_snapshot["summary"]["status"] == "cancelled"


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
        assert judged["attempt_id"] is not None
        assert judged["judgement"]["verdict"] == "错"
        assert judged["judgement"]["diagnosis"] == "wrong_focus"
        assert judged["judgement"]["matched_points"] == []
        assert judged["judgement"]["missing_points"] == [
            {
                "point_id": "location",
                "description": "指出潜在记忆位于模型内部表示",
            }
        ]
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
    assert second["attempt_id"] is None
    assert second["question"]["question_id"] != first_question_id
    assert second["question"]["text"] == "潜在记忆为什么不等于外部文件？"
    assert provider.question_calls == 2


def test_user_appeal_regrades_once_and_appends_a_reconciled_correction(
    tmp_path: Path,
) -> None:
    resource, item = _seed_item(tmp_path)
    provider = _OpenAssessmentProvider(accept_appeal=True)

    with TestClient(_app(tmp_path, provider)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "简答题",
            },
        ).json()
        waiting = _wait_for_status(client, started["session_id"], "awaiting_answer")
        question_id = waiting["question"]["question_id"]
        original_answer = "它放在文件里。"
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/answers",
            json={"request_id": "initial-answer", "answer": original_answer},
        )
        completed = _wait_for_status(client, started["session_id"], "completed")

        assert completed["appeal"] == {
            "status": "available",
            "supplemental_answer": None,
            "original_verdict": "错",
            "final_verdict": None,
            "reason": None,
        }
        command = {
            "request_id": "appeal-command-1",
            "supplemental_answer": "潜在记忆实际位于模型内部表示。",
        }
        submitted = client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/appeals",
            json=command,
        )
        retried = client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/appeals",
            json=command,
        )
        resolved = _wait_for_appeal_status(client, started["session_id"], "resolved")
        second = client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/appeals",
            json={
                "request_id": "appeal-command-2",
                "supplemental_answer": "再补一次。",
            },
        )
        attempt = client.get(f"/api/v1/learning/attempts/{completed['attempt_id']}").json()
        projection = client.get(f"/api/v1/learning/projections/{item.item_id}").json()

    assert submitted.status_code == 202
    assert retried.status_code == 202
    assert resolved["judgement"]["verdict"] == "对", resolved
    assert resolved["appeal"] == {
        "status": "resolved",
        "supplemental_answer": "潜在记忆实际位于模型内部表示。",
        "original_verdict": "错",
        "final_verdict": "对",
        "reason": "结合补充说明，回答已经覆盖位置要点。",
    }
    assert second.status_code == 409
    assert attempt["answer_text"] == original_answer
    assert attempt["supplemental_answer"] == "潜在记忆实际位于模型内部表示。"
    assert attempt["initial_verdict"] == "错"
    assert attempt["final_verdict"] == "对"
    assert attempt["appeal_status"] == "overturned"
    assert attempt["concept_state"] is None
    assert projection["learning_memory_state"] == "not_in_memory"
    assert provider.grading_calls == 2


def test_appeal_has_an_independent_trace_lifecycle_and_can_be_cancelled(
    tmp_path: Path,
) -> None:
    resource, _ = _seed_item(tmp_path)
    provider = _BlockingAppealProvider()

    with TestClient(_app(tmp_path, provider)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "简答题",
            },
        ).json()
        waiting = _wait_for_status(client, started["session_id"], "awaiting_answer")
        question_id = waiting["question"]["question_id"]
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/answers",
            json={"request_id": "initial-answer", "answer": "它放在文件里。"},
        )
        completed = _wait_for_status(client, started["session_id"], "completed")
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/appeals",
            json={
                "request_id": "appeal-blocked",
                "supplemental_answer": "潜在记忆位于模型内部表示。",
            },
        )
        assert provider.appeal_started.wait(timeout=1)

        running_trace = client.get(f"/api/v1/observability/traces/{started['trace_id']}").json()
        cancelled = client.delete(f"/api/v1/assessments/{started['session_id']}")
        attempt = client.get(f"/api/v1/learning/attempts/{completed['attempt_id']}").json()
        cancelled_trace = client.get(f"/api/v1/observability/traces/{started['trace_id']}").json()

    assert running_trace["summary"]["status"] == "running"
    assert cancelled.status_code == 200
    assert cancelled.json()["appeal"]["status"] == "cancelled"
    assert provider.appeal_cancelled.wait(timeout=1)
    assert attempt["supplemental_answer"] is None
    assert attempt["final_verdict"] == "错"
    assert cancelled_trace["summary"]["status"] == "cancelled"

    trace = TraceStore(tmp_path / "trace.db")
    try:
        events = trace.events(started["trace_id"])
    finally:
        trace.close()
    run_end = next(event for event in events if event.type == "web.assessment_run.ended")
    appeal_start = next(event for event in events if event.type == "web.assessment_appeal.started")
    appeal_end = next(event for event in events if event.type == "web.assessment_appeal.ended")
    assert run_end.seq < appeal_start.seq < appeal_end.seq
    assert appeal_start.span_id == appeal_end.span_id
    assert appeal_start.parent_span_id is None
    assert appeal_end.payload["status"] == "cancelled"


def test_failed_appeal_can_retry_same_frozen_command_once_provider_recovers(
    tmp_path: Path,
) -> None:
    resource, _ = _seed_item(tmp_path)
    provider = _FailOnceAppealProvider()
    command = {
        "request_id": "appeal-retry",
        "supplemental_answer": "潜在记忆位于模型内部表示。",
    }

    with TestClient(_app(tmp_path, provider)) as client:
        started = client.post(
            "/api/v1/assessments",
            json={
                "resource_ids": [resource.resource_id],
                "rounds": 1,
                "question_type": "简答题",
            },
        ).json()
        waiting = _wait_for_status(client, started["session_id"], "awaiting_answer")
        question_id = waiting["question"]["question_id"]
        client.post(
            f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/answers",
            json={"request_id": "initial-answer", "answer": "它放在文件里。"},
        )
        completed = _wait_for_status(client, started["session_id"], "completed")
        appeal_url = f"/api/v1/assessments/{started['session_id']}/questions/{question_id}/appeals"
        client.post(appeal_url, json=command)
        failed = _wait_for_appeal_status(client, started["session_id"], "failed")
        retried = client.post(appeal_url, json=command)
        resolved = _wait_for_appeal_status(client, started["session_id"], "resolved")
        attempt = client.get(f"/api/v1/learning/attempts/{completed['attempt_id']}").json()

    assert failed["appeal"]["status"] == "failed"
    assert retried.status_code == 202
    assert resolved["appeal"]["status"] == "resolved"
    assert attempt["supplemental_answer"] == command["supplemental_answer"]
    assert attempt["final_verdict"] == "对"
    assert provider.grading_calls == 3

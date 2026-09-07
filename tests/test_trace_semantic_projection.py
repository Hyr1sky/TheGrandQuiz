"""安全、版本化的 Trace Semantic Projection 行为契约。"""

import pytest

from grandquiz.interfaces.trace_projection import project_trace
from grandquiz.kernel.events import AgentEvent


def _event(
    event_type: str,
    seq: int,
    *,
    payload: dict[str, object] | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        type=event_type,
        seq=seq,
        ts=float(seq + 1),
        trace_id="trace-safe",
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload=payload or {},
    )


def test_project_trace_explains_mc_rejection_without_leaking_internal_payload() -> None:
    events = [
        _event(
            "learning.multiple_choice_generation.started",
            0,
            span_id="generation",
            parent_span_id="assessment",
            payload={"status": "running", "item_id": "secret-item"},
        ),
        _event(
            "learning.multiple_choice_generation.attempt_rejected",
            1,
            parent_span_id="generation",
            payload={
                "attempt": 1,
                "stage": "generation",
                "reason_code": "invalid_json",
                "retained_distractor_count": 0,
                "prompt": "SECRET-PROMPT",
            },
        ),
        _event(
            "plugin.future.secret_event",
            2,
            payload={"answer": "SECRET-ANSWER", "reason_code": "private_reason"},
        ),
        _event(
            "web.assessment_run.degraded",
            3,
            span_id="assessment",
            payload={
                "status": "degraded",
                "stage": "question_generation",
                "reason_code": "question_generation_exhausted",
                "exception": "SECRET-EXCEPTION",
            },
        ),
    ]

    run = project_trace(events, trace_id="trace-safe")

    assert run.schema_version == 1
    assert run.trace_id == "trace-safe"
    assert run.status == "waiting_input"
    assert [event.operation for event in run.events] == [
        "multiple_choice_generation",
        "multiple_choice_generation",
        "other",
        "assessment_run",
    ]
    assert run.events[1].phase == "attempt_rejected"
    assert run.events[1].attempt == 1
    assert run.events[1].stage == "generation"
    assert run.events[1].reason_code == "invalid_json"
    assert run.events[2].reason_code is None
    assert run.events[3].phase == "waiting_input"
    assert run.events[3].stage == "question_generation"
    assert run.events[3].reason_code == "question_generation_exhausted"
    serialized = run.model_dump_json()
    for forbidden in (
        "plugin.future.secret_event",
        "private_reason",
        "secret-item",
        "SECRET-PROMPT",
        "SECRET-ANSWER",
        "SECRET-EXCEPTION",
    ):
        assert forbidden not in serialized


def test_project_trace_exposes_allowlisted_assessment_trace_relation() -> None:
    assessment_trace_id = "a" * 32
    run = project_trace(
        [
            _event(
                "navigation.requested",
                0,
                payload={
                    "target": "assessment",
                    "params": {
                        "resource_id": "SECRET-RESOURCE",
                        "assessment_trace_id": assessment_trace_id,
                    },
                },
            ),
            _event(
                "navigation.requested",
                1,
                payload={
                    "target": "assessment",
                    "params": {"assessment_trace_id": "not-a-trace-id"},
                },
            ),
            _event(
                "plugin.future.secret_event",
                2,
                payload={"assessment_trace_id": "b" * 32},
            ),
        ],
        trace_id="trace-safe",
    )

    assert [link.model_dump() for link in run.related_traces] == [
        {"trace_id": assessment_trace_id, "kind": "assessment"}
    ]
    serialized = run.model_dump_json()
    assert "SECRET-RESOURCE" not in serialized
    assert "not-a-trace-id" not in serialized
    assert "b" * 32 not in serialized


def test_project_trace_distinguishes_generation_judgement_grading_and_commit() -> None:
    events = [
        _event("assessment.started", 0, span_id="assessment"),
        _event(
            "learning.multiple_choice_generation.started",
            1,
            span_id="generation",
            parent_span_id="assessment",
        ),
        _event(
            "model.started",
            2,
            span_id="generate-model",
            parent_span_id="generation",
            payload={"role": "enrich", "messages": ["SECRET-GENERATION"]},
        ),
        _event(
            "model.ended",
            3,
            span_id="generate-model",
            parent_span_id="generation",
            payload={
                "ok": True,
                "output": "SECRET-QUESTION",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        ),
        _event(
            "model.started",
            4,
            span_id="judge-model",
            parent_span_id="generation",
            payload={"role": "basic", "messages": ["SECRET-DISTRACTOR"]},
        ),
        _event(
            "model.ended",
            5,
            span_id="judge-model",
            parent_span_id="generation",
            payload={
                "ok": True,
                "output": '{"label":"合理干扰","rationale":"SECRET-RATIONALE"}',
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        ),
        _event(
            "learning.multiple_choice_generation.ended",
            6,
            span_id="generation",
            parent_span_id="assessment",
            payload={"ok": True, "attempts": 1, "judge_calls": 1},
        ),
        _event(
            "learning.question_asked",
            7,
            parent_span_id="assessment",
            payload={"question": "SECRET-QUESTION"},
        ),
        _event(
            "model.started",
            8,
            span_id="grading-model",
            parent_span_id="assessment",
            payload={"role": "basic", "messages": ["SECRET-ANSWER"]},
        ),
        _event(
            "model.ended",
            9,
            span_id="grading-model",
            parent_span_id="assessment",
            payload={
                "ok": True,
                "output": "SECRET-GRADE",
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        ),
        _event(
            "learning.answer_judged",
            10,
            parent_span_id="assessment",
            payload={"answer": "SECRET-ANSWER", "verdict": "对"},
        ),
        _event(
            "learning.assessment_judgement_committed",
            11,
            parent_span_id="assessment",
            payload={"item_id": "SECRET-ITEM"},
        ),
        _event(
            "assessment.ended",
            12,
            span_id="assessment",
            payload={"ok": True, "status": "completed"},
        ),
    ]

    run = project_trace(events, trace_id="trace-safe")

    assert [event.operation for event in run.events] == [
        "assessment_run",
        "multiple_choice_generation",
        "multiple_choice_generation",
        "multiple_choice_generation",
        "distractor_judgement",
        "distractor_judgement",
        "multiple_choice_generation",
        "assessment_run",
        "grading",
        "grading",
        "grading",
        "learning_commit",
        "assessment_run",
    ]
    assert run.status == "completed"
    assert run.summary.model_calls == 3
    assert run.summary.prompt_tokens == 22
    assert run.summary.completion_tokens == 6
    assert [event.sequence for event in run.events] == list(range(1, 14))
    assert run.events[2].span_id == "generate-model"
    assert run.events[2].parent_span_id == "generation"
    assert run.events[3].span_id == "generate-model"
    assert run.events[3].parent_span_id == "generation"
    assert run.events[3].tokens == 12
    assert run.events[3].latency_ms == 1000.0
    assert run.events[5].tokens == 6
    assert run.events[9].tokens == 10
    assert run.events[10].latency_ms is None
    serialized = run.model_dump_json()
    for forbidden in (
        "SECRET-GENERATION",
        "SECRET-QUESTION",
        "SECRET-DISTRACTOR",
        "SECRET-RATIONALE",
        "SECRET-ANSWER",
        "SECRET-GRADE",
        "SECRET-ITEM",
    ):
        assert forbidden not in serialized


def test_project_trace_builds_failure_summary_from_structured_counts_only() -> None:
    run = project_trace(
        [
            _event(
                "learning.multiple_choice_generation.attempt_rejected",
                0,
                payload={
                    "attempt": 1,
                    "stage": "generation",
                    "reason_code": "invalid_json",
                    "exception": "SECRET-NATURAL-LANGUAGE-FAILURE",
                },
            ),
            _event(
                "learning.multiple_choice_generation.attempt_rejected",
                1,
                payload={
                    "attempt": 2,
                    "stage": "distractor_quality",
                    "reason_code": "distractor_quality_unmet",
                },
            ),
            _event(
                "learning.multiple_choice_generation.attempt_rejected",
                2,
                payload={
                    "attempt": 3,
                    "stage": "distractor_quality",
                    "reason_code": "distractor_quality_unmet",
                },
            ),
            _event(
                "web.assessment_run.degraded",
                3,
                payload={
                    "status": "degraded",
                    "stage": "question_generation",
                    "reason_code": "question_generation_exhausted",
                },
            ),
        ],
        trace_id="trace-safe",
    )

    assert run.summary.headline == (
        "选择题生成失败：3 次尝试；干扰项质量不足 2 次；输出格式无效 1 次"
    )
    assert run.summary.recommended_action == "可以重试本题，或跳过此题继续。"
    assert "SECRET-NATURAL-LANGUAGE-FAILURE" not in run.model_dump_json()


def test_project_trace_does_not_invent_mc_attempts_for_open_question_failure() -> None:
    run = project_trace(
        [
            _event(
                "web.assessment_run.degraded",
                0,
                payload={
                    "status": "degraded",
                    "stage": "question_generation",
                    "reason_code": "question_generation_exhausted",
                },
            )
        ],
        trace_id="trace-safe",
    )

    assert run.summary.headline == "题目生成失败"
    assert "选择题" not in run.summary.headline
    assert "0 次尝试" not in run.summary.headline
    assert run.summary.recommended_action == "可以重试本题，或跳过此题继续。"


def test_project_trace_omits_unknown_mc_attempt_count() -> None:
    run = project_trace(
        [
            _event(
                "learning.multiple_choice_generation.started",
                0,
                span_id="generation",
            ),
            _event(
                "web.assessment_run.degraded",
                1,
                payload={
                    "status": "degraded",
                    "stage": "question_generation",
                    "reason_code": "question_generation_exhausted",
                },
            ),
        ],
        trace_id="trace-safe",
    )

    assert run.summary.headline == "选择题生成失败"
    assert "0 次尝试" not in run.summary.headline


def test_project_trace_scopes_generation_failure_to_current_round_in_mixed_plan() -> None:
    run = project_trace(
        [
            _event(
                "learning.multiple_choice_generation.started",
                0,
                span_id="round-1-generation",
            ),
            _event(
                "learning.multiple_choice_generation.attempt_rejected",
                1,
                payload={
                    "attempt": 5,
                    "stage": "distractor_quality",
                    "reason_code": "distractor_quality_unmet",
                },
            ),
            _event("learning.question_asked", 2, parent_span_id="assessment"),
            _event("learning.answer_judged", 3, parent_span_id="assessment"),
            _event(
                "web.assessment_run.degraded",
                4,
                payload={
                    "status": "degraded",
                    "stage": "question_generation",
                    "reason_code": "question_generation_exhausted",
                },
            ),
        ],
        trace_id="trace-safe",
    )

    assert run.summary.headline == "题目生成失败"
    assert "选择题" not in run.summary.headline
    assert "5 次尝试" not in run.summary.headline
    assert "干扰项质量不足" not in run.summary.headline


@pytest.mark.parametrize(
    ("tail", "headline", "action"),
    [
        (
            [_event("learning.multiple_choice_generation.started", 1)],
            "运行正在进行",
            None,
        ),
        (
            [_event("learning.question_asked", 1)],
            "考核正在等待输入",
            "请提交答案或完成当前审批后继续。",
        ),
        (
            [_event("assessment.ended", 1, payload={"ok": True})],
            "运行已完成",
            None,
        ),
    ],
)
def test_project_trace_does_not_keep_stale_generation_failure_after_recovery(
    tail: list[AgentEvent],
    headline: str,
    action: str | None,
) -> None:
    degraded = _event(
        "web.assessment_run.degraded",
        0,
        payload={
            "status": "degraded",
            "stage": "question_generation",
            "reason_code": "question_generation_exhausted",
        },
    )

    run = project_trace([degraded, *tail], trace_id="trace-safe")

    assert run.summary.headline == headline
    assert run.summary.recommended_action == action


@pytest.mark.parametrize(
    ("events", "headline", "action"),
    [
        (
            [
                _event(
                    "web.assessment_run.degraded",
                    0,
                    payload={"status": "degraded", "stage": "grading"},
                )
            ],
            "判卷未完成",
            "可以跳过此题继续考核。",
        ),
        (
            [_event("learning.question_asked", 0)],
            "考核正在等待输入",
            "请提交答案或完成当前审批后继续。",
        ),
        (
            [
                _event("error", 0),
                _event("assessment.ended", 1, payload={"ok": False}),
            ],
            "运行失败；记录到 1 个错误",
            "请查看失败阶段与原因；可以结束本轮后重试。",
        ),
        (
            [_event("assessment.ended", 0, payload={"status": "cancelled"})],
            "运行已取消",
            "可以在准备好后重新开始。",
        ),
        ([_event("assessment.ended", 0, payload={"ok": True})], "运行已完成", None),
        ([_event("assessment.started", 0)], "运行正在进行", None),
        ([], None, None),
    ],
)
def test_project_trace_summary_branches_use_only_supported_actions(
    events: list[AgentEvent],
    headline: str | None,
    action: str | None,
) -> None:
    run = project_trace(events, trace_id="trace-safe")

    assert run.summary.headline == headline
    assert run.summary.recommended_action == action


def test_project_trace_marks_fatal_assessment_end_as_failed() -> None:
    events = [
        _event("assessment.started", 0, span_id="assessment"),
        _event(
            "error",
            1,
            span_id="assessment",
            payload={"error_type": "SECRET-INTERNAL-ERROR"},
        ),
        _event(
            "assessment.ended",
            2,
            span_id="assessment",
            payload={"ok": False, "error": "SECRET-FAILURE"},
        ),
    ]

    run = project_trace(events, trace_id="trace-safe")

    assert run.status == "failed"
    assert run.ended_at == 3.0
    assert run.summary.error_count == 1
    assert run.events[-1].operation == "assessment_run"
    assert run.events[-1].status == "failed"
    assert "SECRET-INTERNAL-ERROR" not in run.model_dump_json()
    assert "SECRET-FAILURE" not in run.model_dump_json()


def test_project_trace_explains_provider_quota_exhaustion_without_leaking_raw_error() -> None:
    raw_error = (
        "PermissionDeniedError('Error code: 403 - "
        "{\\'error\\': {\\'message\\': \\'Free quota exhausted.\\', "
        "\\'code\\': \\'AllocationQuota.FreeTierOnly\\'}, "
        "\\'request_id\\': \\'SECRET-REQUEST-ID\\'}')"
    )
    run = project_trace(
        [
            _event("assessment.started", 0, span_id="assessment"),
            _event(
                "learning.multiple_choice_generation.started",
                1,
                span_id="generation",
                parent_span_id="assessment",
            ),
            _event(
                "model.started",
                2,
                span_id="model",
                parent_span_id="generation",
                payload={"role": "enrich"},
            ),
            _event(
                "model.ended",
                3,
                span_id="model",
                parent_span_id="generation",
                payload={"ok": False, "error": raw_error},
            ),
            _event(
                "learning.multiple_choice_generation.ended",
                4,
                span_id="generation",
                parent_span_id="assessment",
                payload={
                    "ok": False,
                    "attempts": 1,
                    "stage": "model_call",
                    "error_type": "PermissionDeniedError",
                },
            ),
            _event(
                "assessment.ended",
                5,
                span_id="assessment",
                payload={"ok": False, "error": raw_error},
            ),
            _event("error", 6, payload={"error_type": "PermissionDeniedError"}),
        ],
        trace_id="trace-safe",
    )

    assert run.status == "failed"
    assert run.events[3].reason_code == "provider_quota_exhausted"
    assert run.summary.headline == "选择题生成失败：模型服务免费额度已用尽"
    assert run.summary.recommended_action == (
        "请补充余额或关闭模型服务的“仅使用免费额度”设置，然后重试本题。"
    )
    serialized = run.model_dump_json()
    assert "AllocationQuota.FreeTierOnly" not in serialized
    assert "SECRET-REQUEST-ID" not in serialized


def test_project_trace_exposes_typed_provider_failure_without_vendor_code() -> None:
    run = project_trace(
        [
            _event("assessment.started", 0, span_id="assessment"),
            _event(
                "learning.multiple_choice_generation.started",
                1,
                span_id="generation",
                parent_span_id="assessment",
            ),
            _event(
                "model.started",
                2,
                span_id="model",
                parent_span_id="generation",
                payload={"role": "enrich"},
            ),
            _event(
                "model.ended",
                3,
                span_id="model",
                parent_span_id="generation",
                payload={
                    "ok": False,
                    "error": "SECRET-UPSTREAM-BODY",
                    "provider_failure_category": "rate_limited",
                    "provider_failure_code": "provider_rate_limited",
                    "provider_status_code": 429,
                    "provider_code": "rate_limit_exceeded",
                    "provider_retryable": True,
                },
            ),
            _event("assessment.ended", 4, span_id="assessment", payload={"ok": False}),
            _event("error", 5, payload={"error_type": "ProviderFailure"}),
        ],
        trace_id="trace-safe",
    )

    provider_failure = run.events[3].provider_failure
    assert provider_failure is not None
    assert provider_failure.category == "rate_limited"
    assert provider_failure.status_code == 429
    assert provider_failure.retryable is True
    assert run.events[3].reason_code == "provider_rate_limited"
    assert run.summary.headline == "选择题生成失败：模型服务请求过于频繁"
    serialized = run.model_dump_json()
    assert "rate_limit_exceeded" not in serialized
    assert "SECRET-UPSTREAM-BODY" not in serialized


def test_project_trace_exposes_safe_transport_attempts_without_double_counting_usage() -> None:
    run = project_trace(
        [
            _event(
                "learning.multiple_choice_generation.started",
                0,
                span_id="generation",
                parent_span_id="assessment",
            ),
            _event(
                "model.started",
                1,
                span_id="model",
                parent_span_id="generation",
                payload={"role": "enrich"},
            ),
            _event(
                "model_attempt.started",
                2,
                span_id="attempt-1",
                parent_span_id="model",
                payload={"attempt_index": 1},
            ),
            _event(
                "model_attempt.ended",
                3,
                span_id="attempt-1",
                parent_span_id="model",
                payload={
                    "ok": False,
                    "attempt_index": 1,
                    "error": "SECRET-UPSTREAM-BODY",
                    "provider_failure_category": "rate_limited",
                    "provider_failure_code": "provider_rate_limited",
                    "provider_status_code": 429,
                    "provider_retryable": True,
                    "provider_retry_after_seconds": 2.0,
                },
            ),
            _event(
                "model.retry_decided",
                4,
                span_id="attempt-1",
                parent_span_id="model",
                payload={
                    "attempt_index": 1,
                    "action": "retry",
                    "reason": "transient_failure",
                    "delay_seconds": 2.0,
                },
            ),
            _event(
                "model_retry_wait.started",
                5,
                span_id="wait-1",
                parent_span_id="model",
                payload={"after_attempt": 1, "delay_seconds": 2.0},
            ),
            _event(
                "model_retry_wait.ended",
                6,
                span_id="wait-1",
                parent_span_id="model",
                payload={"ok": True, "after_attempt": 1},
            ),
            _event(
                "model_attempt.started",
                7,
                span_id="attempt-2",
                parent_span_id="model",
                payload={"attempt_index": 2},
            ),
            _event(
                "model_attempt.ended",
                8,
                span_id="attempt-2",
                parent_span_id="model",
                payload={
                    "ok": True,
                    "attempt_index": 2,
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                },
            ),
            _event(
                "model.ended",
                9,
                span_id="model",
                parent_span_id="generation",
                payload={
                    "ok": True,
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 3,
                        "total_tokens": 10,
                    },
                },
            ),
            _event(
                "learning.multiple_choice_generation.ended",
                10,
                span_id="generation",
                parent_span_id="assessment",
                payload={"ok": True},
            ),
        ],
        trace_id="trace-safe",
    )

    assert all(event.operation == "multiple_choice_generation" for event in run.events)
    assert run.events[2].attempt == 1
    assert run.events[3].provider_failure is not None
    assert run.events[3].provider_failure.retry_after_seconds == 2.0
    assert run.events[4].provider_retry is not None
    assert run.events[4].provider_retry.action == "retry"
    assert run.events[4].provider_retry.delay_seconds == 2.0
    assert run.summary.model_calls == 1
    assert run.summary.retries == 1
    assert run.summary.prompt_tokens == 7
    assert run.summary.completion_tokens == 3
    assert run.summary.headline == "运行正在进行"
    assert run.summary.recommended_action is None
    assert "SECRET-UPSTREAM-BODY" not in run.model_dump_json()


def test_project_trace_exposes_safe_fallback_and_actual_candidate_identity() -> None:
    primary = {
        "schema_version": "model-identity.v1",
        "purpose": "chat",
        "selection_source": "default",
        "configuration_fingerprint": "1" * 64,
        "policy_fingerprint": "9" * 64,
    }
    backup = {
        **primary,
        "selection_source": "fallback",
        "configuration_fingerprint": "2" * 64,
    }
    run = project_trace(
        [
            _event("model.started", 0, span_id="model", payload={"model_identity": primary}),
            _event(
                "model_attempt.started",
                1,
                span_id="attempt-1",
                parent_span_id="model",
                payload={"attempt_index": 1, "candidate_index": 1, "model_identity": primary},
            ),
            _event(
                "model_attempt.ended",
                2,
                span_id="attempt-1",
                parent_span_id="model",
                payload={
                    "ok": False,
                    "attempt_index": 1,
                    "candidate_index": 1,
                    "model_identity": primary,
                    "provider_failure_category": "server_error",
                    "provider_failure_code": "provider_unavailable",
                    "provider_retryable": True,
                    "error": "SECRET-UPSTREAM-BODY",
                },
            ),
            _event(
                "model.fallback_decided",
                3,
                span_id="attempt-1",
                parent_span_id="model",
                payload={
                    "attempt_index": 1,
                    "action": "switch",
                    "reason": "candidate_available",
                    "from_candidate": 1,
                    "to_candidate": 2,
                    "model_identity": primary,
                    "selected_model_identity": backup,
                    "secret_profile_id": "do-not-project",
                },
            ),
            _event(
                "model_attempt.started",
                4,
                span_id="attempt-2",
                parent_span_id="model",
                payload={"attempt_index": 2, "candidate_index": 2, "model_identity": backup},
            ),
            _event(
                "model_attempt.ended",
                5,
                span_id="attempt-2",
                parent_span_id="model",
                payload={"ok": True, "attempt_index": 2, "model_identity": backup},
            ),
            _event(
                "model.ended",
                6,
                span_id="model",
                payload={
                    "ok": True,
                    "selected_model_identity": backup,
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            ),
        ],
        trace_id="trace-safe",
    )

    fallback = run.events[3].provider_fallback
    assert fallback is not None
    assert fallback.action == "switch"
    assert fallback.from_candidate == 1
    assert fallback.to_candidate == 2
    assert run.events[4].execution_identity is not None
    assert run.events[4].execution_identity.selection_source == "fallback"
    assert run.events[6].execution_identity == run.events[4].execution_identity
    assert run.summary.fallbacks == 1
    assert run.summary.retries == 0
    serialized = run.model_dump_json()
    assert "do-not-project" not in serialized
    assert "SECRET-UPSTREAM-BODY" not in serialized


def test_project_trace_completed_chat_uses_latest_turn_summary() -> None:
    run = project_trace(
        [
            _event("agent_turn.started", 0, span_id="turn-1"),
            _event(
                "model.started",
                1,
                span_id="model-1",
                parent_span_id="turn-1",
                payload={"role": "basic"},
            ),
            _event(
                "model.ended",
                2,
                span_id="model-1",
                parent_span_id="turn-1",
                payload={
                    "ok": False,
                    "provider_failure_category": "rate_limited",
                    "provider_failure_code": "provider_rate_limited",
                    "provider_status_code": 429,
                    "provider_code": "rate_limit_exceeded",
                    "provider_retryable": True,
                },
            ),
            _event("agent_turn.ended", 3, span_id="turn-1", payload={"ok": False}),
            _event("agent_turn.started", 4, span_id="turn-2"),
            _event(
                "model.started",
                5,
                span_id="model-2",
                parent_span_id="turn-2",
                payload={"role": "basic"},
            ),
            _event(
                "model.ended",
                6,
                span_id="model-2",
                parent_span_id="turn-2",
                payload={
                    "ok": True,
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            ),
            _event("agent_turn.ended", 7, span_id="turn-2", payload={"ok": True}),
        ],
        trace_id="trace-safe",
    )

    assert run.status == "completed"
    assert run.summary.headline == "运行已完成"
    assert run.summary.recommended_action is None


def test_project_trace_distinguishes_zero_usage_from_unknown_usage() -> None:
    idle = project_trace([], trace_id="trace-idle")
    assert idle.status == "idle"
    assert idle.summary.prompt_tokens == 0
    assert idle.summary.completion_tokens == 0
    assert idle.summary.latency_ms is None

    unknown = project_trace(
        [
            _event("model.started", 0, span_id="model", payload={"role": "basic"}),
            _event("model.ended", 1, span_id="model", payload={"ok": False}),
        ],
        trace_id="trace-safe",
    )
    assert unknown.summary.model_calls == 1
    assert unknown.summary.prompt_tokens is None
    assert unknown.summary.completion_tokens is None
    assert unknown.events[-1].tokens is None


def test_project_trace_maps_unknown_public_values_to_other() -> None:
    run = project_trace(
        [
            _event(
                "learning.multiple_choice_generation.attempt_rejected",
                0,
                payload={
                    "attempt": 3,
                    "stage": "SECRET-NEW-STAGE",
                    "reason_code": "SECRET-NEW-REASON",
                },
            )
        ],
        trace_id="trace-safe",
    )

    assert run.events[0].stage == "other"
    assert run.events[0].reason_code == "other"
    assert "SECRET-NEW-STAGE" not in run.model_dump_json()
    assert "SECRET-NEW-REASON" not in run.model_dump_json()


def test_project_trace_drops_all_sensitive_grading_fields() -> None:
    run = project_trace(
        [
            _event(
                "learning.answer_judged",
                0,
                parent_span_id="assessment",
                payload={
                    "prompt": "SECRET-PROMPT",
                    "completion": "SECRET-COMPLETION",
                    "answer": "SECRET-ANSWER",
                    "cited_evidence": ["SECRET-EVIDENCE"],
                    "url": "https://secret.example/path",
                    "filename": "SECRET-FILE.md",
                    "exception": "SECRET-EXCEPTION",
                    "api_key": "SECRET-KEY",
                },
            )
        ],
        trace_id="trace-safe",
    )

    assert run.events[0].operation == "grading"
    serialized = run.model_dump_json()
    for sentinel in (
        "SECRET-PROMPT",
        "SECRET-COMPLETION",
        "SECRET-ANSWER",
        "SECRET-EVIDENCE",
        "secret.example",
        "SECRET-FILE",
        "SECRET-EXCEPTION",
        "SECRET-KEY",
    ):
        assert sentinel not in serialized


def test_project_trace_reports_approval_as_waiting_for_input() -> None:
    run = project_trace(
        [
            _event(
                "approval.requested",
                0,
                payload={"candidates": ["SECRET-CANDIDATE"]},
            )
        ],
        trace_id="trace-safe",
    )

    assert run.status == "waiting_input"
    assert run.ended_at is None
    assert run.events[0].operation == "other"
    assert "SECRET-CANDIDATE" not in run.model_dump_json()


def test_project_trace_keeps_waiting_state_across_non_transition_events() -> None:
    question_wait = project_trace(
        [
            _event("learning.question_asked", 0, parent_span_id="assessment"),
            _event("learning.evidence_revealed", 1, parent_span_id="assessment"),
        ],
        trace_id="trace-safe",
    )
    approval_wait = project_trace(
        [
            _event("approval.requested", 0),
            _event("hook.invoked", 1),
        ],
        trace_id="trace-safe",
    )

    assert question_wait.status == "waiting_input"
    assert approval_wait.status == "waiting_input"


def test_project_trace_latest_lifecycle_transition_wins() -> None:
    reopened = project_trace(
        [
            _event(
                "assessment.ended",
                0,
                span_id="first-assessment",
                payload={"ok": True},
            ),
            _event("assessment.started", 1, span_id="second-assessment"),
        ],
        trace_id="trace-safe",
    )
    grading = project_trace(
        [
            _event("learning.question_asked", 0, parent_span_id="assessment"),
            _event("learning.answer_judged", 1, parent_span_id="assessment"),
        ],
        trace_id="trace-safe",
    )
    completed_with_audit_tail = project_trace(
        [
            _event("acquisition.succeeded", 0, payload={"status": "completed"}),
            _event("learning.knowledge_classified", 1),
        ],
        trace_id="trace-safe",
    )

    assert reopened.status == "running"
    assert reopened.ended_at is None
    assert grading.status == "running"
    assert completed_with_audit_tail.status == "completed"

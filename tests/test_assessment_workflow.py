"""Assessment 领域 descriptor、event conformance 与安全运行着色契约。"""

from grandquiz.domain.learning.assessment.workflow import (
    AWAIT_ANSWER,
    COMMIT_LEARNING,
    GENERATE_QUESTION,
    GRADE_ANSWER,
    JUDGE_DISTRACTORS,
    SELECT_TARGET,
    VALIDATE_EVIDENCE,
    describe_assessment_workflow,
)
from grandquiz.interfaces.trace_projection import (
    project_trace,
    resolve_assessment_workflow_descriptor,
)
from grandquiz.kernel.events import AgentEvent


def _event(
    event_type: str,
    seq: int,
    *,
    node_id: str | None = None,
    payload: dict[str, object] | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
) -> AgentEvent:
    body = dict(payload or {})
    if node_id is not None:
        body["node_id"] = node_id
    return AgentEvent(
        type=event_type,
        seq=seq,
        ts=float(seq),
        trace_id="workflow-trace",
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload=body,
    )


def test_domain_descriptor_has_closed_edges_and_no_fake_mc_node_for_open_questions() -> None:
    opened = describe_assessment_workflow("open")
    multiple_choice = describe_assessment_workflow("multiple_choice")

    open_ids = [node.node_id for node in opened.nodes]
    mc_ids = [node.node_id for node in multiple_choice.nodes]
    assert open_ids == [
        SELECT_TARGET,
        GENERATE_QUESTION,
        VALIDATE_EVIDENCE,
        AWAIT_ANSWER,
        GRADE_ANSWER,
        COMMIT_LEARNING,
    ]
    assert mc_ids == [
        SELECT_TARGET,
        GENERATE_QUESTION,
        VALIDATE_EVIDENCE,
        JUDGE_DISTRACTORS,
        AWAIT_ANSWER,
        GRADE_ANSWER,
        COMMIT_LEARNING,
    ]
    assert next(
        node for node in multiple_choice.nodes if node.node_id == JUDGE_DISTRACTORS
    ).optional
    for descriptor in (opened, multiple_choice):
        node_ids = {node.node_id for node in descriptor.nodes}
        assert len(node_ids) == len(descriptor.nodes)
        assert all(edge.source in node_ids and edge.target in node_ids for edge in descriptor.edges)
    assert isinstance(opened.nodes, tuple)
    assert isinstance(opened.edges, tuple)


def test_descriptor_resolver_uses_structured_assessment_events_and_survives_restart() -> None:
    open_events = [
        _event("assessment.started", 0, node_id=SELECT_TARGET),
        _event(
            "learning.question_asked",
            1,
            node_id=AWAIT_ANSWER,
            payload={"effective": "开放"},
        ),
    ]
    mc_events = [
        _event("web.assessment_run.started", 0, node_id=SELECT_TARGET),
        _event(
            "learning.multiple_choice_generation.started",
            1,
            node_id=GENERATE_QUESTION,
        ),
    ]

    open_descriptor = resolve_assessment_workflow_descriptor(open_events)
    mc_descriptor = resolve_assessment_workflow_descriptor(mc_events)
    assert open_descriptor is not None
    assert mc_descriptor is not None
    assert open_descriptor.variant == "open"
    assert mc_descriptor.variant == "multiple_choice"
    assert resolve_assessment_workflow_descriptor([_event("agent_turn.started", 0)]) is None


def test_projector_colors_waiting_open_workflow_without_distractor_node() -> None:
    descriptor = describe_assessment_workflow("open")
    run = project_trace(
        [
            _event("assessment.started", 0, node_id=SELECT_TARGET, span_id="assessment"),
            _event("model.started", 1, node_id=GENERATE_QUESTION, span_id="generation"),
            _event(
                "model.ended",
                2,
                node_id=GENERATE_QUESTION,
                span_id="generation",
                payload={"ok": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            ),
            _event(
                "learning.question_asked",
                3,
                node_id=AWAIT_ANSWER,
                payload={"effective": "开放"},
            ),
        ],
        trace_id="workflow-trace",
        descriptor=descriptor,
    )

    assert run.workflow is not None
    assert run.workflow.variant == "open"
    assert [node.node_id for node in run.workflow.nodes] == [
        SELECT_TARGET,
        GENERATE_QUESTION,
        VALIDATE_EVIDENCE,
        AWAIT_ANSWER,
        GRADE_ANSWER,
        COMMIT_LEARNING,
    ]
    states = {node.node_id: node.state for node in run.workflow.nodes}
    assert states == {
        SELECT_TARGET: "completed",
        GENERATE_QUESTION: "completed",
        VALIDATE_EVIDENCE: "completed",
        AWAIT_ANSWER: "waiting",
        GRADE_ANSWER: "pending",
        COMMIT_LEARNING: "pending",
    }
    generate = next(node for node in run.workflow.nodes if node.node_id == GENERATE_QUESTION)
    assert generate.attempts == 1
    assert generate.latency_ms == 1000.0


def test_projector_marks_exact_failed_node_and_preserves_optional_unvisited_judge() -> None:
    descriptor = describe_assessment_workflow("multiple_choice")
    run = project_trace(
        [
            _event("assessment.started", 0, node_id=SELECT_TARGET),
            _event(
                "learning.multiple_choice_generation.started",
                1,
                node_id=GENERATE_QUESTION,
                span_id="generation",
            ),
            _event(
                "learning.multiple_choice_generation.attempt_rejected",
                2,
                node_id=VALIDATE_EVIDENCE,
                payload={"attempts": 2, "stage": "validation", "reason_code": "ghost_evidence"},
            ),
            _event(
                "web.assessment_run.degraded",
                3,
                node_id=VALIDATE_EVIDENCE,
                payload={
                    "status": "degraded",
                    "stage": "question_generation",
                    "reason_code": "question_generation_exhausted",
                },
            ),
        ],
        trace_id="workflow-trace",
        descriptor=descriptor,
    )

    assert run.workflow is not None
    nodes = {node.node_id: node for node in run.workflow.nodes}
    assert nodes[SELECT_TARGET].state == "completed"
    assert nodes[GENERATE_QUESTION].state == "completed"
    assert nodes[GENERATE_QUESTION].attempts == 2
    assert nodes[VALIDATE_EVIDENCE].state == "failed"
    assert nodes[VALIDATE_EVIDENCE].attempts is None
    assert nodes[JUDGE_DISTRACTORS].state == "pending"


def test_projector_completes_real_path_and_fails_closed_for_unknown_node_id() -> None:
    descriptor = describe_assessment_workflow("multiple_choice")
    run = project_trace(
        [
            _event("assessment.started", 0, node_id=SELECT_TARGET),
            _event("model.started", 1, node_id=GENERATE_QUESTION, span_id="generation"),
            _event(
                "model.ended",
                2,
                node_id=GENERATE_QUESTION,
                span_id="generation",
                payload={"ok": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            ),
            _event("model.started", 3, node_id=JUDGE_DISTRACTORS, span_id="judge"),
            _event(
                "model.ended",
                4,
                node_id=JUDGE_DISTRACTORS,
                span_id="judge",
                payload={"ok": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            ),
            _event("learning.question_asked", 5, node_id=AWAIT_ANSWER),
            _event("learning.answer_judged", 6, node_id=GRADE_ANSWER),
            _event("learning.assessment_judgement_committed", 7, node_id=COMMIT_LEARNING),
            _event("plugin.future.node", 8, node_id="private_future_node"),
            _event(
                "web.assessment_run.ended",
                9,
                node_id=COMMIT_LEARNING,
                payload={"status": "completed", "ok": True},
            ),
        ],
        trace_id="workflow-trace",
        descriptor=descriptor,
    )

    assert run.workflow is not None
    states = {node.node_id: node.state for node in run.workflow.nodes}
    assert states == {
        SELECT_TARGET: "completed",
        GENERATE_QUESTION: "completed",
        VALIDATE_EVIDENCE: "completed",
        JUDGE_DISTRACTORS: "completed",
        AWAIT_ANSWER: "completed",
        GRADE_ANSWER: "completed",
        COMMIT_LEARNING: "completed",
    }
    assert run.events[-2].node_id is None
    assert "private_future_node" not in run.model_dump_json()


def test_projector_does_not_assign_a_cross_node_span_latency_to_validation() -> None:
    descriptor = describe_assessment_workflow("multiple_choice")
    run = project_trace(
        [
            _event(
                "learning.multiple_choice_generation.started",
                0,
                node_id=GENERATE_QUESTION,
                span_id="generation",
            ),
            _event(
                "model.started",
                1,
                node_id=GENERATE_QUESTION,
                span_id="model-1",
                parent_span_id="generation",
                payload={},
            ),
            _event(
                "model.ended",
                2,
                node_id=GENERATE_QUESTION,
                span_id="model-1",
                parent_span_id="generation",
                payload={"ok": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            ),
            _event(
                "learning.multiple_choice_generation.ended",
                3,
                node_id=VALIDATE_EVIDENCE,
                span_id="generation",
                payload={"ok": True, "attempts": 1},
            ),
        ],
        trace_id="workflow-trace",
        descriptor=descriptor,
    )

    assert run.workflow is not None
    nodes = {node.node_id: node for node in run.workflow.nodes}
    assert nodes[GENERATE_QUESTION].attempts == 1
    assert nodes[GENERATE_QUESTION].latency_ms == 1000.0
    assert nodes[VALIDATE_EVIDENCE].latency_ms is None


def test_latest_round_replaces_historical_mc_descriptor_and_node_state() -> None:
    events = [
        _event("assessment.started", 0, node_id=SELECT_TARGET, span_id="round-1"),
        _event(
            "learning.multiple_choice_generation.started",
            1,
            node_id=GENERATE_QUESTION,
            span_id="mc-1",
            parent_span_id="round-1",
        ),
        _event(
            "learning.question_asked",
            2,
            node_id=AWAIT_ANSWER,
            parent_span_id="round-1",
            payload={"effective": "选择题"},
        ),
        _event("learning.answer_judged", 3, node_id=GRADE_ANSWER),
        _event("learning.assessment_judgement_committed", 4, node_id=COMMIT_LEARNING),
        _event("assessment.started", 5, node_id=SELECT_TARGET, span_id="round-2"),
        _event(
            "model.started",
            6,
            node_id=GENERATE_QUESTION,
            span_id="open-model",
            parent_span_id="round-2",
        ),
        _event(
            "model.ended",
            7,
            node_id=GENERATE_QUESTION,
            span_id="open-model",
            parent_span_id="round-2",
            payload={"ok": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        ),
        _event(
            "learning.question_asked",
            8,
            node_id=AWAIT_ANSWER,
            parent_span_id="round-2",
            payload={"effective": "开放"},
        ),
    ]

    descriptor = resolve_assessment_workflow_descriptor(events)
    assert descriptor is not None
    assert descriptor.variant == "open"
    run = project_trace(events, trace_id="workflow-trace", descriptor=descriptor)

    assert run.workflow is not None
    states = {node.node_id: node.state for node in run.workflow.nodes}
    assert JUDGE_DISTRACTORS not in states
    assert states[AWAIT_ANSWER] == "waiting"
    assert states[GRADE_ANSWER] == "pending"
    assert states[COMMIT_LEARNING] == "pending"


def test_same_node_nested_spans_count_only_leaf_latency_and_real_attempts() -> None:
    descriptor = describe_assessment_workflow("multiple_choice")
    run = project_trace(
        [
            _event(
                "learning.multiple_choice_generation.started",
                0,
                node_id=GENERATE_QUESTION,
                span_id="generation",
            ),
            _event(
                "model.started",
                1,
                node_id=GENERATE_QUESTION,
                span_id="model",
                parent_span_id="generation",
            ),
            _event(
                "model.ended",
                3,
                node_id=GENERATE_QUESTION,
                span_id="model",
                parent_span_id="generation",
                payload={"ok": False},
            ),
            _event(
                "learning.multiple_choice_generation.ended",
                4,
                node_id=GENERATE_QUESTION,
                span_id="generation",
                payload={"ok": False, "attempts": 1, "stage": "model_call"},
            ),
        ],
        trace_id="workflow-trace",
        descriptor=descriptor,
    )

    assert run.workflow is not None
    generate = next(node for node in run.workflow.nodes if node.node_id == GENERATE_QUESTION)
    assert generate.attempts == 1
    assert generate.latency_ms == 2000.0


def test_unknown_event_cannot_forge_a_valid_workflow_node() -> None:
    descriptor = describe_assessment_workflow("multiple_choice")
    run = project_trace(
        [_event("plugin.future.event", 0, node_id=VALIDATE_EVIDENCE)],
        trace_id="workflow-trace",
        descriptor=descriptor,
    )

    assert run.events[0].node_id is None
    assert run.workflow is not None
    assert all(node.state == "pending" for node in run.workflow.nodes)

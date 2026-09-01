"""完整 AgentEvent trace 的安全、版本化语义投影。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel

from grandquiz.kernel.events import AgentEvent, EventType

TraceRunStatus = Literal[
    "idle",
    "running",
    "waiting_input",
    "completed",
    "failed",
    "cancelled",
]
TraceOperation = Literal[
    "assessment_run",
    "multiple_choice_generation",
    "distractor_judgement",
    "grading",
    "learning_commit",
    "other",
]
TracePhase = Literal["started", "attempt_rejected", "ended", "waiting_input", "event"]
TraceEventStatus = Literal["running", "waiting_input", "completed", "failed", "event"]
TraceStage = Literal[
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
TraceReasonCode = Literal[
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
TraceQualityLabel = Literal["invalid", "weak", "reasonable"]

_MC_STARTED = "learning.multiple_choice_generation.started"
_MC_REJECTED = "learning.multiple_choice_generation.attempt_rejected"
_MC_ENDED = "learning.multiple_choice_generation.ended"
_ASSESSMENT_EVENTS = frozenset(
    {
        "assessment.started",
        "assessment.ended",
        "web.assessment_run.started",
        "web.assessment_run.ended",
        "web.assessment_run.degraded",
        "learning.question_asked",
    }
)
_LEARNING_COMMIT_EVENTS = frozenset(
    {
        "learning.assessment_judgement_committed",
        "learning.concept_state_changed",
        "learning.difficulty_tier_changed",
    }
)
_PUBLIC_STAGES = frozenset(TraceStage.__args__)
_PUBLIC_REASONS = frozenset(TraceReasonCode.__args__)
_REASON_LABELS: Mapping[TraceReasonCode, str] = {
    "invalid_json": "输出格式无效",
    "schema_invalid": "输出结构无效",
    "option_count_invalid": "选项数量不符",
    "answer_index_invalid": "答案位置无效",
    "duplicate_options": "选项重复",
    "meta_option": "出现元选项",
    "length_outlier": "选项长度异常",
    "evidence_missing": "缺少材料证据",
    "ghost_evidence": "证据无法定位",
    "question_repeated": "题目重复",
    "option_count_unmet": "选项数量不足",
    "distractor_quality_unmet": "干扰项质量不足",
    "repair_contract_violated": "修复结果不合约",
    "question_generation_exhausted": "出题尝试已耗尽",
    "grading_exhausted": "判卷尝试已耗尽",
    "workflow_degraded": "考核流程降级",
    "other": "其他公开原因",
}


class TraceRejectionCountV1(BaseModel):
    reason_code: TraceReasonCode
    count: int


class SafeTraceSummaryV1(BaseModel):
    model_calls: int
    retries: int
    rejection_counts: list[TraceRejectionCountV1]
    error_count: int
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float | None
    headline: str | None = None
    recommended_action: str | None = None


class SafeTraceEventV1(BaseModel):
    sequence: int
    timestamp: float
    span_id: str | None
    parent_span_id: str | None
    operation: TraceOperation
    phase: TracePhase
    status: TraceEventStatus
    attempt: int | None = None
    stage: TraceStage | None = None
    reason_code: TraceReasonCode | None = None
    quality_label: TraceQualityLabel | None = None
    tokens: int | None = None
    latency_ms: float | None = None


class SafeTraceRunV1(BaseModel):
    schema_version: Literal[1] = 1
    trace_id: str
    status: TraceRunStatus
    started_at: float | None
    ended_at: float | None
    workflow_kind: Literal["assessment"] | None
    summary: SafeTraceSummaryV1
    events: list[SafeTraceEventV1]


def project_trace(events: Sequence[AgentEvent], *, trace_id: str) -> SafeTraceRunV1:
    """从完整 trace 构造新的 allowlist 对象；从不复制 raw payload。"""
    if any(event.trace_id != trace_id for event in events):
        raise ValueError("events 必须全部属于指定 trace_id")

    projected = _project_events(events)
    status = _trace_status(events)
    reason_counts: Counter[TraceReasonCode] = Counter()
    for event in projected:
        if event.phase == "attempt_rejected" and event.reason_code is not None:
            reason_counts[event.reason_code] += 1
    model_calls = sum(event.type == EventType.MODEL_STARTED for event in events)
    prompt_tokens, completion_tokens = _summary_usage(events)
    started_at = events[0].ts if events else None
    ended_at = events[-1].ts if events and status in {"completed", "failed", "cancelled"} else None
    latency_ms = max(0.0, (events[-1].ts - events[0].ts) * 1000) if len(events) >= 2 else None
    headline, recommended_action = _summary_explanation(
        projected,
        status=status,
        error_count=sum(event.type == EventType.ERROR for event in events),
    )
    return SafeTraceRunV1(
        trace_id=trace_id,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        workflow_kind=(
            "assessment" if any(event.operation != "other" for event in projected) else None
        ),
        summary=SafeTraceSummaryV1(
            model_calls=model_calls,
            retries=sum(event.phase == "attempt_rejected" for event in projected),
            rejection_counts=[
                TraceRejectionCountV1(reason_code=reason, count=count)
                for reason, count in sorted(reason_counts.items())
            ],
            error_count=sum(event.type == EventType.ERROR for event in events),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            headline=headline,
            recommended_action=recommended_action,
        ),
        events=projected,
    )


def _summary_explanation(
    events: Sequence[SafeTraceEventV1],
    *,
    status: TraceRunStatus,
    error_count: int,
) -> tuple[str | None, str | None]:
    """只从安全枚举与计数生成文案，不读取 raw payload 或异常正文。"""
    if status == "failed":
        suffix = f"；记录到 {error_count} 个错误" if error_count else ""
        return f"运行失败{suffix}", "请查看失败阶段与原因；可以结束本轮后重试。"
    if status == "cancelled":
        return "运行已取消", "可以在准备好后重新开始。"
    if status == "completed":
        return "运行已完成", None
    if status == "running":
        return "运行正在进行", None
    if status == "idle":
        return None, None

    generation_slice = _latest_question_generation_failure_slice(events)
    question_generation_failed = generation_slice is not None
    current_generation = generation_slice or ()
    reasons: Counter[TraceReasonCode] = Counter(
        event.reason_code
        for event in current_generation
        if event.phase == "attempt_rejected" and event.reason_code is not None
    )
    attempt_values = [event.attempt for event in current_generation if event.attempt is not None]
    attempts = max(attempt_values) if attempt_values else None
    is_multiple_choice = any(
        event.operation == "multiple_choice_generation" for event in current_generation
    )
    latest_waiting = next(
        (event for event in reversed(events) if event.status == "waiting_input"),
        None,
    )
    grading_failed = latest_waiting is not None and (
        latest_waiting.reason_code == "grading_exhausted"
        or (latest_waiting.operation == "assessment_run" and latest_waiting.stage == "grading")
    )

    if question_generation_failed:
        headline = "选择题生成失败" if is_multiple_choice else "题目生成失败"
        if attempts is not None:
            headline += f"：{attempts} 次尝试"
        parts = [headline]
        parts.extend(
            f"{_REASON_LABELS[reason]} {count} 次"
            for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
        )
        return "；".join(parts), "可以重试本题，或跳过此题继续。"
    if grading_failed:
        return "判卷未完成", "可以跳过此题继续考核。"
    return "考核正在等待输入", "请提交答案或完成当前审批后继续。"


def _latest_question_generation_failure_slice(
    events: Sequence[SafeTraceEventV1],
) -> Sequence[SafeTraceEventV1] | None:
    """把 headline 归属到当前题，避免早期题型和 attempt 污染多轮考核。"""
    failure_index: int | None = None
    for index, event in enumerate(events):
        if event.reason_code == "question_generation_exhausted" or (
            event.operation == "assessment_run"
            and event.stage == "question_generation"
            and event.status in {"failed", "waiting_input"}
        ):
            failure_index = index
    if failure_index is None:
        return None

    latest_waiting_index = max(
        (index for index, event in enumerate(events) if event.status == "waiting_input"),
        default=-1,
    )
    if latest_waiting_index != failure_index:
        return None

    boundary = -1
    for index, event in enumerate(events[:failure_index]):
        if event.operation == "assessment_run" and event.status == "waiting_input":
            boundary = index
    return events[boundary + 1 : failure_index + 1]


def _project_events(events: Sequence[AgentEvent]) -> list[SafeTraceEventV1]:
    starts: dict[str, float] = {}
    span_operations: dict[str, TraceOperation] = {}
    question_asked_spans: set[str] = set()
    projected: list[SafeTraceEventV1] = []
    for event in events:
        if event.span_id is not None and event.type.endswith(".started"):
            starts[event.span_id] = event.ts
        operation = _operation(
            event,
            span_operations=span_operations,
            question_asked_spans=question_asked_spans,
        )
        if event.span_id is not None and event.type.endswith(".started"):
            span_operations[event.span_id] = operation
        if event.type == "learning.question_asked" and event.parent_span_id is not None:
            question_asked_spans.add(event.parent_span_id)
        phase = _phase(event)
        start_ts = starts.get(event.span_id) if event.span_id is not None else None
        projected.append(
            SafeTraceEventV1(
                sequence=event.seq + 1,
                timestamp=event.ts,
                span_id=event.span_id,
                parent_span_id=event.parent_span_id,
                operation=operation,
                phase=phase,
                status=_event_status(event, phase),
                attempt=_safe_int(event.payload, "attempt") if operation != "other" else None,
                stage=_stage(event.payload) if operation != "other" else None,
                reason_code=_reason(event.payload) if operation != "other" else None,
                tokens=_usage_total(event.payload),
                latency_ms=(
                    max(0.0, (event.ts - start_ts) * 1000)
                    if start_ts is not None and event.type.endswith(".ended")
                    else None
                ),
            )
        )
    return projected


def _operation(
    event: AgentEvent,
    *,
    span_operations: Mapping[str, TraceOperation],
    question_asked_spans: set[str],
) -> TraceOperation:
    event_type = event.type
    if event_type in {_MC_STARTED, _MC_REJECTED, _MC_ENDED}:
        return "multiple_choice_generation"
    if event_type in _ASSESSMENT_EVENTS:
        return "assessment_run"
    if event_type == "learning.answer_judged":
        return "grading"
    if event_type in _LEARNING_COMMIT_EVENTS:
        return "learning_commit"
    if event_type == EventType.MODEL_ENDED and event.span_id is not None:
        return span_operations.get(event.span_id, "other")
    if event_type == EventType.MODEL_STARTED:
        parent_operation = span_operations.get(event.parent_span_id or "")
        if parent_operation == "multiple_choice_generation":
            return (
                "distractor_judgement"
                if event.payload.get("role") == "basic"
                else "multiple_choice_generation"
            )
        if parent_operation == "assessment_run" and event.parent_span_id in question_asked_spans:
            return "grading"
    return "other"


def _summary_usage(events: Sequence[AgentEvent]) -> tuple[int | None, int | None]:
    starts = [event for event in events if event.type == EventType.MODEL_STARTED]
    if not starts:
        return 0, 0
    ended_by_span = {
        event.span_id: event
        for event in events
        if event.type == EventType.MODEL_ENDED and event.span_id is not None
    }
    prompt_tokens = 0
    completion_tokens = 0
    for started in starts:
        if started.span_id is None:
            return None, None
        ended = ended_by_span.get(started.span_id)
        if ended is None:
            return None, None
        usage_obj = ended.payload.get("usage")
        if not isinstance(usage_obj, Mapping):
            return None, None
        usage = cast("Mapping[str, Any]", usage_obj)
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if (
            not isinstance(prompt, int)
            or isinstance(prompt, bool)
            or not isinstance(completion, int)
            or isinstance(completion, bool)
        ):
            return None, None
        prompt_tokens += prompt
        completion_tokens += completion
    return prompt_tokens, completion_tokens


def _phase(event: AgentEvent) -> TracePhase:
    if event.type == _MC_REJECTED:
        return "attempt_rejected"
    if event.payload.get("status") == "degraded" or event.type in {
        "learning.question_asked",
        "approval.requested",
        "voice.reviewable",
    }:
        return "waiting_input"
    if event.type.endswith(".started"):
        return "started"
    if event.type.endswith(".ended"):
        return "ended"
    return "event"


def _event_status(event: AgentEvent, phase: TracePhase) -> TraceEventStatus:
    if event.type == EventType.ERROR or event.payload.get("ok") is False:
        return "failed"
    if event.payload.get("status") in {"failed", "cancelled"}:
        return "failed"
    if phase == "waiting_input":
        return "waiting_input"
    if phase == "started":
        return "running"
    if phase == "ended":
        return "completed"
    return "event"


def _stage(payload: Mapping[str, Any]) -> TraceStage | None:
    value = payload.get("stage")
    if not isinstance(value, str):
        return None
    return cast("TraceStage", value if value in _PUBLIC_STAGES else "other")


def _reason(payload: Mapping[str, Any]) -> TraceReasonCode | None:
    value = payload.get("reason_code")
    if not isinstance(value, str):
        return None
    return cast("TraceReasonCode", value if value in _PUBLIC_REASONS else "other")


def _safe_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_total(payload: Mapping[str, Any]) -> int | None:
    usage_obj = payload.get("usage")
    if not isinstance(usage_obj, Mapping):
        return None
    usage = cast("Mapping[str, Any]", usage_obj)
    total = usage.get("total_tokens")
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def _trace_status(events: Sequence[AgentEvent]) -> TraceRunStatus:
    if not events:
        return "idle"
    state: TraceRunStatus = "running"
    for event in events:
        payload_status = event.payload.get("status")
        if payload_status == "cancelled":
            state = "cancelled"
        elif payload_status == "degraded" or event.type in {
            "learning.question_asked",
            "approval.requested",
            "voice.reviewable",
        }:
            state = "waiting_input"
        elif payload_status == "failed":
            state = "failed"
        elif payload_status == "completed":
            state = "completed"
        elif event.type in {
            EventType.AGENT_TURN_ENDED,
            EventType.TURN_ENDED,
            "assessment.ended",
        } or event.type.endswith("run.ended"):
            state = "failed" if event.payload.get("ok") is False else "completed"
        elif (
            payload_status == "running"
            or event.type.endswith(".started")
            or event.type == "learning.answer_judged"
            or event.type == _MC_REJECTED
            or event.type in _LEARNING_COMMIT_EVENTS
        ):
            state = "running"
    return state

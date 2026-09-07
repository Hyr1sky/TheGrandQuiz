"""One application-owned execution path for model calls, retries, and fallback.

The logical ``model`` span remains the business-visible call. When a bound model
has a retry runtime, each transport request and wait is exposed as a child span;
legacy injected models keep their original single-span event shape.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence

from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.kernel.model_events import (
    model_failure_event_payload,
    model_identity_event_payload,
)
from grandquiz.providers.base import (
    Completion,
    Message,
    Model,
    ProviderStreamProtocolError,
    StreamingModel,
    TextDelta,
    ToolSpec,
)
from grandquiz.providers.failure import (
    ProviderFailure,
    ProviderFailureCategory,
    provider_failure_payload,
)
from grandquiz.providers.fallback import ProviderFallbackDecision
from grandquiz.providers.models import (
    ModelExecutionCandidate,
    ModelFallbackPlan,
    fallback_plan_of,
    retry_runtime_of,
)
from grandquiz.providers.retry import ProviderRetryDecision, RetryRuntime

TextDeltaObserver = Callable[[TextDelta, str], None]
StreamFinishedObserver = Callable[[str], None]


def _usage_payload(completion: Completion, *, include_total: bool = True) -> dict[str, int]:
    return completion.usage.model_dump(
        mode="json",
        exclude=set() if include_total else {"total_tokens"},
    )


def _success_payload(completion: Completion, **context: object) -> dict[str, object]:
    payload: dict[str, object] = {
        **context,
        "ok": True,
        "output": completion.text,
        "usage": _usage_payload(completion),
    }
    if completion.tool_calls is not None:
        payload["tool_calls"] = [call.model_dump(mode="json") for call in completion.tool_calls]
    return payload


def _retry_policy_payload(runtime: RetryRuntime) -> dict[str, object]:
    policy = runtime.policy
    return {
        "retry_policy": {
            "enabled": policy.enabled,
            "max_attempts": policy.max_attempts,
            "deadline_seconds": policy.deadline_seconds,
            "max_total_wait_seconds": policy.max_total_wait_seconds,
            "fingerprint": policy.fingerprint,
        }
    }


def _attempt_failure_payload(
    exc: BaseException,
    *,
    attempt_index: int,
    output_delivered: bool,
) -> dict[str, object]:
    return {
        "ok": False,
        "attempt_index": attempt_index,
        "usage_status": "unknown",
        "output_delivered": output_delivered,
        "error": repr(exc),
        **provider_failure_payload(exc),
    }


def _decision_payload(
    decision: ProviderRetryDecision,
    failure: ProviderFailure,
    *,
    attempt_index: int,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "attempt_index": attempt_index,
        "action": decision.action,
        "reason": decision.reason,
        "provider_failure_category": failure.category.value,
    }
    if decision.delay_seconds is not None:
        payload["delay_seconds"] = decision.delay_seconds
    return payload


def _timeout_failure() -> ProviderFailure:
    return ProviderFailure(
        category=ProviderFailureCategory.TIMEOUT,
        retryable=True,
        replay_safe=True,
    )


async def _within_deadline[T](
    operation: Callable[[], Awaitable[T]],
    *,
    runtime: RetryRuntime,
    started_at: float,
) -> T:
    remaining = runtime.policy.deadline_seconds - (runtime.clock.monotonic() - started_at)
    if remaining <= 0:
        raise _timeout_failure()
    try:
        async with asyncio.timeout(remaining):
            return await operation()
    except TimeoutError as exc:
        raise _timeout_failure() from exc


async def complete_model_call(
    *,
    model: Model,
    messages: Sequence[Message],
    emitter: EventEmitter,
    parent_span_id: str | None = None,
    prompt_version: str | None = None,
    tools: Sequence[ToolSpec] | None = None,
    context: Mapping[str, object] | None = None,
    emit_error_event: bool = False,
) -> Completion:
    """Execute one logical completion with bounded, observable transport attempts."""

    runtime = retry_runtime_of(model)
    fallback_plan = fallback_plan_of(model)
    model_span = _start_logical_call(
        model=model,
        messages=messages,
        emitter=emitter,
        parent_span_id=parent_span_id,
        prompt_version=prompt_version,
        context=context,
        runtime=runtime,
        fallback_plan=fallback_plan,
    )
    if runtime is None:
        try:
            completion = await model.complete(messages, tools=tools)
        except asyncio.CancelledError:
            _end_cancelled(emitter, model_span, parent_span_id)
            raise
        except Exception as exc:
            _end_failed(
                emitter,
                model_span,
                parent_span_id,
                exc,
                context=context,
                emit_error_event=emit_error_event,
            )
            raise
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=model_span,
            parent_span_id=parent_span_id,
            payload=_success_payload(completion, **dict(context or {})),
        )
        return completion

    return await _complete_with_retry(
        model=model,
        messages=messages,
        tools=tools,
        emitter=emitter,
        model_span=model_span,
        parent_span_id=parent_span_id,
        context=context,
        emit_error_event=emit_error_event,
        runtime=runtime,
        fallback_plan=fallback_plan,
    )


async def stream_model_call(
    *,
    model: Model,
    messages: Sequence[Message],
    emitter: EventEmitter,
    on_text_delta: TextDeltaObserver,
    parent_span_id: str | None = None,
    prompt_version: str | None = None,
    tools: Sequence[ToolSpec] | None = None,
    context: Mapping[str, object] | None = None,
    emit_error_event: bool = False,
    on_stream_finished: StreamFinishedObserver | None = None,
) -> Completion:
    """Execute a normalized stream; never replay after an upstream stream event."""

    if not isinstance(model, StreamingModel):
        raise TypeError("stream_model_call requires a StreamingModel")
    runtime = retry_runtime_of(model)
    fallback_plan = fallback_plan_of(model)
    model_span = _start_logical_call(
        model=model,
        messages=messages,
        emitter=emitter,
        parent_span_id=parent_span_id,
        prompt_version=prompt_version,
        context=context,
        runtime=runtime,
        fallback_plan=fallback_plan,
    )
    if runtime is None:
        try:
            completion, _ = await _consume_stream(
                model=model,
                messages=messages,
                tools=tools,
                model_span=model_span,
                on_text_delta=on_text_delta,
                on_stream_finished=on_stream_finished,
            )
        except asyncio.CancelledError:
            _end_cancelled(emitter, model_span, parent_span_id)
            raise
        except Exception as exc:
            _end_failed(
                emitter,
                model_span,
                parent_span_id,
                exc,
                context=context,
                emit_error_event=emit_error_event,
            )
            raise
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=model_span,
            parent_span_id=parent_span_id,
            payload=_success_payload(completion, **dict(context or {})),
        )
        return completion

    return await _stream_with_retry(
        model=model,
        messages=messages,
        tools=tools,
        emitter=emitter,
        model_span=model_span,
        parent_span_id=parent_span_id,
        context=context,
        emit_error_event=emit_error_event,
        on_text_delta=on_text_delta,
        on_stream_finished=on_stream_finished,
        runtime=runtime,
        fallback_plan=fallback_plan,
    )


def _start_logical_call(
    *,
    model: Model,
    messages: Sequence[Message],
    emitter: EventEmitter,
    parent_span_id: str | None,
    prompt_version: str | None,
    context: Mapping[str, object] | None,
    runtime: RetryRuntime | None,
    fallback_plan: ModelFallbackPlan | None,
) -> str:
    span_id = emitter.new_span_id()
    payload: dict[str, object] = {
        **dict(context or {}),
        "messages": [message.model_dump(mode="json") for message in messages],
        "prompt_version": prompt_version,
        **model_identity_event_payload(model),
    }
    if runtime is not None:
        payload.update(_retry_policy_payload(runtime))
    if fallback_plan is not None:
        payload["fallback_policy"] = {
            "enabled": True,
            "candidate_count": len(fallback_plan.candidates),
            "max_attempts_per_candidate": fallback_plan.policy.max_attempts_per_candidate,
            "fingerprint": fallback_plan.policy.fingerprint,
        }
    emitter.emit(
        EventType.MODEL_STARTED,
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload=payload,
    )
    return span_id


async def _complete_with_retry(
    *,
    model: Model,
    messages: Sequence[Message],
    tools: Sequence[ToolSpec] | None,
    emitter: EventEmitter,
    model_span: str,
    parent_span_id: str | None,
    context: Mapping[str, object] | None,
    emit_error_event: bool,
    runtime: RetryRuntime,
    fallback_plan: ModelFallbackPlan | None,
) -> Completion:
    started_at = runtime.clock.monotonic()
    total_wait = 0.0
    attempt_index = 0
    candidate_index = 0
    candidate_attempt = 0
    fallback_count = 0
    failure_chain: list[dict[str, object]] = []
    while True:
        attempt_index += 1
        candidate_attempt += 1
        candidate = _candidate_for(fallback_plan, candidate_index)
        attempt_span = _start_attempt(
            emitter,
            model_span,
            attempt_index,
            candidate=candidate if fallback_plan is not None else None,
            candidate_index=candidate_index,
            candidate_attempt=candidate_attempt,
        )
        candidate_model = candidate.model if candidate is not None else model
        try:
            completion = await _within_deadline(
                lambda candidate_model=candidate_model: candidate_model.complete(
                    messages, tools=tools
                ),
                runtime=runtime,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            _end_attempt_cancelled(
                emitter,
                attempt_span,
                model_span,
                attempt_index,
                candidate=candidate if fallback_plan is not None else None,
                candidate_index=candidate_index,
                candidate_attempt=candidate_attempt,
            )
            _end_cancelled(emitter, model_span, parent_span_id)
            raise
        except Exception as exc:
            _end_attempt_failed(
                emitter,
                attempt_span,
                model_span,
                exc,
                attempt_index,
                False,
                candidate=candidate if fallback_plan is not None else None,
                candidate_index=candidate_index,
                candidate_attempt=candidate_attempt,
            )
            if not isinstance(exc, ProviderFailure):
                _end_failed(
                    emitter,
                    model_span,
                    parent_span_id,
                    exc,
                    context=context,
                    emit_error_event=emit_error_event,
                    attempt_count=attempt_index,
                    total_wait_seconds=total_wait,
                    fallback_count=(fallback_count if fallback_plan is not None else None),
                    failure_chain=failure_chain,
                )
                raise
            if fallback_plan is not None:
                failure_chain.append(
                    _failure_chain_item(
                        exc,
                        attempt_index=attempt_index,
                        candidate_index=candidate_index,
                        candidate_attempt=candidate_attempt,
                        candidate=candidate,
                    )
                )
            decision = _decide(
                runtime,
                exc,
                attempt_index=candidate_attempt,
                total_attempt_index=attempt_index,
                started_at=started_at,
                total_wait=total_wait,
                output_delivered=False,
            )
            fallback_decision = _fallback_after_failure(
                plan=fallback_plan,
                failure=exc,
                retry_decision=decision,
                runtime=runtime,
                started_at=started_at,
                total_attempt_index=attempt_index,
                candidate_index=candidate_index,
                candidate_attempt=candidate_attempt,
                tools=tools,
                streaming=False,
                output_delivered=False,
            )
            if fallback_decision is not None:
                fallback_result, next_candidate = fallback_decision
                _emit_fallback_decision(
                    emitter,
                    attempt_span,
                    model_span,
                    fallback_result,
                    attempt_index=attempt_index,
                    candidate_index=candidate_index,
                    candidate_attempt=candidate_attempt,
                    candidate=candidate,
                    next_candidate=(
                        None
                        if next_candidate is None or fallback_plan is None
                        else fallback_plan.candidates[next_candidate]
                    ),
                )
                if fallback_result.action == "switch":
                    assert next_candidate is not None
                    candidate_index = next_candidate
                    candidate_attempt = 0
                    fallback_count += 1
                    continue
                _end_failed(
                    emitter,
                    model_span,
                    parent_span_id,
                    exc,
                    context=context,
                    emit_error_event=emit_error_event,
                    attempt_count=attempt_index,
                    total_wait_seconds=total_wait,
                    fallback_count=fallback_count,
                    failure_chain=failure_chain,
                )
                raise
            _emit_decision(emitter, attempt_span, model_span, decision, exc, attempt_index)
            if decision.action == "stop":
                _end_failed(
                    emitter,
                    model_span,
                    parent_span_id,
                    exc,
                    context=context,
                    emit_error_event=emit_error_event,
                    attempt_count=attempt_index,
                    total_wait_seconds=total_wait,
                    fallback_count=fallback_count,
                    failure_chain=failure_chain,
                )
                raise
            assert decision.delay_seconds is not None
            try:
                await _wait_before_retry(
                    emitter,
                    model_span,
                    runtime,
                    decision.delay_seconds,
                    attempt_index,
                )
            except asyncio.CancelledError:
                _end_cancelled(emitter, model_span, parent_span_id)
                raise
            total_wait += decision.delay_seconds
            continue
        _end_attempt_succeeded(
            emitter,
            attempt_span,
            model_span,
            completion,
            attempt_index,
            candidate=candidate if fallback_plan is not None else None,
            candidate_index=candidate_index,
            candidate_attempt=candidate_attempt,
        )
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=model_span,
            parent_span_id=parent_span_id,
            payload=_success_payload(
                completion,
                **dict(context or {}),
                attempt_count=attempt_index,
                total_wait_seconds=total_wait,
                **(
                    {
                        "fallback_count": fallback_count,
                        "selected_model_identity": candidate.identity.model_dump(mode="json"),
                    }
                    if fallback_plan is not None and candidate is not None
                    else {}
                ),
            ),
        )
        return completion


async def _stream_with_retry(
    *,
    model: StreamingModel,
    messages: Sequence[Message],
    tools: Sequence[ToolSpec] | None,
    emitter: EventEmitter,
    model_span: str,
    parent_span_id: str | None,
    context: Mapping[str, object] | None,
    emit_error_event: bool,
    on_text_delta: TextDeltaObserver,
    on_stream_finished: StreamFinishedObserver | None,
    runtime: RetryRuntime,
    fallback_plan: ModelFallbackPlan | None,
) -> Completion:
    started_at = runtime.clock.monotonic()
    total_wait = 0.0
    attempt_index = 0
    candidate_index = 0
    candidate_attempt = 0
    fallback_count = 0
    failure_chain: list[dict[str, object]] = []
    while True:
        attempt_index += 1
        candidate_attempt += 1
        candidate = _candidate_for(fallback_plan, candidate_index)
        candidate_model = candidate.model if candidate is not None else model
        if not isinstance(candidate_model, StreamingModel):
            raise TypeError("fallback candidate must implement StreamingModel")
        attempt_span = _start_attempt(
            emitter,
            model_span,
            attempt_index,
            candidate=candidate if fallback_plan is not None else None,
            candidate_index=candidate_index,
            candidate_attempt=candidate_attempt,
        )
        response_started = False

        def mark_response_started() -> None:
            nonlocal response_started
            response_started = True

        def observe(delta: TextDelta, span_id: str) -> None:
            on_text_delta(delta, span_id)

        try:
            completion, response_started = await _within_deadline(
                lambda candidate_model=candidate_model: _consume_stream(
                    model=candidate_model,
                    messages=messages,
                    tools=tools,
                    model_span=model_span,
                    on_text_delta=observe,
                    on_stream_finished=on_stream_finished,
                    on_upstream_event=mark_response_started,
                ),
                runtime=runtime,
                started_at=started_at,
            )
        except asyncio.CancelledError:
            _end_attempt_cancelled(
                emitter,
                attempt_span,
                model_span,
                attempt_index,
                candidate=candidate if fallback_plan is not None else None,
                candidate_index=candidate_index,
                candidate_attempt=candidate_attempt,
            )
            _end_cancelled(emitter, model_span, parent_span_id)
            raise
        except Exception as exc:
            _end_attempt_failed(
                emitter,
                attempt_span,
                model_span,
                exc,
                attempt_index,
                response_started,
                candidate=candidate if fallback_plan is not None else None,
                candidate_index=candidate_index,
                candidate_attempt=candidate_attempt,
            )
            if not isinstance(exc, ProviderFailure):
                _end_failed(
                    emitter,
                    model_span,
                    parent_span_id,
                    exc,
                    context=context,
                    emit_error_event=emit_error_event,
                    attempt_count=attempt_index,
                    total_wait_seconds=total_wait,
                    fallback_count=(fallback_count if fallback_plan is not None else None),
                    failure_chain=failure_chain,
                )
                raise
            if fallback_plan is not None:
                failure_chain.append(
                    _failure_chain_item(
                        exc,
                        attempt_index=attempt_index,
                        candidate_index=candidate_index,
                        candidate_attempt=candidate_attempt,
                        candidate=candidate,
                    )
                )
            decision = _decide(
                runtime,
                exc,
                attempt_index=candidate_attempt,
                total_attempt_index=attempt_index,
                started_at=started_at,
                total_wait=total_wait,
                output_delivered=response_started,
            )
            fallback_decision = _fallback_after_failure(
                plan=fallback_plan,
                failure=exc,
                retry_decision=decision,
                runtime=runtime,
                started_at=started_at,
                total_attempt_index=attempt_index,
                candidate_index=candidate_index,
                candidate_attempt=candidate_attempt,
                tools=tools,
                streaming=True,
                output_delivered=response_started,
            )
            if fallback_decision is not None:
                fallback_result, next_candidate = fallback_decision
                _emit_fallback_decision(
                    emitter,
                    attempt_span,
                    model_span,
                    fallback_result,
                    attempt_index=attempt_index,
                    candidate_index=candidate_index,
                    candidate_attempt=candidate_attempt,
                    candidate=candidate,
                    next_candidate=(
                        None
                        if next_candidate is None or fallback_plan is None
                        else fallback_plan.candidates[next_candidate]
                    ),
                )
                if fallback_result.action == "switch":
                    assert next_candidate is not None
                    candidate_index = next_candidate
                    candidate_attempt = 0
                    fallback_count += 1
                    continue
                _end_failed(
                    emitter,
                    model_span,
                    parent_span_id,
                    exc,
                    context=context,
                    emit_error_event=emit_error_event,
                    attempt_count=attempt_index,
                    total_wait_seconds=total_wait,
                    fallback_count=fallback_count,
                    failure_chain=failure_chain,
                )
                raise
            _emit_decision(emitter, attempt_span, model_span, decision, exc, attempt_index)
            if decision.action == "stop":
                _end_failed(
                    emitter,
                    model_span,
                    parent_span_id,
                    exc,
                    context=context,
                    emit_error_event=emit_error_event,
                    attempt_count=attempt_index,
                    total_wait_seconds=total_wait,
                    fallback_count=fallback_count,
                    failure_chain=failure_chain,
                )
                raise
            assert decision.delay_seconds is not None
            try:
                await _wait_before_retry(
                    emitter,
                    model_span,
                    runtime,
                    decision.delay_seconds,
                    attempt_index,
                )
            except asyncio.CancelledError:
                _end_cancelled(emitter, model_span, parent_span_id)
                raise
            total_wait += decision.delay_seconds
            continue
        _end_attempt_succeeded(
            emitter,
            attempt_span,
            model_span,
            completion,
            attempt_index,
            candidate=candidate if fallback_plan is not None else None,
            candidate_index=candidate_index,
            candidate_attempt=candidate_attempt,
        )
        emitter.emit(
            EventType.MODEL_ENDED,
            span_id=model_span,
            parent_span_id=parent_span_id,
            payload=_success_payload(
                completion,
                **dict(context or {}),
                attempt_count=attempt_index,
                total_wait_seconds=total_wait,
                **(
                    {
                        "fallback_count": fallback_count,
                        "selected_model_identity": candidate.identity.model_dump(mode="json"),
                    }
                    if fallback_plan is not None and candidate is not None
                    else {}
                ),
            ),
        )
        return completion


async def _consume_stream(
    *,
    model: StreamingModel,
    messages: Sequence[Message],
    tools: Sequence[ToolSpec] | None,
    model_span: str,
    on_text_delta: TextDeltaObserver,
    on_stream_finished: StreamFinishedObserver | None = None,
    on_upstream_event: Callable[[], None] | None = None,
) -> tuple[Completion, bool]:
    text_parts: list[str] = []
    completion: Completion | None = None
    response_started = False
    async for event in model.stream_complete(messages, tools=tools):
        response_started = True
        if on_upstream_event is not None:
            on_upstream_event()
        if isinstance(event, TextDelta):
            if completion is not None:
                raise ProviderStreamProtocolError("CompletionFinished 之后仍收到文本增量")
            text_parts.append(event.text)
            on_text_delta(event, model_span)
        else:
            if completion is not None:
                raise ProviderStreamProtocolError("一次流包含多个 CompletionFinished")
            completion = event.completion
    if completion is None:
        raise ProviderStreamProtocolError("Provider stream 缺少 CompletionFinished")
    if "".join(text_parts) != completion.text:
        raise ProviderStreamProtocolError("文本增量与最终 Completion.text 不一致")
    if on_stream_finished is not None:
        on_stream_finished(model_span)
    return completion, response_started


def _start_attempt(
    emitter: EventEmitter,
    model_span: str,
    attempt_index: int,
    *,
    candidate: ModelExecutionCandidate | None = None,
    candidate_index: int = 0,
    candidate_attempt: int = 1,
) -> str:
    span_id = emitter.new_span_id()
    payload: dict[str, object] = {"attempt_index": attempt_index}
    payload.update(_candidate_payload(candidate, candidate_index, candidate_attempt))
    emitter.emit(
        EventType.MODEL_ATTEMPT_STARTED,
        span_id=span_id,
        parent_span_id=model_span,
        payload=payload,
    )
    return span_id


def _end_attempt_succeeded(
    emitter: EventEmitter,
    attempt_span: str,
    model_span: str,
    completion: Completion,
    attempt_index: int,
    *,
    candidate: ModelExecutionCandidate | None = None,
    candidate_index: int = 0,
    candidate_attempt: int = 1,
) -> None:
    emitter.emit(
        EventType.MODEL_ATTEMPT_ENDED,
        span_id=attempt_span,
        parent_span_id=model_span,
        payload={
            "ok": True,
            "attempt_index": attempt_index,
            "usage": _usage_payload(completion, include_total=False),
            **_candidate_payload(candidate, candidate_index, candidate_attempt),
        },
    )


def _end_attempt_failed(
    emitter: EventEmitter,
    attempt_span: str,
    model_span: str,
    exc: BaseException,
    attempt_index: int,
    output_delivered: bool,
    *,
    candidate: ModelExecutionCandidate | None = None,
    candidate_index: int = 0,
    candidate_attempt: int = 1,
) -> None:
    emitter.emit(
        EventType.MODEL_ATTEMPT_ENDED,
        span_id=attempt_span,
        parent_span_id=model_span,
        payload={
            **_attempt_failure_payload(
                exc,
                attempt_index=attempt_index,
                output_delivered=output_delivered,
            ),
            **_candidate_payload(candidate, candidate_index, candidate_attempt),
        },
    )


def _end_attempt_cancelled(
    emitter: EventEmitter,
    attempt_span: str,
    model_span: str,
    attempt_index: int,
    *,
    candidate: ModelExecutionCandidate | None = None,
    candidate_index: int = 0,
    candidate_attempt: int = 1,
) -> None:
    emitter.emit(
        EventType.MODEL_ATTEMPT_ENDED,
        span_id=attempt_span,
        parent_span_id=model_span,
        payload={
            "ok": False,
            "cancelled": True,
            "status": "cancelled",
            "attempt_index": attempt_index,
            "usage_status": "unknown",
            **_candidate_payload(candidate, candidate_index, candidate_attempt),
        },
    )


def _decide(
    runtime: RetryRuntime,
    failure: ProviderFailure,
    *,
    attempt_index: int,
    total_attempt_index: int | None = None,
    started_at: float,
    total_wait: float,
    output_delivered: bool,
) -> ProviderRetryDecision:
    return runtime.policy.decide(
        failure,
        attempt_index=attempt_index,
        total_attempt_index=total_attempt_index,
        elapsed_seconds=runtime.clock.monotonic() - started_at,
        total_wait_seconds=total_wait,
        utc_timestamp=runtime.clock.utc_timestamp(),
        rng=runtime.rng,
        output_delivered=output_delivered,
    )


def _emit_decision(
    emitter: EventEmitter,
    attempt_span: str,
    model_span: str,
    decision: ProviderRetryDecision,
    failure: ProviderFailure,
    attempt_index: int,
) -> None:
    emitter.emit(
        EventType.MODEL_RETRY_DECIDED,
        span_id=attempt_span,
        parent_span_id=model_span,
        payload=_decision_payload(
            decision,
            failure,
            attempt_index=attempt_index,
        ),
    )


async def _wait_before_retry(
    emitter: EventEmitter,
    model_span: str,
    runtime: RetryRuntime,
    delay_seconds: float,
    attempt_index: int,
) -> None:
    wait_span = emitter.new_span_id()
    emitter.emit(
        EventType.MODEL_RETRY_WAIT_STARTED,
        span_id=wait_span,
        parent_span_id=model_span,
        payload={"after_attempt": attempt_index, "delay_seconds": delay_seconds},
    )
    try:
        await runtime.sleeper(delay_seconds)
    except asyncio.CancelledError:
        emitter.emit(
            EventType.MODEL_RETRY_WAIT_ENDED,
            span_id=wait_span,
            parent_span_id=model_span,
            payload={
                "ok": False,
                "cancelled": True,
                "status": "cancelled",
                "after_attempt": attempt_index,
            },
        )
        raise
    emitter.emit(
        EventType.MODEL_RETRY_WAIT_ENDED,
        span_id=wait_span,
        parent_span_id=model_span,
        payload={"ok": True, "after_attempt": attempt_index},
    )


def _end_cancelled(
    emitter: EventEmitter,
    model_span: str,
    parent_span_id: str | None,
) -> None:
    emitter.emit(
        EventType.MODEL_ENDED,
        span_id=model_span,
        parent_span_id=parent_span_id,
        payload={"ok": False, "cancelled": True, "status": "cancelled"},
    )


def _end_failed(
    emitter: EventEmitter,
    model_span: str,
    parent_span_id: str | None,
    exc: BaseException,
    *,
    context: Mapping[str, object] | None,
    emit_error_event: bool,
    attempt_count: int | None = None,
    total_wait_seconds: float | None = None,
    fallback_count: int | None = None,
    failure_chain: Sequence[Mapping[str, object]] = (),
) -> None:
    if emit_error_event:
        emitter.emit(
            EventType.ERROR,
            span_id=model_span,
            parent_span_id=parent_span_id,
            payload={"error": repr(exc), **provider_failure_payload(exc)},
        )
    outcome_context = dict(context or {})
    if attempt_count is not None:
        outcome_context["attempt_count"] = attempt_count
    if total_wait_seconds is not None:
        outcome_context["total_wait_seconds"] = total_wait_seconds
    if fallback_count is not None:
        outcome_context["fallback_count"] = fallback_count
    if failure_chain:
        outcome_context["provider_failure_chain"] = [dict(item) for item in failure_chain]
    emitter.emit(
        EventType.MODEL_ENDED,
        span_id=model_span,
        parent_span_id=parent_span_id,
        payload=model_failure_event_payload(exc, **outcome_context),
    )


def _candidate_for(
    plan: ModelFallbackPlan | None,
    candidate_index: int,
) -> ModelExecutionCandidate | None:
    return None if plan is None else plan.candidates[candidate_index]


def _candidate_payload(
    candidate: ModelExecutionCandidate | None,
    candidate_index: int,
    candidate_attempt: int,
) -> dict[str, object]:
    if candidate is None:
        return {}
    return {
        "candidate_index": candidate_index + 1,
        "candidate_attempt": candidate_attempt,
        "model_identity": candidate.identity.model_dump(mode="json"),
    }


def _failure_chain_item(
    failure: ProviderFailure,
    *,
    attempt_index: int,
    candidate_index: int,
    candidate_attempt: int,
    candidate: ModelExecutionCandidate | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "attempt_index": attempt_index,
        "candidate_index": candidate_index + 1,
        "candidate_attempt": candidate_attempt,
        **provider_failure_payload(failure),
    }
    if candidate is not None:
        payload["model_identity"] = candidate.identity.model_dump(mode="json")
    return payload


def _fallback_after_failure(
    *,
    plan: ModelFallbackPlan | None,
    failure: ProviderFailure,
    retry_decision: ProviderRetryDecision,
    runtime: RetryRuntime,
    started_at: float,
    total_attempt_index: int,
    candidate_index: int,
    candidate_attempt: int,
    tools: Sequence[ToolSpec] | None,
    streaming: bool,
    output_delivered: bool,
) -> tuple[ProviderFallbackDecision, int | None] | None:
    if plan is None:
        return None
    reached_local_limit = (
        plan.policy.allows_failure(failure)
        and candidate_attempt >= plan.policy.max_attempts_per_candidate
    )
    if retry_decision.action != "stop" and not reached_local_limit:
        return None
    next_candidate, skipped_ineligible = _next_eligible_candidate(
        plan,
        candidate_index=candidate_index,
        tools=tools,
        streaming=streaming,
    )
    decision = plan.policy.decide(
        failure,
        total_attempt_index=total_attempt_index,
        global_attempt_limit=runtime.policy.max_attempts,
        elapsed_seconds=runtime.clock.monotonic() - started_at,
        deadline_seconds=runtime.policy.deadline_seconds,
        output_delivered=output_delivered,
        next_candidate=next_candidate,
        skipped_ineligible=skipped_ineligible,
    )
    return decision, next_candidate


def _next_eligible_candidate(
    plan: ModelFallbackPlan,
    *,
    candidate_index: int,
    tools: Sequence[ToolSpec] | None,
    streaming: bool,
) -> tuple[int | None, bool]:
    skipped = False
    for index in range(candidate_index + 1, len(plan.candidates)):
        candidate = plan.candidates[index]
        if tools and candidate.capabilities.tools != "supported":
            skipped = True
            continue
        if streaming and (
            candidate.capabilities.native_streaming != "supported"
            or not isinstance(candidate.model, StreamingModel)
        ):
            skipped = True
            continue
        return index, skipped
    return None, skipped


def _emit_fallback_decision(
    emitter: EventEmitter,
    attempt_span: str,
    model_span: str,
    decision: ProviderFallbackDecision,
    *,
    attempt_index: int,
    candidate_index: int,
    candidate_attempt: int,
    candidate: ModelExecutionCandidate | None,
    next_candidate: ModelExecutionCandidate | None,
) -> None:
    payload: dict[str, object] = {
        "attempt_index": attempt_index,
        "candidate_attempt": candidate_attempt,
        "action": decision.action,
        "reason": decision.reason,
        "from_candidate": candidate_index + 1,
    }
    if candidate is not None:
        payload["model_identity"] = candidate.identity.model_dump(mode="json")
    if decision.next_candidate is not None and next_candidate is not None:
        payload["to_candidate"] = decision.next_candidate + 1
        payload["selected_model_identity"] = next_candidate.identity.model_dump(mode="json")
    emitter.emit(
        EventType.MODEL_FALLBACK_DECIDED,
        span_id=attempt_span,
        parent_span_id=model_span,
        payload=payload,
    )

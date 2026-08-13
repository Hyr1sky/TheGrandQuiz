"""Eval suite runner: combine deterministic rules with calibrated Tier-2 quality."""

from __future__ import annotations

import json
from typing import cast

from grandquiz.evals.case import Case
from grandquiz.evals.graders import GRADERS
from grandquiz.evals.quality import QualityEvaluation, QualityRequest
from grandquiz.evals.quality_calibration import CalibratedQualitySuite
from grandquiz.evals.resources import eval_fixture_path
from grandquiz.evals.result import CaseReport, ReactObservation
from grandquiz.evals.solvers import build_event_harness, load_cases, solve
from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.kernel.trace import Span, summarize_token_usage
from grandquiz.providers.base import Provider, Role
from grandquiz.providers.replay import Cassette, ReplayProvider

_QUALITY_CASSETTE = eval_fixture_path("eval_quality_grounded_answer.cassette.json")


def _prompt_versions(events: list[AgentEvent]) -> list[str]:
    versions: list[str] = []
    for event in events:
        if event.type != EventType.MODEL_STARTED:
            continue
        version = event.payload.get("prompt_version")
        if isinstance(version, str) and version not in versions:
            versions.append(version)
    return versions


async def run_case(
    case: Case,
    *,
    provider_override: Provider | None = None,
    quality_suite: CalibratedQualitySuite | None = None,
    quality_unavailable_reason: str | None = None,
) -> CaseReport:
    """Run one case; any solver or provider exception is a hard failure."""

    quality = case.quality_profile
    quality_question = case.quality_question
    try:
        result = await solve(case, provider_override=provider_override)
    except Exception as exc:
        return CaseReport(
            case_id=case.id,
            kind=case.kind,
            passed=False,
            failures=[f"solve 抛异常（硬失败，不静默通过）：{exc!r}"],
            total_tokens=0,
            prompt_versions=[],
            error=repr(exc),
            rule_passed=False,
            quality_rubric_id=quality.rubric_id if quality is not None else None,
        )

    failures: list[str] = []
    actual = [event.type for event in result.events]
    if actual != case.expected_events:
        failures.append(f"事件类型序列不符：期望 {case.expected_events}，实得 {actual}")
    grader = GRADERS.get(case.id)
    if grader is None:
        failures.append(f"缺少 grader：{case.id}")
    else:
        failures.extend(grader(result))
    rule_passed = not failures

    quality_evaluation: QualityEvaluation | None = None
    quality_passed: bool | None = None
    quality_events: list[AgentEvent] = []
    quality_spans: list[Span] = []
    if quality is not None and quality_suite is None:
        suffix = f"：{quality_unavailable_reason}" if quality_unavailable_reason else ""
        failures.append(f"Tier-2 缺少已校准 QualitySuite，不能退化为仅运行规则门{suffix}")
        quality_passed = False
    elif quality is not None and quality_suite is not None:
        if not isinstance(result.observation, ReactObservation):
            failures.append("Tier-2 case 缺少 ReactObservation，无法取得最终用户可见回答")
            final_outputs: tuple[str, ...] = ()
        else:
            final_outputs = result.observation.final_outputs
        if quality_question is None or not final_outputs:
            failures.append("Tier-2 缺少用户问题或最终用户可见回答")
            quality_passed = False
        else:
            quality_emitter, quality_events, quality_trace = build_event_harness()
            try:
                quality_evaluation = await quality_suite.evaluate(
                    QualityRequest(
                        rubric_id=quality.rubric_id,
                        question=quality_question,
                        candidate=final_outputs[-1],
                        reference=quality.reference,
                    ),
                    emitter=quality_emitter,
                )
            except Exception as exc:
                failures.append(f"Tier-2 judge 抛异常（质量硬失败）：{exc!r}")
                quality_passed = False
            finally:
                quality_spans = quality_trace.span_tree("run")
                quality_trace.close()
            if quality_evaluation is not None:
                quality_passed = quality_evaluation.passed
            if quality_evaluation is not None and not quality_passed:
                failures.extend(
                    f"Tier-2 {criterion.criterion_id}={criterion.score}：{criterion.rationale}"
                    for criterion in quality_evaluation.criteria
                    if criterion.score < 3
                )
    return CaseReport(
        case_id=case.id,
        kind=case.kind,
        passed=rule_passed and quality_passed is not False,
        failures=failures,
        total_tokens=summarize_token_usage(result.events).total_tokens,
        prompt_versions=_prompt_versions(result.events),
        rule_passed=rule_passed,
        quality_passed=quality_passed,
        quality_rubric_id=quality.rubric_id if quality is not None else None,
        judge_tokens=(
            quality_evaluation.usage.total_tokens if quality_evaluation is not None else 0
        ),
        quality_evaluation=quality_evaluation,
        subject_events=result.events,
        subject_spans=result.spans,
        quality_events=quality_events,
        quality_spans=quality_spans,
    )


def _load_quality_cassette() -> ReplayProvider:
    raw: dict[str, dict[str, str]] = json.loads(_QUALITY_CASSETTE.read_text(encoding="utf-8"))
    model_for_role = cast(
        "dict[Role, str]", {entry["role"]: entry["model"] for entry in raw.values()}
    )
    return ReplayProvider(Cassette.load(_QUALITY_CASSETTE), model_for_role)


async def run_all(*, quality_provider_override: Provider | None = None) -> list[CaseReport]:
    """Run the full suite after calibrating Tier-2 exactly once."""

    suite: CalibratedQualitySuite | None = None
    unavailable_reason: str | None = None
    try:
        provider = quality_provider_override or _load_quality_cassette()
        suite = await CalibratedQualitySuite.create(provider=provider)
    except Exception as exc:
        unavailable_reason = repr(exc)
    return [
        await run_case(
            case,
            quality_suite=suite,
            quality_unavailable_reason=unavailable_reason,
        )
        for case in load_cases()
    ]

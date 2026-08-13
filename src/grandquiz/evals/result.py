"""Eval execution and suite result contracts."""

from dataclasses import dataclass, field
from typing import Any

from grandquiz.domain.learning.assessment.engine import AssessmentResult
from grandquiz.domain.learning.ingest import IngestResult
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.case import Case
from grandquiz.evals.quality import QualityEvaluation
from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.kernel.trace import Span
from grandquiz.providers.base import Role


def _empty_events() -> list[AgentEvent]:
    return []


def _empty_spans() -> list[Span]:
    return []


@dataclass
class SolveResult:
    """一次 solve 的事件、trace、领域末态与 provider 观测。"""

    case: Case
    events: list[AgentEvent]
    spans: list[Span]
    result: AssessmentResult | IngestResult | None
    store: LearningStore
    memory: LearningMemory
    calls: int
    roles: list[Role]
    context: dict[str, Any]


@dataclass
class CaseReport:
    """One case verdict with separate subject and Tier-2 evidence."""

    case_id: str
    kind: str
    passed: bool
    failures: list[str]
    total_tokens: int
    prompt_versions: list[str]
    error: str | None = None
    rule_passed: bool = False
    quality_passed: bool | None = None
    quality_rubric_id: str | None = None
    judge_tokens: int = 0
    quality_evaluation: QualityEvaluation | None = None
    subject_events: list[AgentEvent] = field(default_factory=_empty_events)
    subject_spans: list[Span] = field(default_factory=_empty_spans)
    quality_events: list[AgentEvent] = field(default_factory=_empty_events)
    quality_spans: list[Span] = field(default_factory=_empty_spans)

    @property
    def execution_tokens(self) -> int:
        return self.total_tokens

    @property
    def judge_prompt_versions(self) -> list[str]:
        versions: list[str] = []
        for event in self.quality_events:
            if event.type != EventType.MODEL_STARTED:
                continue
            version = event.payload.get("prompt_version")
            if isinstance(version, str) and version not in versions:
                versions.append(version)
        return versions

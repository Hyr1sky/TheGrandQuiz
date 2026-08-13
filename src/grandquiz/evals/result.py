"""Eval execution and suite result contracts."""

from dataclasses import dataclass, field

from grandquiz.domain.learning.assessment.engine import AssessmentResult
from grandquiz.domain.learning.ingest import IngestResult
from grandquiz.domain.learning.memory import ConceptState, LearningMemory
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.case import AssessCase, Case, ReactCase
from grandquiz.evals.quality import QualityEvaluation
from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.kernel.trace import Span
from grandquiz.providers.base import Role


def _empty_events() -> list[AgentEvent]:
    return []


def _empty_spans() -> list[Span]:
    return []


@dataclass(frozen=True)
class AskedHistory:
    """One item's immutable within-session question ledger."""

    item_id: str
    questions: tuple[str, ...]


@dataclass(frozen=True)
class AssessObservation:
    """Deterministic setup evidence owned by an assess solver."""

    items: tuple[KnowledgeItem, ...]
    natural_item_id: str | None
    selected_resource_ids: tuple[str, ...] | None
    weak_target_item_id: str | None
    pre_weak_state: ConceptState | None
    pre_in_weak: bool | None
    recently_asked: tuple[AskedHistory, ...]

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.items)

    def questions_for(self, item_id: str) -> tuple[str, ...]:
        return next(
            (entry.questions for entry in self.recently_asked if entry.item_id == item_id),
            (),
        )


@dataclass(frozen=True)
class BasicIngestObservation:
    """An ingest case whose evidence is fully represented by result/store/events."""


@dataclass(frozen=True)
class WebAcquisitionObservation:
    """Additional evidence for the two-path acquisition replay case."""

    selected_url: str
    rejected_url: str
    rejected_result: IngestResult
    provider_calls_after_success: int


@dataclass(frozen=True)
class ReactObservation:
    """User-visible ReAct outputs and optional grounded-document baseline."""

    final_outputs: tuple[str, ...]
    grounded_resource_id: str | None
    full_document_chars: int


SolveObservation = (
    AssessObservation | BasicIngestObservation | WebAcquisitionObservation | ReactObservation
)


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
    observation: SolveObservation

    def __post_init__(self) -> None:
        """Reject case/observation combinations that no solver may produce."""
        expected: type[
            AssessObservation
            | BasicIngestObservation
            | WebAcquisitionObservation
            | ReactObservation
        ]
        if isinstance(self.case, AssessCase):
            expected = AssessObservation
        elif isinstance(self.case, ReactCase):
            expected = ReactObservation
        elif self.case.source == "web_replay":
            expected = WebAcquisitionObservation
        else:
            expected = BasicIngestObservation
        if not isinstance(self.observation, expected):
            raise TypeError(
                f"{type(self.case).__name__} requires {expected.__name__}, "
                f"got {type(self.observation).__name__}"
            )


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

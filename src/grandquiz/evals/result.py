"""Eval solver 的统一输出契约，供公共 runner 与规则 grader 共同消费。"""

from dataclasses import dataclass
from typing import Any

from grandquiz.domain.learning.assessment.engine import AssessmentResult
from grandquiz.domain.learning.ingest import IngestResult
from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.store import LearningStore
from grandquiz.evals.case import Case
from grandquiz.kernel.events import AgentEvent
from grandquiz.kernel.trace import Span
from grandquiz.providers.base import Role


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

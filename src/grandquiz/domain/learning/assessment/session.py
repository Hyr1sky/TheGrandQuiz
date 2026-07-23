"""多题考核会话——持有跨轮状态，把单题 workflow 组合成稳定的领域 Interface。

``assess_once`` 仍是 ADR-0004 规定的确定性单题 workflow；本 Module 只拥有一场考核会话中
跨轮共享的依赖、覆盖台账与随机种子推进。CLI 和 ReAct tool 负责各自的交互、恢复与展示策略，
不再各自复制这些领域状态。
"""

from grandquiz.domain.learning.asked_questions import AskedQuestionsLedger
from grandquiz.domain.learning.assessment.engine import AssessmentResult, assess_once
from grandquiz.domain.learning.assessment.scope import ALL_SCOPE, QuizScope
from grandquiz.domain.learning.assessment.selection import Focus
from grandquiz.domain.learning.difficulty import DifficultyLedger
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.preference import PreferenceMemory
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.clock import new_rng
from grandquiz.kernel.events import EventEmitter
from grandquiz.providers.base import Provider


class AssessmentSession:
    """在多轮考核之间持有覆盖状态与确定性随机序列。"""

    def __init__(
        self,
        *,
        store: Store,
        provider: Provider,
        responder: Responder,
        memory: Memory,
        seed: int = 0,
        asked_questions: AskedQuestionsLedger | None = None,
        preferences: PreferenceMemory | None = None,
        difficulty: DifficultyLedger | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._responder = responder
        self._memory = memory
        self._next_seed = seed
        self._recently_asked: dict[str, list[str]] = {}
        self._asked_questions = asked_questions
        self._preferences = preferences
        self._difficulty = difficulty

    async def assess(
        self,
        *,
        emitter: EventEmitter,
        focus: Focus = "mixed",
        scope: QuizScope = ALL_SCOPE,
        question_type: str | None = None,
    ) -> AssessmentResult:
        """运行下一轮单题 workflow，并推进本会话的覆盖台账与随机序列。"""
        seed = self._next_seed
        self._next_seed += 1
        return await assess_once(
            store=self._store,
            provider=self._provider,
            responder=self._responder,
            memory=self._memory,
            emitter=emitter,
            rng=new_rng(seed),
            recently_asked=self._recently_asked,
            asked_questions=self._asked_questions,
            focus=focus,
            preferences=self._preferences,
            scope=scope,
            question_type=question_type,
            difficulty=self._difficulty,
        )

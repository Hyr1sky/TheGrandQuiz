"""``start_quiz(count)`` 工具：受控一问一答子流程，内部跑 ``assess_once × count``（R1-S6）。

LLM 只触发它、拿结构化小结，不进逐题循环、不复述题目、不自己判卷——取代早期把逐轮编排压给
LLM 的软工具方案（那套 deepseek 守不住：编题 / MC 答案加前缀毁逐字判卷 / 题目双重渲染 /
confabulate）。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from grandquiz.domain.learning.asked_questions import AskedQuestionsLedger
from grandquiz.domain.learning.assessment.engine import AssessmentResult, assess_once
from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.selection import Focus
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.preference import PreferenceMemory
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import Store
from grandquiz.domain.learning.tools._scoped_emitter import ScopedEmitter
from grandquiz.domain.learning.tools.query_weak_tool import WeakConcept
from grandquiz.kernel.clock import new_rng
from grandquiz.kernel.events import EventEmitter
from grandquiz.kernel.tools import Tool, ToolContext
from grandquiz.providers.base import Provider

# start_quiz 单次调用的出题上限：挡 LLM 传超大 count 把一次工具调用拖成长跑（保守取 20）。
_MAX_QUIZ_COUNT = 20


@dataclass
class _QuizSeedCounter:
    """受控考核循环的**选题种子推进器**（进程内、跨同一会话的多次 start_quiz 调用累积）。

    每题取 ``seed + counter`` 并把 counter 自增（**禁墙上时钟 / 全局 random**——replay 时同 seed +
    同题序 → 同选题）；同 ``run_quiz`` 的 ``seed + 轮次``，只是把计数器提升为跨调用会话态，故连续
    两次 ``start_quiz`` 不会因种子重置而复现同一选题序。
    """

    seed: int
    _counter: int = 0

    def next_seed(self) -> int:
        seed = self.seed + self._counter
        self._counter += 1
        return seed


class QuizRoundResult(BaseModel):
    """``start_quiz`` 小结里的**单题结果**：被考概念 + 判决 + 记账后终态（全由代码算，非 LLM 产）。

    ``concept_state`` 是本题记账后被考 item 在 Learning Memory 的最终状态（薄弱 / 观察中，或
    None=未追踪 / 已销账），透出记账结果供 LLM 转述——与逐题的 ``CONCEPT_STATE_CHANGED`` 事件互补。
    """

    item_id: str
    concept: str
    verdict: VerdictLabel
    concept_state: str | None = None


class StartQuizResult(BaseModel):
    """``start_quiz`` 回给 ReAct 的**结构化小结**——LLM 据此转述，不复述题目 / 不自己判卷。

    ``status="refused"``（空库）时 ``asked=0`` / ``rounds=[]`` / ``weak=[]``；``status="completed"``
    时 ``asked`` 为实际考过题数（用户中途取消则少于请求 count），``rounds`` 逐题判决，``weak`` 是
    本次考核后全库仍被追踪的薄弱概念（按 item_id 升序，同 ``query_weak_concepts`` 全局 KB 口径）。
    """

    status: Literal["completed", "refused"]
    asked: int
    rounds: list[QuizRoundResult]
    weak: list[WeakConcept]


class _StartQuizParams(BaseModel):
    count: int = 1  # 本次考核出题数（默认 1；handler 夹到 [1, _MAX_QUIZ_COUNT]）
    focus: Focus = "mixed"  # 选题聚焦：mixed 覆盖优先（默认）/ new 只考没考过的 / weak 复习薄弱
    # 目录式 scope（GKB-S4）：按 exact resource_id 收窄考哪些材料；None（默认）= 全库。LLM 从目录
    # 清单认出用户意图对应的 resource_id 填入（命中不了 → 拿不到 id → 别填，assess_once 诚实拒答）。
    resource_ids: list[str] | None = None
    # 用户显式题型意图短语（GKB-S5，修 #1 错题型；ADR-0006）：LLM 只抽用户原话里的题型意图
    # （"简答"/"选择题"/"追问"…），代码用冻结同义表映射到既有三题型、显式意图胜过记忆状态自适应
    # 路由；None（默认）= 不指定 → 按薄弱状态自适应路由。别自造题型、别把"简答"填成"选择题"。
    question_type: str | None = None


def _weak_concepts(store: Store, memory: Memory) -> list[WeakConcept]:
    """全库被追踪的薄弱概念摘要（item_id 升序，全局 KB）——与 ``query_weak_concepts`` 同口径。"""
    concept_by_id = {item.item_id: item.concept for item in store.all_items()}
    return [
        WeakConcept(item_id=item_id, concept=concept_by_id[item_id], state=state)
        for item_id in sorted(memory.weak_item_ids())
        if item_id in concept_by_id and (state := memory.state_of(item_id)) is not None
    ]


def make_start_quiz_tool(
    *,
    provider: Provider,
    store: Store,
    memory: Memory,
    responder: Responder,
    preferences: PreferenceMemory | None = None,
    quiz_seed: int = 0,
    asked_questions: AskedQuestionsLedger | None = None,
) -> Tool:
    """建 ``start_quiz(count)`` 工具：受控一问一答子流程，内部跑 ``assess_once × count``。

    **只组合** ``assess_once``（一行不改）：逐题选题 / 出题（role=enrich）/ 判卷（MC 走确定性代码、
    开放走 role=basic）/ 记账全在 ``assess_once`` 的确定性骨架里，本工具只做 **N 题编排 + 收小结**。
    每题作答走**注入的 Responder**（真机 ``InteractiveResponder`` 的 ``questionary.select`` 逐字
    返回所选项文本 → ``grade_multiple_choice`` 逐字比对，从根杜绝 "B. " 前缀污染判卷）。

    内部 ``assess_once`` 的 assessment 根 span（本无父）经 ``ScopedEmitter`` 重挂到本次 TOOL_CALL
    span 之下（``ctx.parent_span_id``），故整棵考核子树上同一条脊柱、进 trace；出题 / 判卷 / 记账的
    点事件（QUESTION_ASKED / ANSWER_JUDGED / …）携显式父原样透传。

    ``focus``（工具入参，LLM 按用户意图逐次给）：选题聚焦档位，透传每题的 ``assess_once`` →
    ``select_target``——``mixed``（默认）覆盖优先（先考未考过、考完一遍才兜底薄弱，修 dogfood
    锁死）、``new`` 只考未考过、``weak`` 复习薄弱。``recently_asked`` 跨同一会话累积、其 keys 作
    已考集喂选题，故连续 mixed 考核自然覆盖不同 item（不再锁死）。

    ``resource_ids``（工具入参，目录式 scope，GKB-S4，修 #1 考错库）：按 exact resource_id 把候选
    池收窄到指定材料，透传每题 ``assess_once`` → ``apply_scope``（选题前的确定性预过滤）。``None``
    （默认）= 全库。LLM 从注入的目录清单认出用户意图对应的 resource_id 填入；命中为空 →
    ``assess_once`` 发 ``empty_scope`` 拒答（诚实说"还没这主题的材料"，不静默考别的库）。

    ``question_type``（工具入参，用户显式题型意图短语，GKB-S5，修 #1 错题型；ADR-0006）：LLM 只抽
    用户原话里的题型意图短语（"简答"/"选择题"/"追问"…）原样填入，透传每题 ``assess_once`` →
    ``resolve_question_type`` 用冻结同义表映射到既有三题型——**显式意图胜过记忆状态自适应路由**，
    未知 / ``None``（默认）回落自适应；短答意图代码层禁止映射到"选择题"（护栏，防复现 #1）。

    ``preferences``：透传给 ``assess_once`` 解析出题语言（**偏好 > 中文**）；``None`` 时行为不变
    （走"中文"兜底）。``recently_asked`` / ``_QuizSeedCounter`` 在闭包捕获、跨同一会话的多次
    ``start_quiz`` 累积（复考换角度去重 + 选题种子确定性推进）。空库 → 优雅返回 ``refused``（不调
    任何 LLM）；用户中途取消作答（Responder 抛 ``KeyboardInterrupt``）→ 结束考核、返回已完成部分。

    ``asked_questions``：跨会话持久的已问过台账（``AskedQuestionsLedger``，skeleton-ledger.md
    #8 修复）——透传每题 ``assess_once``，与 ``recently_asked``（会话内）互补，让"换角度去重"这条
    防线在关掉 CLI 重开后依然生效。``None``（默认）= 不接持久层、向后兼容。
    """
    seed_counter = _QuizSeedCounter(seed=quiz_seed)
    recently_asked: dict[str, list[str]] = {}

    async def handler(params: _StartQuizParams, ctx: ToolContext) -> str:
        # 作用域化 emitter：把每题 assessment 根 span 重挂到本次 TOOL_CALL 之下（隔离在工具边界）。
        scoped: EventEmitter = (
            ScopedEmitter(ctx.emitter, ctx.parent_span_id)
            if ctx.parent_span_id is not None
            else ctx.emitter
        )
        concept_by_id = {it.item_id: it.concept for it in store.all_items()}
        count = min(max(params.count, 1), _MAX_QUIZ_COUNT)  # 夹到 [1, 上限]，挡 0 / 负 / 超大
        rounds: list[QuizRoundResult] = []
        for _ in range(count):
            try:
                result: AssessmentResult = await assess_once(
                    store=store,
                    provider=provider,
                    responder=responder,
                    memory=memory,
                    emitter=scoped,
                    rng=new_rng(seed_counter.next_seed()),
                    recently_asked=recently_asked,
                    asked_questions=asked_questions,
                    focus=params.focus,
                    preferences=preferences,
                    resource_ids=params.resource_ids,
                    question_type=params.question_type,
                )
            except KeyboardInterrupt:
                # 用户取消作答：结束本次考核，返回已完成部分（不把取消当空作答污染判卷）。
                break
            if result.status == "refused":
                # 空库：无题可考，优雅拒答（不调任何 LLM）——首题即 refused，直接返回。
                return StartQuizResult(
                    status="refused", asked=0, rounds=[], weak=[]
                ).model_dump_json()
            # judged 分支：item_id / verdict 必非 None（AssessmentResult 契约）；防御性收窄。
            if result.item_id is None or result.verdict is None:
                continue
            rounds.append(
                QuizRoundResult(
                    item_id=result.item_id,
                    concept=concept_by_id.get(result.item_id, result.item_id),
                    verdict=result.verdict,
                    concept_state=result.concept_state,
                )
            )
        return StartQuizResult(
            status="completed",
            asked=len(rounds),
            rounds=rounds,
            weak=_weak_concepts(store, memory),
        ).model_dump_json()

    return Tool(
        name="start_quiz",
        description=(
            "从全库发起一次考核：出 count 道题（默认 1）逐题问用户并判卷，返回考了几题 / "
            "每题判决 / 暴露的薄弱点小结。你只触发它、转述小结——"
            "不要复述题目、不要自行判卷、不要编题。\n"
            "据用户意图填两个可选旋钮（都不填 = 全库、按掌握状态自适应出题）：\n"
            "· resource_ids（考哪份材料）：从上文【学情】里的库存材料清单认出用户点名的主题对应的 "
            "exact resource_id 填入（可多选）——语义匹配是你的活（'代理通信协议' 认到 ACP 那条）；"
            "认不出对应材料 / 用户没点具体材料 → 别填，宁可诚实拒答也不考错库。\n"
            "· question_type（要哪种题型）：只抽用户原话里的题型意图短语（如 '简答' / '选择题' / "
            "'追问'）原样填入，由代码映射到题型；用户没点题型 → 别填。"
            "别自造题型、别把 '简答' 填成 '选择题'。**该 question_type 作用于本次全部 count 道题——"
            "本工具一次只出一种题型**。\n"
            "· **要混合题型**（如'一道选择一道简答'）：请**分多次调用本工具**，"
            "每次一个 question_type + 对应 count（先 {question_type:'选择题',count:1} "
            "再 {question_type:'简答',count:1}）；"
            "**别用 focus 表达'混合'——focus 是选题覆盖策略、与题型无关**。\n"
            "· focus（选题聚焦，非题型）：mixed=覆盖优先（默认，先考没考过的），new=只考没考过的"
            "（用户说'考其他的 / 换一批'），weak=复习薄弱（用户说'复习 / 考薄弱'）。\n"
            "工具输入示例——用户'考代理通信协议的简答题'（清单里该主题的 resource_id 记作 r-acp）："
            '{"resource_ids": ["r-acp"], "question_type": "简答"}；'
            "用户'随便考我一道'：{}（两旋钮都不填 = 全库自动路由）；"
            '用户\'一道选择一道简答\' → 分两次调用：先 {"question_type": "选择题", "count": 1} '
            '再 {"question_type": "简答", "count": 1}。'
        ),
        params=_StartQuizParams,
        handler=handler,
        wants_context=True,
    )

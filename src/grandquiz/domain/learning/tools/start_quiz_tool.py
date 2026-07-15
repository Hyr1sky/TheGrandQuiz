"""``start_quiz(count)`` 工具：受控一问一答子流程，内部跑 ``assess_once × count``（R1-S6）。

LLM 只触发它、拿结构化小结，不进逐题循环、不复述题目、不自己判卷——取代早期把逐轮编排压给
LLM 的软工具方案（那套 deepseek 守不住：编题 / MC 答案加前缀毁逐字判卷 / 题目双重渲染 /
confabulate）。
"""

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from grandquiz.domain.learning.asked_questions import AskedQuestionsLedger
from grandquiz.domain.learning.assessment.engine import AssessmentResult, assess_once
from grandquiz.domain.learning.assessment.grading import VerdictLabel
from grandquiz.domain.learning.assessment.selection import Focus
from grandquiz.domain.learning.difficulty import DifficultyLedger
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

logger = logging.getLogger(__name__)

# start_quiz 单次调用的出题上限：挡 LLM 传超大 count 把一次工具调用拖成长跑（保守取 20）。
_MAX_QUIZ_COUNT = 20


def _clamp_count(n: int) -> int:
    """把请求题数夹到 ``[1, _MAX_QUIZ_COUNT]``（挡 0 / 负 / 超大）。

    与改动前 handler 的 ``min(max(params.count, 1), _MAX_QUIZ_COUNT)`` 逐字节等价——是 SE-S4
    保"无分段路径字节不变"的锚点。
    """
    return min(max(n, 1), _MAX_QUIZ_COUNT)


class QuizSegment(BaseModel):
    """批内一段（SE-S4）：连续 ``count`` 道题共用同一题型意图短语 ``question_type``。

    ``question_type`` 是**用户原话里的题型意图短语**（"选择题" / "简答" / "追问"…），同 ADR-0006 的
    口径——LLM 只抽短语、代码用冻结同义表映射到既有三题型（**不是**最终题型枚举、也不新增第 4
    题型）。
    ``count`` 为该段题数；``<= 0`` 的段在 ``expand_segments`` 里贡献 0 题、被跳过（fail-soft，容
    LLM 抽出 0 / 负）。
    """

    count: int
    question_type: str


def expand_segments(
    segments: list[QuizSegment] | None,
    *,
    count: int,
    question_type: str | None,
) -> list[str | None]:
    """把「分段列表 或 单值题型」展开成**每题一个题型意图**的列表（纯函数，无 I/O / 随机 / 时钟）。

    返回列表每个元素是喂给该题 ``assess_once(question_type=...)`` → ``resolve_question_type`` 的意图
    短语（``None`` = 该题不指定、回落记忆状态自适应路由）。逐位置解析仍复用 ADR-0006 的仲裁，本
    函数**不做任何题型裁决**、只负责编排展开。口径（各分支都被单测钉死）：

    - ``segments`` 为 ``None`` 或空列表 → ``[question_type] * _clamp_count(count)``：**改动前单值
      行为**——单一题型重复 clamp(count) 次（``question_type`` 可为 ``None`` = 全程自适应）。此路径
      与旧 ``for _ in range(min(max(count, 1), _MAX_QUIZ_COUNT))`` 逐字节等价（字节等价的锚）。
    - ``segments`` 非空 → 展平：对每段把 ``seg.question_type`` 重复 ``seg.count`` 次、顺序拼接；
      **分段存在时总题数 = 各段 count 之和**（``count`` 入参此时被忽略）。其中：
        - 某段 ``count <= 0`` → 该段贡献 0 题（跳过），不报错（fail-soft）。
        - 展平后总数 > ``_MAX_QUIZ_COUNT`` → **截断**到前 ``_MAX_QUIZ_COUNT`` 题并 log 警告（不静默
          丢，与单值路径同一上限，挡 LLM 传超大段把一次调用拖成长跑）。
        - 展平后为空（各段全 0 / 负）→ 回落 ``[question_type] * _clamp_count(count or 1)``：防退化
          成 0 题（0 题会让工具空转、返回 asked=0，对用户是"我要考试却什么都没发生"）。
    """
    if not segments:
        return [question_type] * _clamp_count(count)
    intents: list[str | None] = []
    for seg in segments:
        if seg.count > 0:
            intents.extend([seg.question_type] * seg.count)
    if not intents:
        # 各段 count 全 <= 0：回落单值路径，避免退化成 0 题（fail-soft）。
        return [question_type] * _clamp_count(count or 1)
    if len(intents) > _MAX_QUIZ_COUNT:
        logger.warning(
            "start_quiz 分段总题数 %d 超上限 %d，截断到前 %d 题（分段调度 SE-S4）",
            len(intents),
            _MAX_QUIZ_COUNT,
            _MAX_QUIZ_COUNT,
        )
        intents = intents[:_MAX_QUIZ_COUNT]
    return intents


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
    # 批内分段调度（SE-S4）：按题目位置分段指定题型（"前 3 道选择、后 2 道简答"）。仍逐题交互、
    # 非批量出卷。None（默认）= 不分段 → 走上面的单值 question_type / count 老路（字节等价）。给了
    # segments 时**总题数 = 各段 count 之和**（count 入参被忽略）；每题的 question_type 仍逐题走
    # resolve_question_type（ADR-0006，无新裁决）。展开逻辑见 expand_segments。
    segments: list[QuizSegment] | None = None


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
    difficulty: DifficultyLedger | None = None,
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

    ``segments``（``_StartQuizParams`` 字段，批内分段调度，SE-S4）：按题目位置分段指定题型（"前 3
    道选择、后 2 道简答"）——经 ``expand_segments`` 展开成逐题意图后，**每题仍走**
    ``resolve_question_type``（复用 ADR-0006，无新裁决逻辑）。``None``（默认）= 不分段，走单值
    ``question_type`` / ``count`` 老路，行为字节等价改动前；给了 ``segments`` 时总题数 = 各段
    count 之和（``count`` 入参被忽略）。仍逐题一问一答，非批量出卷。

    ``preferences``：透传给 ``assess_once`` 解析出题语言（**偏好 > 中文**）；``None`` 时行为不变
    （走"中文"兜底）。``recently_asked`` / ``_QuizSeedCounter`` 在闭包捕获、跨同一会话的多次
    ``start_quiz`` 累积（复考换角度去重 + 选题种子确定性推进）。空库 → 优雅返回 ``refused``（不调
    任何 LLM）；用户中途取消作答（Responder 抛 ``KeyboardInterrupt``）→ 结束考核、返回已完成部分。

    ``asked_questions``：跨会话持久的已问过台账（``AskedQuestionsLedger``，skeleton-ledger.md
    #8 修复）——透传每题 ``assess_once``，与 ``recently_asked``（会话内）互补，让"换角度去重"这条
    防线在关掉 CLI 重开后依然生效。``None``（默认）= 不接持久层、向后兼容。

    ``difficulty``：跨会话持久的难度台账（``DifficultyLedger``，SE-S3）——透传每题 ``assess_once``，
    销账那刻据三路信号跨档、真跨档才发 ``DIFFICULTY_TIER_CHANGED``。``None``（默认）= 不接难度自
    适应、向后兼容（行为字节等价改动前）。
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
        # 展开成逐题题型意图（SE-S4）：无分段 → [question_type]*clamp(count)（字节等价改动前）；
        # 有分段 → 各段展平。每题仍逐题走 resolve_question_type，无新裁决。
        intents = expand_segments(
            params.segments, count=params.count, question_type=params.question_type
        )
        rounds: list[QuizRoundResult] = []
        for intent in intents:
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
                    question_type=intent,
                    difficulty=difficulty,
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
            "据用户意图填可选旋钮（都不填 = 全库、按掌握状态自适应出题）：\n"
            "· resource_ids（考哪份材料）：从上文【学情】里的库存材料清单认出用户点名的主题对应的 "
            "exact resource_id 填入（可多选）——语义匹配是你的活（'代理通信协议' 认到 ACP 那条）；"
            "认不出对应材料 / 用户没点具体材料 → 别填，宁可诚实拒答也不考错库。\n"
            "· question_type（整批一种题型）：只抽用户原话里的题型意图短语（如 '简答' / '选择题' / "
            "'追问'）原样填入，由代码映射到题型；用户没点题型 → 别填。"
            "别自造题型、别把 '简答' 填成 '选择题'。该 question_type 作用于本次全部 count 道题。\n"
            "· segments（按位置分段的不同题型）：用户想在一轮里**先 X 题一种、再 Y 题另一种**"
            "（如'先 3 道选择再 2 道简答'）时填此项——一个分段列表，每段填 {count, question_type}"
            "（question_type 同上、只抽意图短语）。给了 segments 时**总题数 = 各段 count 之和**"
            "（此时忽略顶层 count），仍是逐题一问一答。简单场景（整批一种题型或自适应）不填 "
            "segments、只用 question_type 或都不填即可。**别用 focus 表达'混合'——focus 是选题"
            "覆盖策略、与题型无关**。\n"
            "· focus（选题聚焦，非题型）：mixed=覆盖优先（默认，先考没考过的），new=只考没考过的"
            "（用户说'考其他的 / 换一批'），weak=复习薄弱（用户说'复习 / 考薄弱'）。\n"
            "工具输入示例——用户'考代理通信协议的简答题'（清单里该主题的 resource_id 记作 r-acp）："
            '{"resource_ids": ["r-acp"], "question_type": "简答"}；'
            "用户'随便考我一道'：{}（都不填 = 全库自动路由）；"
            "用户'先 3 道选择再 2 道简答' → 一次调用填 segments："
            '{"segments": [{"count": 3, "question_type": "选择题"}, '
            '{"count": 2, "question_type": "简答"}]}。'
        ),
        params=_StartQuizParams,
        handler=handler,
        wants_context=True,
    )

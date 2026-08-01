"""单题考核编排——考我 → 选题 → 出题 → 答 → 判卷的确定性 workflow（非自由 ReAct）。

ADR-0004："LLM 判卷，代码记账"。这里的骨架是确定性代码：选题、判决落账（``weak_item_id``）、
发事件全在代码里；LLM 只在"出题"（role=enrich）与"判卷"（role=basic）两个有界槽被调用。
每步都在**同一条事件脊柱**上发事件——trace 形状：

    assessment（根 span）
    ├── model（出题的 model span[enrich]，挂 assessment 下）
    └── model（判卷的 model span[basic]，挂 assessment 下；**仅开放 / 追问有**——选择题判卷是
             确定性代码，无此 span）
    · assessment_refused / question_asked / answer_judged / concept_state_changed / followup_given
      皆 parent=assessment span 的点事件（无 span_id，不进树）

M3.4（题型路由 + 追问）：出题前按被考概念在 Learning Memory 的状态**路由题型**
（``route_question_type``，确定性代码）——首次接触 / 未追踪 → 选择题（MC，确定性判卷、
无判卷 model span），薄弱复考 → 追问（深挖 prompt 变体 + LLM 判卷），观察中 → 开放
（标准 LLM 判卷）。判决为"勉强 / 错"时后置追问"给正解"（确定性代码从 summary + evidence
组文本，发 ``FOLLOWUP_GIVEN``）。故 MC 与 开放 的事件序列不同：MC 路径无判卷 model span。
体现 ADR-0004："LLM 判卷，代码记账"——题型、MC 判卷、给正解全是代码。

失败分两支（照 ingest）：
- **领域优雅分支**：空知识库 → 发 ``ASSESSMENT_REFUSED``（``reason=empty_kb``）+ 优雅返回
  ``status="refused"``，**不调任何 LLM**、**不碰 memory**（eval case 2）。
- **基础设施 / harness 失败**（``QuestionError`` / ``GradingError`` / ``ReplayMiss`` / provider 传输
  异常 / bug）→ 闭合 assessment span 后**原样冒泡**（不吞成 refused 以免掩盖 harness 错误；
  优雅降级属 M6 RecoveryPolicy）。

M3.3（薄弱记忆 + 三态状态机 + 薄弱优先复考）：选题读 ``memory`` 走薄弱优先候选集；判卷后由**代码**
（非 LLM）调 ``memory.record_verdict`` 做三态转移 / 连对销账，并发 ``CONCEPT_STATE_CHANGED``
（在 ``ANSWER_JUDGED`` 之后、``assessment.ended`` 之前）。体现 ADR-0004："LLM 判卷，代码记账"。
"""

from typing import Any, Literal

from pydantic import BaseModel

from grandquiz.domain.learning.asked_questions import AskedQuestionsLedger
from grandquiz.domain.learning.assessment.grading import (
    VerdictLabel,
    grade_answer,
    grade_multiple_choice,
)
from grandquiz.domain.learning.assessment.question import (
    MultipleChoiceQuestion,
    QuestionSpec,
    generate_multiple_choice,
    generate_question,
)
from grandquiz.domain.learning.assessment.routing import (
    QuestionType,
    is_supported_question_type_intent,
    resolve_question_type,
    route_question_type,
)
from grandquiz.domain.learning.assessment.scope import (
    ALL_SCOPE,
    AllScope,
    QuizScope,
    SelectedScope,
    UnresolvedScope,
)
from grandquiz.domain.learning.assessment.selection import Focus, apply_scope, select_target
from grandquiz.domain.learning.assessment_history import assessment_fact
from grandquiz.domain.learning.difficulty import (
    DEFAULT_TIER,
    DifficultyLedger,
    difficulty_prompt_hint,
    distractor_quality_floor,
    target_option_count,
)
from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.learning_facts import LearningFactJournal
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.preference import QUESTION_LANGUAGE_KEY, PreferenceMemory
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.responder import (
    AnswerSubmissionMetadata,
    Responder,
    SubmissionMetadataProvider,
)
from grandquiz.domain.learning.state import LearningStateWriter
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.clock import Rng
from grandquiz.kernel.events import EventEmitter
from grandquiz.providers.base import Provider

# assessment 是 workflow span，用 kernel 级通用类型串（kernel 不认识 "assessment"，泛型建树即可）。
_ASSESSMENT_STARTED = "assessment.started"
_ASSESSMENT_ENDED = "assessment.ended"

# verdict 属"勉强 / 错"→ 该 item 记为薄弱（代码记账，非 LLM 产）+ 触发后置追问（给正解）。
_WEAK_VERDICTS: frozenset[VerdictLabel] = frozenset({"勉强", "错"})
_MAX_ASKED_QUESTIONS_PER_ITEM = 20

# 选择题出题重试头寸（SE-S5b 兜底，2026-07-15 dogfood 洞察）：judge 验收闸门开启（高档，
# quality_floor 非 None）时，"出题→judge→不达标重生成"会吃掉重试预算——dogfood 里一道 tier4 题
# 踩着默认 3 次上限才过，再差一版就 QuestionError 跳整轮。故闸门开启时给更多头寸（5），降低偶发
# 跳题；闸门关闭（默认档 / eval harness）沿用 3（= generate_multiple_choice 默认，字节等价改动前）。
# 这是零风险兜底、不碰难度语义（选项数 / 门槛 / 档位映射不变）；真正的判官策略重设计另议。
_MC_ATTEMPTS_DEFAULT = 3
_MC_ATTEMPTS_WITH_JUDGE_FLOOR = 5


def _resolve_language(preferences: PreferenceMemory | None) -> str:
    """按 **偏好(question_language) > 硬兜底"中文"** 解析出题 / 判卷有效语言（确定性代码，非 LLM）。

    语言是**跨全库的个人设置**（ADR-0005），只来自 Preference Memory，不是材料或标题属性。
    显式设置且非空的 ``question_language`` 偏好生效；否则退到硬兜底"中文"。
    ``preferences`` 为 ``None``（不传偏好的调用方 / 既有 eval harness）时直接走"中文"兜底——向后
    兼容，既有默认路径 message / replay_key 一字不变。
    """
    if preferences is not None:
        pref = preferences.get_preference(QUESTION_LANGUAGE_KEY)
        if pref is not None and pref.value:
            return pref.value
    return "中文"


def _compose_multiple_choice_solution(question: MultipleChoiceQuestion) -> str:
    """从选择题自身的答案键与题目证据组成题目级参考作答。"""

    evidence = "；".join(question.cited_evidence)
    return f"正确选项：{question.options[question.answer_index]}（原文依据：{evidence}）"


class AssessmentResult(BaseModel):
    """一次单题考核的结果：``refused``（空库拒答）或 ``judged``（出题 → 答 → 判卷完成）。

    ``status="refused"`` 时 item_id / verdict / weak_item_id / concept_state / question_type
    均为 None；
    ``status="judged"`` 时据本轮考核填充（``weak_item_id`` 是代码按 ``verdict`` 算出的记账结果，
    非 LLM 产）。``concept_state`` 是本轮记账后被考 item 在 Learning Memory 里的**最终状态**快照
    （薄弱 / 观察中，或 None=未追踪 / 已销账）——透出记账结果，便于直接断言，与 CONCEPT_STATE_CHANGED
    事件互补（事件携完整转移，此处只留终态）。``question_type`` 透出本轮路由到的题型（选择题 /
    开放 / 追问），便于直接断言路由决策（eval case 8），与 QUESTION_ASKED 事件的 ``question_type``
    互补。
    """

    status: Literal["refused", "judged"]
    item_id: str | None = None
    verdict: VerdictLabel | None = None
    weak_item_id: str | None = None
    concept_state: str | None = None
    question_type: QuestionType | None = None


async def assess_once(
    *,
    store: Store,
    provider: Provider,
    responder: Responder,
    memory: Memory,
    emitter: EventEmitter,
    rng: Rng,
    recently_asked: dict[str, list[str]] | None = None,
    asked_questions: AskedQuestionsLedger | None = None,
    focus: Focus = "mixed",
    preferences: PreferenceMemory | None = None,
    scope: QuizScope = ALL_SCOPE,
    candidate_item_ids: list[str] | None = None,
    question_type: str | None = None,
    difficulty: DifficultyLedger | None = None,
    learning_facts: LearningFactJournal | None = None,
) -> AssessmentResult:
    """对**全局 KB** 跑一轮单题考核，全程发事件。见模块 docstring。

    候选池 = 全库（``store.all_items()``），不按标题分区（ADR-0005）；出题 / 判卷语言只来自
    ``preferences``（偏好 > 中文，见 ``_resolve_language``）。

    ``recently_asked``：会话内**已问过**台账（item_id → 已问过的题目文本列表），由考核循环入口
    （``run_quiz``）持有并跨轮累积、下传做去重（"LLM 判卷，代码记账"——已问过是代码持有的状态）。
    默认 ``None`` = 不去重、向后兼容：既有测试 / eval harness 不传它时行为一字不变（出题函数收到
    空 ``asked_before`` → message / replay_key / prompt 版本号不变）。``recently_asked`` 的 keys
    同时作**本会话已考过**的 item 集下传选题（``asked_item_ids``），实现 R1-S7 的覆盖优先：先考
    未考过的，考完一遍才兜底复考薄弱（修 dogfood 的"锁死同一 item"）——**刻意保持会话范围**：
    "mixed"覆盖优先问的是"这次坐下来学，有没有还没碰过的"，不该被跨会话历史污染成"这个概念
    这辈子有没有被问过"（那样用久的知识库会让"覆盖优先"永远无题可选）。

    ``asked_questions``：**跨会话持久**的已问过台账（``AskedQuestionsLedger`` 协议，见
    ``asked_questions.py``；skeleton-ledger.md #8 修复）——修的是"关掉 CLI 重开，复考同一薄弱
    概念可能被逐字重问上次会话问过的题"这个真实 bug（``recently_asked`` 只在**单次会话内**生效）。
    默认 ``None`` = 不接持久层、向后兼容。给 ``generate_question``/``generate_multiple_choice``
    的 ``asked_before`` 是 ``recently_asked``（本会话）与 ``asked_questions``（历史会话）两路
    已问文本的**并集**——两条防线互补而非互相替代：会话内防线永远存在（哪怕不接持久层），持久层
    只是把它的记忆窗口从"这次会话"延伸到"所有会话"。成功发出 ``QUESTION_ASKED`` 后，本轮新题
    文本同时追加进两边（各自独立地：``recently_asked`` 给的仍是本会话覆盖优先要用的 keys）。

    ``focus``：选题聚焦档位（``mixed`` 默认 / ``new`` 只考未考过 / ``weak`` 复习薄弱），透传
    ``select_target``。默认 ``mixed`` 向后兼容——旧调用方不传它时行为等价于"未考过优先、否则全集"
    （无会话已考态时即全集随机，同改动前）。**仅影响选题调用处**：判卷 / 记账 / 事件序一律不动。

    ``preferences``：显式偏好台账（Preference Memory）。出题 / 判卷前经 ``_resolve_language`` 解析
    有效语言，优先级 **偏好（``question_language``）> 硬兜底"中文"**——偏好下传出题 / 判卷的
    ``{{LANGUAGE}}`` 槽（"LLM 判卷，代码记账"：语言解析是确定性代码）。默认 ``None`` = 不读偏好、
    向后兼容：既有测试 / eval harness 不传它时有效语言 == "中文"、发出的 message / replay_key 一字
    不变。

    ``resource_ids``：**目录式 scope**（GKB-S4，修 #1 考错库）——把全库候选池按 exact resource_id
    收窄到指定材料再选题（``apply_scope``，纯代码、无模糊匹配）。默认 ``None`` = 全库
    （``apply_scope`` 恒等返回，字节等价旧行为：既有测试 / eval / cassette 一字不变）。语义匹配是
    LLM 的活（目录注入让它把用户意图翻成 exact resource_id），代码只做确定性精确过滤。判空分两支：
    None scope 且全库空 → ``empty_kb``（旧语义）；非 None scope 且过滤后空 → 新 ``empty_scope``
    （在 ``select_target`` **之前**、**不调任何 LLM**——命中不了诚实拒答，不静默考别的库）。

    ``question_type``：**用户显式题型意图短语**（GKB-S5，修 #1 错题型；ADR-0006）——用户点了题型
    （"出简答题"）时透传的原文短语。路由处 ``effective = resolve_question_type(question_type,
    state)``：显式意图**胜过**记忆状态自适应路由（``route_question_type``），未知 / 缺省回落自适应
    （fail-soft），且短答类意图代码层禁止映射到"选择题"（护栏，防复现 #1）。复用既有三题型 →
    **不新增 prompt / 不新增 grading 路径**。默认 ``None`` = 走自适应路由，**字节等价旧行为**
    （既有测试 / eval / cassette 一字不变）。``QUESTION_ASKED`` payload 同记 ``routed``（自适应
    会给的）与 ``effective``（实际用的），供 eval 断言意图透传；``AssessmentResult.question_type``
    透出 effective。

    ``difficulty``：**难度台账**（``DifficultyLedger`` 协议，见 ``difficulty.py``；SE-S3 接线）——把
    SE-S1 台账 + SE-S2 跨档规则接进真实考核编排的注入点。默认 ``None`` = **不接难度自适应、行为逐
    字节等价改动前**（难度块整个 gated 在 ``difficulty is not None``，连销账前那次 ``record_of`` 读
    都不做 → 真正零开销零行为变化；既有测试 / eval harness / golden cassette 不传它时 message /
    replay_key / 事件序列一字不变）。本期难度**只在"销账"这一确定时刻更新**（SE-S3 决策：销账轮数
    信号只在 ``ConceptRecord`` 被删那一刻可捕获，非销账轮不动难度，"每轮微调"留后续）。销账那刻据
    三路信号（销账轮数 = 转移前 ``verdict_history`` 长度 / 答题耗时 = ``QUESTION_ASKED``→
    ``ANSWER_JUDGED`` 时间戳差 / 判决分布 = 是否掉过"勉强"）调 ``next_tier``，**仅真跨档**
    （``new != current``）才写台账 + 发 ``DIFFICULTY_TIER_CHANGED``（PRD 决策 6）。难度**落到出题**
    从 SE-S5a 起：选择题分支读该 item 当前档 → 目标选项数（``target_option_count``）下传出题
    （档越高、干扰项越多）；SE-S6 起开放 / 追问分支读档 → 难度提示（``difficulty_prompt_hint``）
    下传出题（高档逼边界 / 反例 / 跨概念，低档问核心定义）——**软杠杆，如实承认比 MC 硬杠杆软**。
    """
    # a. 开 assessment span（根）。此后任何未预期异常都必须闭合它（见末尾 except）。
    #    先读全库候选池（全局 KB，非 task 局部——修 #2 跨会话丢知识），再按 scope 收窄（apply_scope，
    #    resource_ids=None → 恒等全库）。ASSESSMENT_STARTED payload 带**有效 resource_ids + 命中数**
    #    （candidate_pool_size = scope 后池大小 = 命中数；判别力字段，供 trace/eval 断言选了哪库）。
    resource_ids = scope.resource_ids if isinstance(scope, SelectedScope) else None
    items = (
        []
        if isinstance(scope, UnresolvedScope)
        else apply_scope(store.all_items(), resource_ids, item_ids=candidate_item_ids)
    )
    assessment_span = emitter.new_span_id()
    scope_payload: dict[str, Any] = {
        "mode": scope.mode,
        "resource_ids": resource_ids,
        "candidate_pool_size": len(items),
    }
    if candidate_item_ids is not None:
        scope_payload["facet_filtered"] = True
    if isinstance(scope, UnresolvedScope):
        scope_payload["requested_label"] = scope.requested_label
    emitter.emit(
        _ASSESSMENT_STARTED,
        span_id=assessment_span,
        payload=scope_payload,
    )
    try:
        if isinstance(scope, UnresolvedScope):
            reason = "unresolved_scope"
            emitter.emit(
                LearningEvent.ASSESSMENT_REFUSED,
                parent_span_id=assessment_span,
                payload={"reason": reason, "requested_label": scope.requested_label},
            )
            emitter.emit(
                _ASSESSMENT_ENDED,
                span_id=assessment_span,
                payload={"ok": True, "status": "refused", "reason": reason},
            )
            return AssessmentResult(status="refused")

        # b. 空库 / 空 scope 拒答：不调任何 LLM，优雅返回 refused。两支——None scope 且全库空 →
        #    empty_kb（eval case 2，逐字节不动）；非 None scope 过滤后空 → empty_scope（在选题前）。
        if not items:
            reason = (
                "empty_facet"
                if candidate_item_ids is not None
                else ("empty_kb" if isinstance(scope, AllScope) else "empty_scope")
            )
            emitter.emit(
                LearningEvent.ASSESSMENT_REFUSED,
                parent_span_id=assessment_span,
                payload={"reason": reason},
            )
            emitter.emit(
                _ASSESSMENT_ENDED,
                span_id=assessment_span,
                payload={"ok": True, "status": "refused", "reason": reason},
            )
            return AssessmentResult(status="refused")

        # c. 选题（确定性，种子化 rng）。会话内已考过的 item（recently_asked 的 keys）+ focus 决定
        #    候选集：mixed 覆盖优先（先考未考过、否则兜底薄弱），new 只考未考过，weak 复习薄弱。
        asked_item_ids: set[str] = set(recently_asked) if recently_asked is not None else set()
        target = select_target(
            items, rng=rng, memory=memory, asked_item_ids=asked_item_ids, focus=focus
        )

        # d. 题型决策（确定性代码；决策上脊柱供断言）。routed = 记忆状态自适应会给的题型；
        #    effective = 实际用的——用户显式意图（question_type 短语）胜过自适应，未知 / 缺省回落
        #    自适应（ADR-0006）。短答意图 ↛ 选择题的护栏在 resolve_question_type / 冻结映射表里。
        state = memory.state_of(target.item_id)
        routed = route_question_type(state)
        effective = resolve_question_type(question_type, state)

        # 有效语言解析（确定性代码）：偏好 > 中文。下传出题 / 判卷的 {{LANGUAGE}} 槽。
        language = _resolve_language(preferences)

        # e. 分型出题（role=enrich）+ 校验门（缝 3）。选择题走 MC 出题；追问用深挖 prompt 变体；
        #    开放走标准出题。三者都发 QUESTION_ASKED（带 question_type，锚定真实 item + 非空证据）。
        #    从会话内 + 跨会话"已问过"台账取被考 item 的已问列表下传做去重（都为 None = 不去重、
        #    向后兼容；只接一边也行——两条防线互补，见函数 docstring）。
        asked_before: list[str] = []
        if recently_asked is not None:
            recent = recently_asked.get(target.item_id, [])
            asked_before += recent[-_MAX_ASKED_QUESTIONS_PER_ITEM:]
        if asked_questions is not None:
            asked_before += asked_questions.asked_before(
                target.item_id, limit=_MAX_ASKED_QUESTIONS_PER_ITEM
            )
        asked_before = asked_before[-_MAX_ASKED_QUESTIONS_PER_ITEM:]
        mc: MultipleChoiceQuestion | None = None
        question_spec: QuestionSpec | None = None
        if effective == "选择题":
            # SE-S5a 选择题硬杠杆①：难度**只在概念离开默认档后**才落到题面——接了难度台账
            # （difficulty is not None）且被考 item 的档 ≠ 默认档（3）时，读档 → 目标选项数
            # （档越高、干扰项越多、越难靠排除法蒙对），下传出题请求。**默认档（含全部新概念）与
            # 未接台账（eval harness）→ num_options=None、不注入选项数约束**——
            # generate_multiple_choice 发出的 message / replay_key 逐字节等价改动前。刻意只在
            # "已适应"概念上加杠杆：新概念保持出题官自然给的选项数（不把"默认 MC 提到 4 项硬底"
            # 这个独立的题面质量变更混进本增量，也免默认路径重试耗尽风险）；升/降档后才收紧/放宽。
            current_tier = difficulty.tier_of(target.item_id) if difficulty is not None else None
            num_options = (
                target_option_count(current_tier)
                if current_tier is not None and current_tier != DEFAULT_TIER
                else None
            )
            # SE-S5b 选择题硬杠杆②：干扰项质量 judge 验收闸门。distractor_quality_floor 内部**只对
            # 高于默认档（3）的 tier 返回非 None**（tier 4→"较弱干扰"、5→"合理干扰"，≤3→None），故
            # 不必在此再判 tier != DEFAULT_TIER；但 current_tier is None（未接难度台账 / difficulty
            # =None）时必须 None——保证默认路径 / eval harness 一次都不调 judge、字节等价改动前。
            quality_floor = (
                distractor_quality_floor(current_tier) if current_tier is not None else None
            )
            # judge 闸门开启时给出题更多重试头寸（免踩线跳题，见 _MC_ATTEMPTS_* 注释）；关闭时
            # 沿用默认 3，与改动前逐字节等价（max_attempts 不进 message / replay_key）。
            mc_attempts = (
                _MC_ATTEMPTS_WITH_JUDGE_FLOOR if quality_floor is not None else _MC_ATTEMPTS_DEFAULT
            )
            mc = await generate_multiple_choice(
                target,
                provider=provider,
                emitter=emitter,
                parent_span_id=assessment_span,
                language=language,
                asked_before=asked_before,
                num_options=num_options,
                quality_floor=quality_floor,
                max_attempts=mc_attempts,
            )
            question_text = mc.question
            asked_evidence = list(mc.cited_evidence)
        else:
            prompt_name = "question_probe" if effective == "追问" else "question_generate"
            # SE-S6 开放 / 追问难度软杠杆：读该 item 当前档 → 难度提示（高档逼边界 / 反例 / 跨概念，
            # 低档问核心定义），下传出题请求。读法与上方 MC 分支的 current_tier 一致（两分支互斥、
            # 每次执行只读一次，无重复读）。difficulty_prompt_hint **内部对默认档（3）返回 None**、
            # 只对非默认档给提示；current_tier is None（未接难度台账 / difficulty=None）→ hint=None
            # → generate_question 不追加任何 message、发出的 message / replay_key / prompt 版本号
            # 逐字节等价改动前（cassette 不破的命根）。**软性如实标注**：这条比 MC 硬杠杆软——只保证
            # 不同档追加不同提示，不保证高档题真的更难（深度主观、超出确定性可断言范围）。
            current_tier = difficulty.tier_of(target.item_id) if difficulty is not None else None
            difficulty_hint = (
                difficulty_prompt_hint(current_tier) if current_tier is not None else None
            )
            question_spec = await generate_question(
                target,
                provider=provider,
                emitter=emitter,
                parent_span_id=assessment_span,
                prompt_name=prompt_name,
                language=language,
                asked_before=asked_before,
                difficulty_hint=difficulty_hint,
            )
            question_text = question_spec.question
            asked_evidence = list(question_spec.cited_evidence)
        asked_payload: dict[str, Any] = {
            "item_id": target.item_id,
            "question": question_text,
            "cited_evidence": asked_evidence,
            # question_type 保留为**有效题型**（= effective）向后兼容既有断言；additive 另记 routed
            # （自适应会给的）与 effective（实际用的），供 eval 断言用户意图是否透传（ADR-0006）。
            "question_type": effective,
            "routed": routed,
            "effective": effective,
        }
        if mc is not None:
            # MC 另带 options 供"用户视图"；answer_index 刻意不进事件（不泄露答案键——判卷走 in-code
            # mc 对象的确定性比对，答案键仍在 model span 输出里可供 trace / replay 复查）。
            asked_payload["options"] = list(mc.options)
        elif question_spec is not None:
            asked_payload["expected_points"] = [
                point.model_dump(mode="json") for point in question_spec.expected_points
            ]
        # 接住返回的 AgentEvent（带注入 Clock 的 .ts）——SE-S3 决策 B：本轮答题耗时近似 =
        # (ANSWER_JUDGED.ts − QUESTION_ASKED.ts)。接住返回值不改变发射行为、零副作用（replay 下
        # ManualClock 使其确定、生产 SystemClock 下是真实墙上时间——都对）；耗时只被销账证据读取。
        q_event = emitter.emit(
            LearningEvent.QUESTION_ASKED, parent_span_id=assessment_span, payload=asked_payload
        )
        # 会话内去重可立即更新；持久已问题目与判决状态在下方同一事务提交。
        if recently_asked is not None:
            recently_asked.setdefault(target.item_id, []).append(question_text)

        # f. 作答（注入的 responder，async）。选择题把 options 透传给 responder，开放 / 追问传 None
        #    （ScriptedResponder 忽略 options）。
        answer = await responder.answer(
            question_text, options=list(mc.options) if mc is not None else None
        )
        submission = (
            responder.last_submission_metadata()
            if isinstance(responder, SubmissionMetadataProvider)
            else AnswerSubmissionMetadata(
                answer_format="choice" if mc is not None else "natural_language"
            )
        )

        # g. 分型判卷。选择题 → 确定性代码（**不调 LLM**，无判卷 model span）；开放 / 追问 → LLM
        #    判卷（role=basic）+ 校验门（缝 3）。两路统一得到 VerdictLabel + cited_evidence。
        # verdict_reason：判官一句话诊断，只在开放 / 追问的 LLM 判卷槽产出（MC 判卷是代码、无判官
        # → 空串）。additive 进 ANSWER_JUDGED 供 printer 展示；**不参与记账**（weak_item_id 仍按
        # verdict 算）。
        verdict_reason = ""
        matched_points: list[dict[str, str]] = []
        missing_points: list[dict[str, str]] = []
        if mc is not None:
            verdict_label: VerdictLabel = grade_multiple_choice(answer, mc)
            judged_evidence = list(mc.cited_evidence)
            correct_option = {
                "point_id": "correct_option",
                "description": f"选择正确选项：{mc.options[mc.answer_index]}",
            }
            if verdict_label == "对":
                matched_points = [correct_option]
                diagnosis = "complete"
            else:
                missing_points = [correct_option]
                diagnosis = "incorrect_choice"
        else:
            assert question_spec is not None
            verdict = await grade_answer(
                question_spec,
                answer,
                provider=provider,
                emitter=emitter,
                parent_span_id=assessment_span,
                language=language,
            )
            verdict_label = verdict.verdict
            verdict_reason = verdict.reason
            judged_evidence = list(verdict.cited_evidence)
            points_by_id = {
                point.point_id: point.description for point in question_spec.expected_points
            }
            matched_points = [
                {"point_id": point_id, "description": points_by_id[point_id]}
                for point_id in verdict.matched_points
            ]
            missing_points = [
                {"point_id": point_id, "description": points_by_id[point_id]}
                for point_id in verdict.missing_points
            ]
            diagnosis = verdict.diagnosis

        # h. 代码记账：verdict 属"勉强 / 错"→ weak_item_id = 被考 item；"对"→ None（不由 LLM 产）。
        weak_item_id = target.item_id if verdict_label in _WEAK_VERDICTS else None
        # 接住返回的 AgentEvent（决策 B）——与 q_event 一起算销账那刻的答题耗时近似。
        j_event = emitter.emit(
            LearningEvent.ANSWER_JUDGED,
            parent_span_id=assessment_span,
            payload={
                "item_id": target.item_id,
                "verdict": verdict_label,
                "weak_item_id": weak_item_id,
                "answer": answer,
                "reason": verdict_reason,
                "diagnosis": diagnosis,
                "matched_points": matched_points,
                "missing_points": missing_points,
                "cited_evidence": judged_evidence,
            },
        )

        # i. 持久状态原子提交：已问题目、Learning Memory 与 Difficulty 要么全成、要么全回滚。
        elapsed_ms = round((j_event.ts - q_event.ts) * 1000)
        committed = LearningStateWriter(
            memory=memory,
            asked_questions=asked_questions,
            difficulty=difficulty,
            learning_facts=learning_facts,
        ).commit_judgement(
            item_id=target.item_id,
            question=question_text,
            verdict=verdict_label,
            elapsed_ms=elapsed_ms,
            learning_fact=(
                assessment_fact(
                    question_event=q_event,
                    judgement_event=j_event,
                    item_id=target.item_id,
                    question_text=question_text,
                    answer_text=answer,
                    verdict=verdict_label,
                    adaptive_question_type=routed,
                    effective_question_type=effective,
                    routing_source=(
                        "user_override"
                        if is_supported_question_type_intent(question_type)
                        else "adaptive"
                    ),
                    input_modality=submission.input_modality,
                    answer_format=submission.answer_format,
                    evidence_revealed_before_answer=(submission.evidence_revealed_before_answer),
                    elapsed_ms=elapsed_ms,
                    question_generation_version=load_prompt(
                        "question_multiple_choice"
                        if mc is not None
                        else ("question_probe" if effective == "追问" else "question_generate")
                    ).version,
                    grading_kind="deterministic" if mc is not None else "model",
                    grading_version=(
                        "multiple-choice-exact.v1"
                        if mc is not None
                        else load_prompt("answer_grade").version
                    ),
                )
                if learning_facts is not None
                else None
            ),
        )
        transition = committed.transition
        emitter.emit(
            LearningEvent.CONCEPT_STATE_CHANGED,
            parent_span_id=assessment_span,
            payload={
                "item_id": transition.item_id,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "consecutive_correct": transition.consecutive_correct,
            },
        )

        if committed.difficulty_change is not None:
            change = committed.difficulty_change
            emitter.emit(
                LearningEvent.DIFFICULTY_TIER_CHANGED,
                parent_span_id=assessment_span,
                payload={
                    "item_id": target.item_id,
                    "concept": target.concept,
                    "from_tier": change.from_tier,
                    "to_tier": change.to_tier,
                    "reason": change.reason,
                },
            )

        if committed.learning_fact is not None and learning_facts is not None:
            emitter.emit(
                LearningEvent.ASSESSMENT_JUDGEMENT_COMMITTED,
                parent_span_id=assessment_span,
                payload=committed.learning_fact.model_dump(mode="json"),
            )
            learning_facts.mark_published(committed.learning_fact.event_id)

        # j. 后置追问：判"勉强 / 错"→ 给本题正解，发 FOLLOWUP_GIVEN（在
        #    CONCEPT_STATE_CHANGED 之后、assessment.ended 之前）。
        #    判"对"不发；MC 判错同样触发（MC 无"勉强"）。
        if verdict_label in _WEAK_VERDICTS:
            if mc is not None:
                correct_answer = _compose_multiple_choice_solution(mc)
            elif question_spec is not None:
                correct_answer = question_spec.reference_answer
            else:
                raise AssertionError("非选择题判卷缺少 QuestionSpec")
            emitter.emit(
                LearningEvent.FOLLOWUP_GIVEN,
                parent_span_id=assessment_span,
                payload={
                    "item_id": target.item_id,
                    "correct_answer": correct_answer,
                },
            )

        # k. 闭合 assessment span。
        emitter.emit(
            _ASSESSMENT_ENDED, span_id=assessment_span, payload={"ok": True, "status": "judged"}
        )
        return AssessmentResult(
            status="judged",
            item_id=target.item_id,
            verdict=verdict_label,
            weak_item_id=weak_item_id,
            concept_state=memory.state_of(target.item_id),
            question_type=effective,
        )
    except Exception as exc:
        # 非领域异常（QuestionError / GradingError / ReplayMiss / provider 基础设施错误 / bug）：
        # 闭合 assessment span 后原样冒泡。不吞成 refused——那会掩盖 harness / 基础设施错误。
        emitter.emit(
            _ASSESSMENT_ENDED, span_id=assessment_span, payload={"ok": False, "error": repr(exc)}
        )
        raise

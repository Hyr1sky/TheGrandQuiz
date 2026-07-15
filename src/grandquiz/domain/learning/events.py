"""学习领域事件——类型常量命名空间（决策 4 的轻量约定）。

不给每个事件建 typed 类：领域事件就是一个命名空间字符串 + 一份 JSON-able payload
（``payload = 相应模型的 model_dump()``）。它们经 kernel 的 ``emit()`` 上同一条脊柱
（见 ``kernel/events.py`` 的 ``AgentEvent`` 信封）；kernel 泛型持久化 / 分发它们、**不认识**
具体类型（M2 已验证：``test_trace.py::test_trace_store_persists_unknown_domain_event``）。

本模块**不 import kernel**（只是字符串常量），亦不 import 领域模型——保持"事件是信封、
kernel 领域无关"这一脊柱设计不被反向耦合。
"""


class LearningEvent:
    """学习领域事件类型常量。命名空间前缀 ``learning.``，kernel 不认识。

    ingest 竖切的事件时序（本任务只定常量，发射在后续步骤）：
    资源建档 → 深读产候选 → 审批 → 逐个入库；深读失败走 fetch_failed 分支、不入库。
    """

    RESOURCE_CREATED = "learning.resource_created"
    RESOURCE_READ = (
        "learning.resource_read"  # 抓取成功回填内容后：资源状态跃迁上脊柱（对称于 fetch_failed）
    )
    RESOURCE_FETCH_FAILED = "learning.resource_fetch_failed"  # eval case 7：深读失败，不产幽灵 item
    ITEMS_EXTRACTED = "learning.items_extracted"  # Reader 产候选（审批前预览）
    RESOURCE_APPROVED = "learning.resource_approved"  # 用户经审批门通过
    ITEM_CREATED = "learning.item_created"  # 逐个入库（审批后）——eval case 1

    # M3.2 单题考核竖切（考我 → 选题 → 出题 → 答 → 判卷）：
    ASSESSMENT_REFUSED = "learning.assessment_refused"  # eval case 2：空库拒答，不调任何 LLM
    QUESTION_ASKED = (
        "learning.question_asked"  # 出题：锚定真实 item + 非空 cited_evidence（case 3）
    )
    ANSWER_JUDGED = "learning.answer_judged"  # 判卷：verdict + weak_item_id（LLM 判卷，代码记账）

    # M3.3 薄弱记忆 + 三态状态机：每轮判卷后代码记账（写 Learning Memory）都发此事件、把记账结果
    # 上脊柱（eval case 4 / 6，payload 含 from_state / to_state / consecutive_correct）。
    # **无转移时也发**（如答对未追踪概念 → from/to 皆 None），保证每轮记账都可断言、事件序列稳定。
    CONCEPT_STATE_CHANGED = "learning.concept_state_changed"

    # M3.4 题型路由 + 追问：判决为"勉强 / 错"时的后置追问——给正解（确定性代码，从被考 item 的
    # summary + evidence 组正解文本，非 LLM 产）。payload 含 item_id + correct_answer；判"对"不发。
    # 在 CONCEPT_STATE_CHANGED 之后、assessment.ended 之前（见 assessment 模块 docstring 时序）。
    FOLLOWUP_GIVEN = "learning.followup_given"

    # SE-S3 难度自适应透明展示：销账那一刻据三路信号（销账轮数 / 答题耗时 / 判决分布）跨档时发
    # 此事件——**仅真跨档（new != current）才发**（照 CONCEPT_STATE_CHANGED 的"无转移不发"先例，
    # 避免噪声）。payload 含 item_id / concept / from_tier / to_tier / reason（据哪路信号跨档的
    # 简短说明，取自 next_tier 的可解释性）。kernel 不认识它（领域无关脊柱，经泛型 emit 上脊柱）。
    DIFFICULTY_TIER_CHANGED = "learning.difficulty_tier_changed"

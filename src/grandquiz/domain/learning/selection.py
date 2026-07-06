"""选题——从某任务的 KnowledgeItem 里挑一个考核目标（确定性、可回放）。

"LLM 判卷，代码记账"（ADR-0004）：选题是**确定性代码**，不是 LLM 的活。随机性走**注入的
种子化 rng**（``new_rng(seed)``），同 seed 恒得同结果，故整条考核竖切可逐字节回放
（domain 自身禁 ``random`` / ``time``）。

**薄弱优先**（M3.3，eval case 5）：有薄弱概念（Learning Memory 的薄弱 ∪ 观察中非空）时，
候选集只含这些概念对应的 item（**新概念不进集**，先补最该补的）；否则从全集选。代码构造候选集、
LLM 只在集内被挑到——选题数据源是 Learning Memory（ADR-0003）。``memory`` 为 None（未接记忆，
如 M3.2 的旧调用）时退化为全集随机，故本函数签名向后兼容、旧调用方无需改动。
"""

from grandquiz.domain.learning.memory import LearningMemory
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.kernel.clock import Rng


def select_target(
    items: list[KnowledgeItem], *, rng: Rng, memory: LearningMemory | None = None
) -> KnowledgeItem:
    """从 ``items`` 中确定性地选一个考核目标（``rng.choice``，同 seed 同结果）。

    ``memory`` 为 None 或其薄弱集为空 → 从全部 ``items`` 选（保持 M3.2 行为）；否则候选集 =
    薄弱 ∪ 观察中概念对应的 item（新概念被排除，eval case 5），从中选。薄弱概念的 item 若已不在
    ``items`` 里致候选集为空 → 兜底回退全集（护栏，正常不该发生：记忆里的 item 应仍在库）。

    空 ``items`` → ``ValueError``：空库时调用方应先走"拒答"分支（发 ``ASSESSMENT_REFUSED``），
    根本不该走到选题这一步（eval case 2）。这里的 raise 是防御性护栏，不是正常控制流。
    """
    if not items:
        raise ValueError("空知识库不该进入选题——调用方应先走拒答分支（eval case 2）")
    if memory is not None:
        weak_ids = memory.weak_item_ids()
        if weak_ids:
            candidates = [item for item in items if item.item_id in weak_ids]
            if candidates:  # 兜底：候选集为空（薄弱 item 已不在库）时回退全集
                return rng.choice(candidates)
    return rng.choice(items)

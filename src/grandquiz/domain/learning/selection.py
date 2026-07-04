"""选题——从某任务的 KnowledgeItem 里挑一个考核目标（确定性、可回放）。

"LLM 判卷，代码记账"（ADR-0004）：选题是**确定性代码**，不是 LLM 的活。M3.2 的选题规则
最简——从任务全部 item 里等概率随机挑一个，随机性走**注入的种子化 rng**（``new_rng(seed)``），
同 seed 恒得同结果，故整条考核竖切可逐字节回放（domain 自身禁 ``random`` / ``time``）。

薄弱优先候选集是 M3.3 的**内部升级**：届时改为"先查 Learning Memory 构造薄弱优先候选集
（有薄弱概念时新概念不进集），再在集内 ``rng.choice``"。**本函数签名此刻就焊死、不随内部实现变**，
调用方（``assess_once``）无需改动。
"""

from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.kernel.clock import Rng


def select_target(items: list[KnowledgeItem], *, rng: Rng) -> KnowledgeItem:
    """从 ``items`` 中确定性地选一个考核目标（``rng.choice``，同 seed 同结果）。

    空列表 → ``ValueError``：空库时调用方应先走"拒答"分支（发 ``ASSESSMENT_REFUSED``），
    根本不该走到选题这一步（eval case 2）。这里的 raise 是防御性护栏，不是正常控制流。
    """
    if not items:
        raise ValueError("空知识库不该进入选题——调用方应先走拒答分支（eval case 2）")
    return rng.choice(items)

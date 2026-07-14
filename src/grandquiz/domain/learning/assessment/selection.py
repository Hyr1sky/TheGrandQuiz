"""选题——从某任务的 KnowledgeItem 里挑一个考核目标（确定性、可回放）。

"LLM 判卷，代码记账"（ADR-0004）：选题是**确定性代码**，不是 LLM 的活。随机性走**注入的
种子化 rng**（``new_rng(seed)``），同 seed 恒得同结果，故整条考核竖切可逐字节回放
（domain 自身禁 ``random`` / ``time``）。

**覆盖优先 + 兜底 remediation + 可选 focus**（R1-S7，eval case 5/6）：早先的"薄弱优先排他"
（有薄弱 → 候选只含薄弱、排除新概念）会把一题答错永久**锁死**同一 item（dogfood：10 知识点
只考 1、全追问全错死循环）。改为**会话内已考去重 + focus 分档**：

- ``focus="mixed"``（默认）：候选 = **未考过（unasked）** 若非空 → 否则**薄弱** 若非空 → 否则全集。
  关键——有薄弱 + 有未考过时选未考过（覆盖优先，不锁死），考完一遍才兜底回来复考薄弱。
- ``focus="new"``（"考其他的 / 没考过的"）：未考过 若非空 → 否则全集（**不兜底薄弱**）。
- ``focus="weak"``（"复习薄弱"）：薄弱 若非空 → 否则未考过 → 否则全集。

``asked_item_ids`` 是**本会话已考过**的 item 集（由考核循环 / start_quiz 持有并跨轮累积下传）；
默认空集 = 不去重，向后兼容旧调用方（首题、无会话态时行为不变）。候选集内仍 ``rng.choice`` 确定性
选。``memory`` 为 None（未接记忆）时薄弱集为空，故 mixed / new 退化为"未考过优先、否则全集"。
"""

from collections.abc import Collection
from typing import Literal, assert_never

from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.models import KnowledgeItem
from grandquiz.kernel.clock import Rng

# 选题聚焦档位：覆盖优先（默认）/ 只考未考过 / 复习薄弱——assess_once / start_quiz 按用户意图下传。
Focus = Literal["mixed", "new", "weak"]


def apply_scope(items: list[KnowledgeItem], resource_ids: list[str] | None) -> list[KnowledgeItem]:
    """目录式 scope 的**上游预过滤**（纯代码、确定性，无模糊匹配）——GKB-S4，修 #1 考错库。

    ``resource_ids is None`` → **恒等返回** ``items``（默认全库；字节等价旧行为）。否则按
    **exact resource_id** 保序过滤出 ``item.resource_id in set(resource_ids)`` 的 item——**保
    item_id 升序、绝不重排序**（重排即破 ``select_target`` 里 ``rng.choice`` 的下标稳定 → replay
    对不齐）。``resource_ids`` 的先后不影响输出序（只做成员归属，不按 scope 排序）；空命中（含非
    None 空列表）→ 空列表，调用方据此走 ``empty_scope`` 拒答。

    语义匹配是 LLM 的活（S3 目录注入 + 工具 description 让它把用户意图翻成 exact resource_id），
    这里只做代码侧的**精确成员过滤**——刻意不写模糊子串 / 分词匹配（绕开大小写 / 中文规范化
    parity 陷阱，replay 逐字节稳）。是 ``select_target`` **之前**的一层过滤，``select_target`` 签名
    及其既有 caller 零改。
    """
    if resource_ids is None:
        return items
    allowed = set(resource_ids)
    return [item for item in items if item.resource_id in allowed]


def _candidates(
    focus: Focus,
    *,
    unasked: list[KnowledgeItem],
    weak: list[KnowledgeItem],
    items: list[KnowledgeItem],
) -> list[KnowledgeItem]:
    """按 focus 定候选集（纯代码；空列表 falsy → 落到下一优先级 / 全集兜底）。见模块 docstring。"""
    if focus == "mixed":
        return unasked or weak or items
    if focus == "new":
        return unasked or items
    if focus == "weak":
        return weak or unasked or items
    assert_never(focus)  # 穷尽 Focus；未来加档位会在此炸出，而非静默落进某分支


def select_target(
    items: list[KnowledgeItem],
    *,
    rng: Rng,
    memory: Memory | None = None,
    asked_item_ids: Collection[str] = frozenset(),
    focus: Focus = "mixed",
) -> KnowledgeItem:
    """从 ``items`` 中确定性地选一个考核目标（``rng.choice``，同 seed 同结果）。

    候选集按 ``focus`` + ``asked_item_ids`` + ``memory`` 薄弱集构造（见模块 docstring）；空候选
    在各 focus 下都最终兜底到全集，故绝不返回空 / raise（除非 ``items`` 本身为空）。

    空 ``items`` → ``ValueError``：空库时调用方应先走"拒答"分支（发 ``ASSESSMENT_REFUSED``），
    根本不该走到选题这一步（eval case 2）。这里的 raise 是防御性护栏，不是正常控制流。
    """
    if not items:
        raise ValueError("空知识库不该进入选题——调用方应先走拒答分支（eval case 2）")
    asked = set(asked_item_ids)
    unasked = [item for item in items if item.item_id not in asked]
    weak_ids: set[str] = memory.weak_item_ids() if memory is not None else set()
    weak = [item for item in items if item.item_id in weak_ids]
    return rng.choice(_candidates(focus, unasked=unasked, weak=weak, items=items))

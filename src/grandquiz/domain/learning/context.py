"""学情上下文 provider——把 Learning Memory（薄弱概念）+ Preference Memory（偏好）渲成一段紧凑
"学情"文本，供 ReAct agent **不调工具就知道**学习者薄弱点 + 偏好、更聪明地编排考核。

这是"记忆互通复用"的兑现：同一份持久 SQLite 记忆，既是考核循环选题的数据源，也经此注入 ReAct
上下文。渲染是**确定性纯代码**（薄弱概念按 ``item_id`` 升序，无 clock / random / time），故 replay
对得齐。

两个出口：
- ``render_learner_context``：纯渲染（取当前 memory / preferences 快照 → 字符串），可直接单测。
- ``learner_context_provider``：返回一个**闭包**（捕获 store / memory / preferences / task 引用），
  作为 kernel ``ContextBuilder`` 某分区的 ``Callable[[], str]`` provider。ContextBuilder 每次 build
  调它现取 → 学情随考核推进刷新（同一会话下一回合的注入反映最新薄弱账）。这条 domain→kernel 的
  传入合法（kernel 只认字符串 provider，不认识本模块的领域类型）。

**可扩展**：加一项偏好 = 往 ``_PREFERENCE_LABELS`` 加一条 (key, 标签)；加一类学情段（如掌握进度）
= 加一个 ``_render_*`` 段函数并在 ``render_learner_context`` 里串上——渲染项增列表、不改注入机制。
"""

from collections.abc import Callable

from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.preference import QUESTION_LANGUAGE_KEY, PreferenceMemory
from grandquiz.domain.learning.store import Store

# 学情块的抬头：明确这是给 agent 编排用的背景、不是要它直接读给用户的话术。
_HEADER = "【学情（供你更聪明地编排考核，不要直接读给用户，也不要据此自己出题 / 判卷）】"

# 被渲染的偏好：(偏好键, 中文标签)。加一项偏好（如难度）= 加一条，渲染循环不改。
_PREFERENCE_LABELS: list[tuple[str, str]] = [
    (QUESTION_LANGUAGE_KEY, "出题语言偏好"),
]


def render_learner_context(*, store: Store, memory: Memory, preferences: PreferenceMemory) -> str:
    """把当前薄弱概念 + 偏好渲成紧凑"学情"文本；无薄弱且无偏好 → 空串（分区据此被跳过）。

    确定性：薄弱概念按 ``item_id`` 升序（不随 set 迭代序漂移）；无时序输入。薄弱概念名走**全库**
    读（``store.all_items()``，全局 KB——``LearningTask`` 已消解，无 task 分区，ADR-0005）。
    """
    sections: list[str] = []
    weak = _render_weak(store, memory)
    if weak:
        sections.append(weak)
    prefs = _render_preferences(preferences)
    if prefs:
        sections.append(prefs)
    if not sections:
        return ""
    return "\n".join([_HEADER, *sections])


def learner_context_provider(
    *, store: Store, memory: Memory, preferences: PreferenceMemory
) -> Callable[[], str]:
    """返回捕获引用的闭包，供 ``ContextBuilder`` 作 memory 分区的 provider（每次 build 现取）。

    捕获的是 store / memory / preferences 的**引用**而非快照，故考核推进（判错落薄弱账 / 设偏好）
    后再 build，渲染反映最新状态。
    """

    def provider() -> str:
        return render_learner_context(store=store, memory=memory, preferences=preferences)

    return provider


def _render_weak(store: Store, memory: Memory) -> str:
    """渲当前薄弱 / 观察中概念：``概念名（状态）``，按 item_id 升序、顿号分隔。空 → 空串。"""
    weak_ids = memory.weak_item_ids()
    if not weak_ids:
        return ""
    concept_by_id = {item.item_id: item.concept for item in store.all_items()}
    parts: list[str] = []
    for item_id in sorted(weak_ids):
        concept = concept_by_id.get(item_id, item_id)
        state = memory.state_of(item_id)
        parts.append(f"{concept}（{state}）" if state is not None else concept)
    return "薄弱概念（下次优先考）：" + "、".join(parts)


def _render_preferences(preferences: PreferenceMemory) -> str:
    """渲已显式设置的偏好：``标签：值``，分号分隔。未设任何偏好 → 空串。"""
    parts: list[str] = []
    for key, label in _PREFERENCE_LABELS:
        pref = preferences.get_preference(key)
        if pref is not None:
            parts.append(f"{label}：{pref.value}")
    if not parts:
        return ""
    return "；".join(parts)

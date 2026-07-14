"""``query_weak_concepts()`` 工具：只读 Learning Memory + store，返回全库薄弱概念摘要。"""

from pydantic import BaseModel

from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.store import Store
from grandquiz.kernel.tools import Tool


class WeakConcept(BaseModel):
    """一个被追踪的薄弱概念摘要：item_id + 概念名 + 当前状态（薄弱 / 观察中）。"""

    item_id: str
    concept: str
    state: str


class WeakConceptsResult(BaseModel):
    """``query_weak_concepts`` 的结构化结果：全库被追踪的薄弱概念（按 item_id 升序，全局 KB）。"""

    weak: list[WeakConcept]


class _QueryWeakParams(BaseModel):
    # 无入参：只读全库薄弱台账（store / memory 在工具闭包里捕获）。
    pass


def make_query_weak_concepts_tool(*, store: Store, memory: Memory) -> Tool:
    """建 ``query_weak_concepts()`` 工具：只读 Learning Memory + store，返回全库薄弱概念摘要。

    确定性、无 LLM（context-free，不需 ctx）：取记忆里被追踪的 item，用**全库**概念名映射解析
    （全局 KB——``LearningTask`` 已消解、知识进同一池，ADR-0005），按 item_id 升序输出概念名 + 状态。
    """

    async def handler(params: _QueryWeakParams) -> str:
        _ = params  # 无入参：全部依赖在闭包捕获
        concept_by_id = {item.item_id: item.concept for item in store.all_items()}
        weak = [
            WeakConcept(item_id=item_id, concept=concept_by_id[item_id], state=state)
            for item_id in sorted(memory.weak_item_ids())
            if item_id in concept_by_id and (state := memory.state_of(item_id)) is not None
        ]
        return WeakConceptsResult(weak=weak).model_dump_json()

    return Tool(
        name="query_weak_concepts",
        description="只读查询已积累的薄弱概念（薄弱 / 观察中）及其概念名。",
        params=_QueryWeakParams,
        handler=handler,
    )

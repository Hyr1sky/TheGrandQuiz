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

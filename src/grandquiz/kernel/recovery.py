"""RecoveryPolicy + ErrorClass——散落错误处理的统一裁决（architecture step 6）。

**为什么不 ``isinstance``**：kernel 领域无关，``kernel↛domain`` 已是 import-linter CI 门
（``uv run lint-imports``）——本模块 import ``QuestionError`` 之类的领域异常会当场变红。故分类
不认识具体异常类型，只读异常**自带的** ``error_class`` 标：domain / providers 各自 import 本模块的
``ErrorClass`` 给自身异常打标（domain→kernel 是合法方向），policy 读该属性分类。

**未带标 → 默认 FATAL（大声失败）**：宁可把没归类的异常当致命冒泡，也不静默降级掩盖它。
这条默认闭掉了"新异常忘了归类就被悄悄吞掉"的坑，也让 ``ReplayMiss``（harness bug）即便没显式
打标也必冒泡。

裁决极简、确定（无墙上时钟 / random）：``DEGRADED`` → 跳过本单元（``SKIP``）；其余一律
``PROPAGATE``（原样冒泡）。每次裁决发 ``RECOVERY_DECIDED`` 上事件脊柱，留痕可观测 / 可回放。
"""

import enum

from grandquiz.kernel.events import EventEmitter, EventType


class ErrorClass(enum.Enum):
    """错误分类——异常自带的语义标，policy 据此裁决。

    只定义**有真实映射行为**的分类（不留死枚举 / 死分支）：

    - ``FATAL``：会话级失败，必冒泡、绝不静默吞（``ReplayMiss`` / 未归类的未知异常）。
    - ``DEGRADED``：本单元可恢复失败，跳过本单元继续（出题 / 判卷重试用尽）。
    - ``RESOURCE_UNREADABLE``：单个资源不可读（抓取 / 深读失败）——ingest 已在内部走 failed 分支
      优雅降级、不抛给 policy；此标供事件归因，policy 现走默认冒泡（不当"可跳过"静默吞）。
    """

    FATAL = "fatal"
    DEGRADED = "degraded"
    RESOURCE_UNREADABLE = "resource_unreadable"


class Decision(enum.Enum):
    """裁决结果——调用方据此决定冒泡还是跳过。

    - ``PROPAGATE``：原样冒泡（会话级失败，绝不静默吞——保 eval / replay 契约）。
    - ``SKIP``：跳过本单元（本轮 / 本资源）、继续下一个。
    """

    PROPAGATE = "propagate"
    SKIP = "skip"


def classify(exc: BaseException) -> ErrorClass:
    """读 ``exc.error_class`` 归类；未带标（或标的不是 ``ErrorClass``）→ ``FATAL``（纯函数）。

    确定：只依赖异常自身，无墙上时钟 / random。用 ``getattr`` + 值类型校验而非
    ``isinstance(exc, 领域异常)``——kernel 不认识领域异常类型（分层守卫），只认 ``error_class`` 标。
    """
    tag = getattr(exc, "error_class", None)
    if isinstance(tag, ErrorClass):
        return tag
    return ErrorClass.FATAL


class RecoveryPolicy:
    """按异常自带分类统一裁决，并把裁决发上事件脊柱。

    构造注入 ``emitter``（事件走同一条脊柱、由注入 Clock 定 ts，故裁决可观测且回放对齐）；
    ``decide`` 的**返回值**是异常分类的确定函数（不依赖时钟 / random），发事件是其可观测副作用。
    """

    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter

    def decide(self, exc: BaseException) -> Decision:
        """裁决 ``exc``：``DEGRADED`` → ``SKIP``，其余 → ``PROPAGATE``；发 ``RECOVERY_DECIDED``。

        ``ReplayMiss`` / 未知异常（→ ``FATAL``）与 ``RESOURCE_UNREADABLE`` 都落非-DEGRADED 分支 →
        ``PROPAGATE``，故 ``ReplayMiss`` 必冒泡、绝不 ``SKIP``（决策 6：eval / replay 契约不可破）。
        """
        error_class = classify(exc)
        decision = Decision.SKIP if error_class is ErrorClass.DEGRADED else Decision.PROPAGATE
        self._emitter.emit(
            EventType.RECOVERY_DECIDED,
            payload={
                "error": repr(exc),
                "error_class": error_class.value,
                "decision": decision.value,
            },
        )
        return decision

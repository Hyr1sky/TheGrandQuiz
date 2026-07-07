"""HookManager——Hook 体系的 interceptor 半边（architecture.md:78-85）。

observer 半边（旁观 ``on_*`` / ``after_*``、只读、异常隔离）已落在 ``EventSink``；本模块补齐
**interceptor** 半边（``before_*`` 挂点、可改写入参、可阻断）。审批门 / 注入防护挂在这里。

**kernel 领域无关**：``HookManager`` 是通用机制——domain 侧注册 hook（domain→kernel 合法），
kernel 泛型地在 ``run_before(point, value)`` 点按注册序折叠已注册的 interceptor，从不认识领域语义
（``point`` 是不透明字符串、``value`` 是不透明对象）。故本模块**禁止 import domain**（issue 03 的
``kernel↛domain`` import-linter 门会挡红）。

## 三条不变量

- **改写**：interceptor 返回改写后的新值，被下一环 / 调用方看到（fold 语义）。
- **阻断（veto）**：interceptor 抛 ``HookVeto`` → ``run_before`` 冒泡它、绝不放行原值。这是"可阻断"
  半的清晰机制（哨兵异常，与"返回改写值"在类型上互斥、不歧义）。
- **fail-closed（安全门失败绝不静默放行）**：interceptor 抛**非 veto** 异常 → 隔离（``logger`` +
  发事件留痕）但**不静默降级成放行**，而是转成 ``HookVeto`` 冒泡。沿用 M6"未知即 FATAL、fail
  loud、宁挡勿放"——不可信输入的中和器若出 bug，宁可挡住也不把未中和内容喂给 LLM。

observer（``on_*`` / ``after_*``，只读）在 interceptor 折叠完后收到**终值**，其异常同
``EventSink``：被捕获 + 记录、不冒泡、不打断其它 observer、不影响返回值。

确定性：无墙上时钟 / random；注册序即执行序（列表保序）；``ts`` 由注入的 ``EventEmitter`` 定。
每次 ``run_before`` 发一条 ``HOOK_INVOKED`` 上事件脊柱（payload 含 point / mutated / vetoed）。
"""

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from grandquiz.kernel.events import EventEmitter, EventType

logger = logging.getLogger(__name__)

T = TypeVar("T")

# interceptor：吃当前值、返回**同型**改写值（或抛 HookVeto 阻断）；observer 只读旁观、返回值忽略。
# 注册 / 折叠 API 泛型于挂点值类型 ``T``（见 register_interceptor / run_before）；内部注册表按挂点
# 异构存储，故落 ``Any``——kernel 视值为不透明，逐挂点的类型安全由泛型方法签名在调用点保证。
Interceptor = Callable[[T], T]
HookObserver = Callable[[T], None]


class HookVeto(Exception):
    """interceptor 主动阻断信号——``run_before`` 捕获后发 ``HOOK_INVOKED``（vetoed）再冒泡。

    这是"可阻断"半的清晰表达：interceptor **返回值** = 改写、**抛 HookVeto** = 阻断，二者互斥。
    interceptor 抛的任何**非** ``HookVeto`` 异常也会被 fail-closed 地转成 ``HookVeto`` 冒泡
    （绝不静默放行原值），故调用方只需捕获这一种即涵盖"被拦下"的全部情形。
    """


class HookManager:
    """interceptor / observer 的注册表 + 折叠器。无状态可复用（跨多次 run 共享一个实例）。

    ``emitter`` 是**每次 run 独有**的（带该 run 的 ``trace_id`` / 单调 ``seq``），故不进构造、而是
    ``run_before`` 的调用参数——让 HookManager 本身长寿、可在组装点建好一次注入各处（如 Reader）。
    """

    def __init__(self) -> None:
        # 内部注册表按挂点异构 → 落 Any；逐挂点类型安全由下面泛型方法签名在调用点保证。
        self._interceptors: dict[str, list[Callable[[Any], Any]]] = {}
        self._observers: dict[str, list[Callable[[Any], None]]] = {}

    def register_interceptor(self, point: str, interceptor: Interceptor[T]) -> None:
        """在 ``point`` 挂一个 interceptor（``before_*`` 语义：可改写 / 可阻断）。按注册序执行。"""
        self._interceptors.setdefault(point, []).append(interceptor)

    def register_observer(self, point: str, observer: HookObserver[T]) -> None:
        """在 ``point`` 挂一个只读 observer——interceptor 折叠完后收终值，异常被隔离。"""
        self._observers.setdefault(point, []).append(observer)

    def run_before(
        self,
        point: str,
        value: T,
        *,
        emitter: EventEmitter,
        parent_span_id: str | None = None,
    ) -> T:
        """按注册序折叠 ``point`` 的 interceptor 于 ``value``，返回终值；被 veto → 冒泡 veto。

        每个 interceptor 可返回改写值或抛 ``HookVeto`` 阻断；非 veto 异常按 fail-closed 转
        ``HookVeto``（隔离 + 留痕，绝不静默放行原值）。折叠成功后把终值只读地喂给 observer（异常
        隔离），并发一条 ``HOOK_INVOKED``（mutated = 值是否变、vetoed=False）。
        """
        original = value
        current: Any = value
        for interceptor in self._interceptors.get(point, ()):
            try:
                current = interceptor(current)
            except HookVeto:
                # 主动阻断：留痕后原样冒泡（绝不放行）。
                self._emit(
                    emitter, point, mutated=False, vetoed=True, parent_span_id=parent_span_id
                )
                raise
            except Exception as exc:
                # 安全门 bug：隔离（不炸整个 turn）但 fail-closed——转 HookVeto 冒泡，绝不静默放行。
                logger.exception("hook interceptor raised at point %r; fail-closed (veto)", point)
                self._emit(
                    emitter,
                    point,
                    mutated=False,
                    vetoed=True,
                    parent_span_id=parent_span_id,
                    error=repr(exc),
                )
                raise HookVeto(f"interceptor at {point!r} failed; fail-closed") from exc

        mutated = current != original
        for observer in self._observers.get(point, ()):
            try:
                observer(current)
            except Exception:
                # 只读旁观者的异常边界：同 EventSink——捕获 + 记录、不冒泡、不打断其它 observer。
                logger.exception("hook observer raised at point %r; isolated", point)
        self._emit(emitter, point, mutated=mutated, vetoed=False, parent_span_id=parent_span_id)
        # fold 保持 T：初值 T、interceptor 契约同型（Interceptor[T]），故终值仍是 T。
        return current

    @staticmethod
    def _emit(
        emitter: EventEmitter,
        point: str,
        *,
        mutated: bool,
        vetoed: bool,
        parent_span_id: str | None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, object] = {"point": point, "mutated": mutated, "vetoed": vetoed}
        if error is not None:
            payload["error"] = error
        emitter.emit(EventType.HOOK_INVOKED, payload=payload, parent_span_id=parent_span_id)

"""``ingest_tool`` / ``start_quiz_tool`` 共用的 emitter 包装——两个工具各自的编排
（``ingest_resource`` / ``assess_once``）都要把自建根 span 重挂到本次 TOOL_CALL 之下，
故此包装抽成两者的共同依赖。
"""

from collections.abc import Mapping
from typing import Any

from grandquiz.kernel.events import AgentEvent, EventEmitter


class ScopedEmitter(EventEmitter):
    """把被包装编排的**根 span** 重挂到给定 parent 之下的 emitter 包装（wrap 不改写）。

    组装持有 inner + ``__getattr__`` 全量委托：本包装不持有自己的 sink / clock / 计数器，只覆写
    ``trace_id`` / ``new_span_id`` / ``emit`` 三个成员（把 seq / span 计数与发布委托 inner，单一
    真源），其余**任意** EventEmitter 成员经 ``__getattr__`` 落到 inner。唯一改写：``emit`` 时把
    ``parent_span_id is None`` 的事件重挂到 ``root_parent``。于是被包装编排（``ingest_resource``）
    自建的根 span（``ingest.started`` / ``.ended``，本无父）成为本次 TOOL_CALL span 的子节点，而内部
    model / 点事件（都携显式 ``parent_span_id``）原样归位不变。``ingest_resource`` 因此一行不动。

    去掉了旧的 partial-subclass 脆弱（不调 ``super().__init__`` 却只覆写 3 方法——任何未覆写却触碰
    实例态的继承成员会 AttributeError）：现在 ``__getattr__`` 把未覆写成员透明委托 inner，故 inner
    未来新增任何方法 / 属性都不再炸（钉死于 test_cli_react）。仍名义上继承 EventEmitter 以保类型兼容
    （装配点把 scoped 当 ``EventEmitter`` 用）。
    """

    def __init__(self, inner: EventEmitter, root_parent: str) -> None:
        # 刻意不调 super().__init__：本包装不持有自己的 sink / clock / 计数器，全部委托 inner。
        self._inner = inner
        self._root_parent = root_parent

    def __getattr__(self, name: str) -> Any:
        # 未覆写的成员（及此前缺失的内部态）透明委托 inner。``_inner`` 本身在 __init__ 里经普通
        # setattr 落定，正常查找即命中、不会递归进本方法；加一道守卫防反序列化等场景的无限递归。
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    @property
    def trace_id(self) -> str:
        return self._inner.trace_id

    def new_span_id(self) -> str:
        return self._inner.new_span_id()

    def emit(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AgentEvent:
        return self._inner.emit(
            event_type,
            payload=payload,
            span_id=span_id,
            # 根 span（无父）重挂到 TOOL_CALL span 之下；内部事件携显式父、原样透传。
            parent_span_id=parent_span_id if parent_span_id is not None else self._root_parent,
        )

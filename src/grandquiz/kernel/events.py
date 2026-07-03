"""AgentEvent——作为整个 runtime 脊柱的事件信封。

trace = 事件的持久化；hook = 事件的订阅者；流式输出 = 事件的网络投影；
eval replay = 事件流的回放。一个 span 是一对事件（``*.started`` / ``*.ended``，
共享 ``span_id``）；TraceStore 把事件流投影成 span 树。kernel 保持领域无关：
泛型地分发 / 持久化事件，从不查看 ``payload`` 的具体内容。
"""

import copy
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grandquiz.kernel.clock import Clock


class EventType:
    """kernel 级事件类型（M1 子集）。领域事件用自己的命名空间字符串
    （如 ``learning.item_created``）在 domain 层定义，kernel 无需认识。"""

    TURN_STARTED = "turn.started"
    TURN_ENDED = "turn.ended"
    MODEL_STARTED = "model.started"
    MODEL_ENDED = "model.ended"
    ERROR = "error"


class AgentEvent(BaseModel):
    """不可变的事件信封。``payload`` 对 kernel 不透明（JSON-able dict），由发射方
    用各自的 typed 模型构造，构造时深拷贝隔离——事件不与构造方共享嵌套引用（发射后再改源
    dict 不会污染已落事件）；consumer 视 ``payload`` 为只读（EventSink 把同一事件实例扇出给
    所有订阅者，谁都不该改它）。"""

    model_config = ConfigDict(frozen=True)

    type: str
    seq: int
    ts: float
    trace_id: str
    span_id: str | None = None
    parent_span_id: str | None = None
    payload: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("payload", mode="before")
    @classmethod
    def _isolate_payload(cls, value: object) -> object:
        # 深拷贝隔离：见类 docstring。payload 契约上是 JSON-able，deepcopy 有界且良定义。
        return copy.deepcopy(value)


Observer = Callable[[AgentEvent], None]


class EventSink:
    """扇出登记处——脊柱，订阅者挂在这里。M1 订阅者：CLI 打印器；后续：TraceStore、HookManager。

    publish 不做 per-observer 异常隔离，订阅者不得抛异常——隔离是 HookManager（M4）的职责
    （见 CLAUDE.md "Hook 抛异常必须被隔离"）。"""

    def __init__(self) -> None:
        self._observers: list[Observer] = []

    def subscribe(self, observer: Observer) -> None:
        self._observers.append(observer)

    def publish(self, event: AgentEvent) -> None:
        for observer in self._observers:
            observer(event)


class EventEmitter:
    """给一次 run 的事件盖上单调 ``seq`` + ``ts`` 并铸造 span id，然后发布到 sink。

    确定性：``seq`` 与 span id 来自计数器；``ts`` 来自注入的 Clock；
    ``trace_id`` 是每次 run 的输入。
    """

    def __init__(self, sink: EventSink, clock: Clock, trace_id: str) -> None:
        self._sink = sink
        self._clock = clock
        self._trace_id = trace_id
        self._seq = 0
        self._span_counter = 0

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def new_span_id(self) -> str:
        span_id = f"{self._trace_id}:s{self._span_counter}"
        self._span_counter += 1
        return span_id

    def emit(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            type=event_type,
            seq=self._seq,
            ts=self._clock.now(),
            trace_id=self._trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            payload=payload if payload is not None else {},
        )
        self._seq += 1
        self._sink.publish(event)
        return event

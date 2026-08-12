"""AgentEvent——作为整个 runtime 脊柱的事件信封。

trace = 事件的持久化；hook = 事件的订阅者；流式输出 = 事件的网络投影；
eval replay = 事件流的回放。一个 span 是一对事件（``*.started`` / ``*.ended``，
共享 ``span_id``）；TraceStore 把事件流投影成 span 树。kernel 保持领域无关：
泛型地分发 / 持久化事件，从不查看 ``payload`` 的具体内容。
"""

import copy
import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grandquiz.kernel.clock import Clock

logger = logging.getLogger(__name__)


class EventType:
    """kernel 级事件类型（M1 子集）。领域事件用自己的命名空间字符串
    （如 ``learning.item_created``）在 domain 层定义，kernel 无需认识。"""

    TURN_STARTED = "turn.started"
    TURN_ENDED = "turn.ended"
    # 有界 tool-calling 循环（run_agent_turn）：AGENT_TURN 是根 span，其下嵌 MODEL / TOOL_CALL。
    # 命名沿用 ``<prefix>.started`` / ``.ended`` 约定，故 build_span_tree 无需改动即成对折叠。
    AGENT_TURN_STARTED = "agent_turn.started"
    AGENT_TURN_ENDED = "agent_turn.ended"
    TOOL_CALL_STARTED = "tool_call.started"
    TOOL_CALL_ENDED = "tool_call.ended"
    MODEL_STARTED = "model.started"
    MODEL_OUTPUT_DELTA = "model.output_delta"
    MODEL_ENDED = "model.ended"
    CONTEXT_PREPARED = "context.prepared"
    RECOVERY_DECIDED = "recovery.decided"
    HOOK_INVOKED = "hook.invoked"
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


@runtime_checkable
class Processor(Protocol):
    """事件脊柱的**富订阅者**协议——形状对标 openai-agents 的 ``TracingProcessor``（不 vendor、
    无第三方依赖）。``on_event`` 消费每个 ``AgentEvent``；span 生命周期（``on_span_start`` /
    ``on_span_end``）**可由** ``*.started`` / ``*.ended`` 事件对派生（见 ``build_span_tree``），
    故不作强制方法——需要的 processor 自行在 ``on_event`` 里派生即可。kernel 领域无关：processor
    只认 ``AgentEvent`` 信封，从不查看 ``payload`` 的领域字段。为 Tier C 的 OTLP processor 留口。

    既有 ``Callable[[AgentEvent], None]`` 订阅者仍经 ``EventSink.subscribe`` 注册（向后兼容）；
    富消费者用 ``EventSink.register`` 注册本协议实现。两条路径的扇出都被异常隔离。"""

    def on_event(self, event: AgentEvent) -> None: ...


class DurableProcessorError(RuntimeError):
    """承重事件处理器写入失败；上层必须把当前 turn 视为失败。"""

    def __init__(self, event: AgentEvent, processor: Processor, cause: Exception) -> None:
        self.event = event
        self.processor = processor
        self.cause = cause
        super().__init__(
            f"durable event processor failed on {event.type} (seq={event.seq}): {cause!r}"
        )


class EventSink:
    """扇出登记处——脊柱，订阅者挂在这里。订阅者：CLI 打印器 / TraceStore / eval 事件收集。

    每个订阅者的调用被**异常隔离**：某订阅者抛异常被捕获 + 记录（log），不冒泡、不中断对其它
    订阅者的扇出、不中断本轮 / 本次 run。这闭掉了 EventSink 不隔离订阅者异常的已知坑（当初
    Rich markup 崩的根因）。只做 observer 侧隔离；interceptor（``before_*`` 改参 / 阻断）语义仍
    留 M4 HookManager。

    两种注册方式，行为等价、都被隔离：``subscribe(callable)`` 收一个 ``Observer`` 回调（向后
    兼容）；``register(processor)`` 收一个 ``Processor`` 富消费者（登记其 ``on_event``）。"""

    def __init__(self) -> None:
        self._observers: list[Observer] = []
        self._durable_processors: list[Processor] = []

    def subscribe(self, observer: Observer) -> None:
        """注册一个只读订阅者回调（向后兼容路径）。异常隔离对它同样生效。"""
        self._observers.append(observer)

    def register(self, processor: Processor) -> None:
        """注册 best-effort 富 ``Processor``；异常与普通 observer 一样隔离。"""
        self._observers.append(processor.on_event)

    def register_durable(self, processor: Processor) -> None:
        """注册承重处理器；失败会阻断 publish 并以结构化异常向上冒泡。"""
        self._durable_processors.append(processor)

    def publish(self, event: AgentEvent) -> None:
        for processor in self._durable_processors:
            try:
                processor.on_event(event)
            except Exception as exc:
                # 不在事件脊柱内部再 emit ERROR，避免持久层失败递归发布自身失败。
                raise DurableProcessorError(event, processor, exc) from exc
        for observer in self._observers:
            try:
                observer(event)
            except Exception:
                # 隔离边界：坏订阅者被捕获 + 记录，不冒泡、不中断其它订阅者的扇出与本轮。
                # 只吞 Exception（KeyboardInterrupt / SystemExit 等 BaseException 照常传播）。
                logger.exception(
                    "event sink observer raised on %s (seq=%d); isolated",
                    event.type,
                    event.seq,
                )


class EventEmitter:
    """给一次 run 的事件盖上单调 ``seq`` + ``ts`` 并铸造 span id，然后发布到 sink。

    确定性：``seq`` 与 span id 来自计数器；``ts`` 来自注入的 Clock；
    ``trace_id`` 是每次 run 的输入。
    """

    def __init__(
        self,
        sink: EventSink,
        clock: Clock,
        trace_id: str,
        *,
        initial_seq: int = 0,
        initial_span_counter: int = 0,
    ) -> None:
        if initial_seq < 0 or initial_span_counter < 0:
            raise ValueError("事件与 span 计数器不能为负数")
        self._sink = sink
        self._clock = clock
        self._trace_id = trace_id
        self._seq = initial_seq
        self._span_counter = initial_span_counter

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

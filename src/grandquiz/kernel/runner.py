"""Agent runner——两条 turn 循环发射同一条事件脊柱。

- ``run_turn``（M1）：最小、无工具的单次 turn。
- ``run_agent_turn``（R1-S1）：有界 tool-calling 循环（自由 ReAct 的机制层）——LLM 出 tool_calls →
  经注册表执行 → 结果回灌进 messages → 再想 → 直到出 final 文本或触顶 ``max_iterations``。

按 ADR-0004，核心考核链路仍是确定性 workflow（出题 / 判卷是槽）；``run_agent_turn`` 是自由 ReAct
只服务开放编排的机制层。确定性：循环有界；tool 选择即 completion 输出，走同一 record/replay 路径；
工具执行是确定性代码，每趟重跑（不进 cassette）。
"""

from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.kernel.hooks import HookManager, HookVeto
from grandquiz.kernel.recovery import Decision, RecoveryPolicy
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.providers.base import Completion, Message, Provider, Role, ToolCall

_TOOL_CALL_HOOK = "tool_call"
# ReAct 编排固定走 basic 角色（已确认 deepseek 支持 function-calling）；显式常量避免散落字面量。
_REACT_ROLE: Role = "basic"


class MaxIterationsExceeded(RuntimeError):
    """有界 tool-calling 循环跑满 ``max_iterations`` 仍未收敛到 final 文本——**大声失败**。

    刻意 raise 而非静默截断返回半成品：截断会把"agent 卡在工具环里"伪装成正常回答，毁掉可观测性。
    未打 ``error_class`` 标 → 经 ``RecoveryPolicy`` 默认归 FATAL、必冒泡。
    """

    def __init__(self, max_iterations: int) -> None:
        super().__init__(f"agent turn 跑满 max_iterations={max_iterations} 仍未产出 final 文本")
        self.max_iterations = max_iterations


class Runner:
    def __init__(
        self,
        provider: Provider,
        emitter: EventEmitter,
        *,
        system_prompt: str | None = None,
        prompt_version: str | None = None,
        tools: ToolRegistry | None = None,
        hooks: HookManager | None = None,
        recovery: RecoveryPolicy | None = None,
        max_iterations: int = 8,
    ) -> None:
        self._provider = provider
        self._emitter = emitter
        self._system_prompt = system_prompt
        # prompt 版本号进 trace（架构约束）——此处只留种子；正式 prompt registry 是后续里程碑。
        self._prompt_version = prompt_version
        # tool-calling 循环的加硬件（run_agent_turn 用；run_turn 不碰）。全可选，保持 M1 构造兼容。
        self._tools = tools if tools is not None else ToolRegistry()
        self._hooks = hooks
        self._recovery = recovery
        self._max_iterations = max_iterations
        self._history: list[Message] = []

    def _messages(self) -> list[Message]:
        messages: list[Message] = []
        if self._system_prompt is not None:
            messages.append(Message(role="system", content=self._system_prompt))
        messages.extend(self._history)
        return messages

    async def run_turn(self, user_message: str) -> str:
        turn_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.TURN_STARTED,
            span_id=turn_span,
            payload={"user_message": user_message},
        )

        # 历史只在成功后提交：失败不留孤儿 user 消息，否则重试会喂给 LLM 两条连续 user。
        call_messages = [*self._messages(), Message(role="user", content=user_message)]
        model_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.MODEL_STARTED,
            span_id=model_span,
            parent_span_id=turn_span,
            payload={
                "messages": [m.model_dump() for m in call_messages],
                "prompt_version": self._prompt_version,
            },
        )
        try:
            completion: Completion = await self._provider.complete(call_messages, role="basic")
        except Exception as exc:
            # 错误也要闭合 model span（started/ended 成对不变量）：ERROR 是一等信号，
            # MODEL_ENDED(ok=False) 封口，否则 TraceStore 会拿到永远开着的 span。
            self._emitter.emit(
                EventType.ERROR,
                span_id=model_span,
                parent_span_id=turn_span,
                payload={"error": repr(exc)},
            )
            self._emitter.emit(
                EventType.MODEL_ENDED,
                span_id=model_span,
                parent_span_id=turn_span,
                payload={"ok": False, "error": repr(exc)},
            )
            self._emitter.emit(EventType.TURN_ENDED, span_id=turn_span, payload={"ok": False})
            raise

        self._emitter.emit(
            EventType.MODEL_ENDED,
            span_id=model_span,
            parent_span_id=turn_span,
            payload={
                "ok": True,
                "output": completion.text,
                "usage": completion.usage.model_dump(),
            },
        )
        # 跨轮裁剪（架构约束）：历史只保留每轮最终 assistant 回答——M1 无工具中间步，故平凡。
        self._history.append(Message(role="user", content=user_message))
        self._history.append(Message(role="assistant", content=completion.text))
        self._emitter.emit(EventType.TURN_ENDED, span_id=turn_span, payload={"ok": True})
        return completion.text

    async def run_agent_turn(self, user_message: str) -> str:
        """有界 tool-calling 循环：LLM ⇄ 工具往返直到 final 文本，或触顶大声失败。

        ``AGENT_TURN`` 是根 span；每轮 ``provider.complete`` 是其下 MODEL span；每次工具执行是其下
        TOOL_CALL span（嵌套 AGENT_TURN → [MODEL, TOOL_CALL, …, MODEL]）。跨轮裁剪（架构约束）：成功
        后历史只提交 user + final assistant，丢弃全部 tool 调用中间过程。
        """
        recovery = self._recovery if self._recovery is not None else RecoveryPolicy(self._emitter)
        turn_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.AGENT_TURN_STARTED,
            span_id=turn_span,
            payload={"user_message": user_message},
        )

        # 历史只在成功后提交（同 run_turn）：失败不留孤儿 user 消息。工具往返只进本地 call_messages
        call_messages = [*self._messages(), Message(role="user", content=user_message)]
        try:
            for _ in range(self._max_iterations):
                completion = await self._generate(call_messages, parent_span_id=turn_span)
                if not completion.tool_calls:
                    # final 文本 → 终止。裁剪：只留 user + final assistant。
                    self._history.append(Message(role="user", content=user_message))
                    self._history.append(Message(role="assistant", content=completion.text))
                    self._emitter.emit(
                        EventType.AGENT_TURN_ENDED,
                        span_id=turn_span,
                        payload={"ok": True, "output": completion.text},
                    )
                    return completion.text

                # 有 tool_calls：先把 assistant 的工具请求消息追加，再逐个执行、把结果回灌。
                call_messages.append(
                    Message(
                        role="assistant",
                        content=completion.text,
                        tool_calls=completion.tool_calls,
                    )
                )
                for tool_call in completion.tool_calls:
                    result_message = await self._execute_tool_call(
                        tool_call, parent_span_id=turn_span, recovery=recovery
                    )
                    call_messages.append(result_message)
        except Exception:
            # 任何冒泡（FATAL 工具错 / veto / model 错）先封口 AGENT_TURN（started/ended 成对）。
            self._emitter.emit(EventType.AGENT_TURN_ENDED, span_id=turn_span, payload={"ok": False})
            raise

        # 循环耗尽仍未收敛：大声失败（非静默截断），先封口再抛。
        self._emitter.emit(
            EventType.AGENT_TURN_ENDED,
            span_id=turn_span,
            payload={"ok": False, "reason": "max_iterations"},
        )
        raise MaxIterationsExceeded(self._max_iterations)

    async def _generate(self, call_messages: list[Message], *, parent_span_id: str) -> Completion:
        """发一次 MODEL span 并调 provider；错误闭合 span（ok=False）后原样冒泡。

        ReAct 生成走 ``role="basic"``（已确认 deepseek 支持 function-calling），并把注册表的
        ``tool_specs()`` 一并传给 ``provider.complete(tools=...)``——否则真 provider 从不发 tools、
        模型只能用文本"扮演"调工具（dogfood 的 11 agent_turn / 0 tool_call 根因）。``role`` 显式记进
        MODEL_STARTED payload，修 trace 里 role 为空。
        """
        model_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.MODEL_STARTED,
            span_id=model_span,
            parent_span_id=parent_span_id,
            payload={
                "role": _REACT_ROLE,
                "messages": [m.model_dump() for m in call_messages],
                "prompt_version": self._prompt_version,
            },
        )
        try:
            completion = await self._provider.complete(
                call_messages, role=_REACT_ROLE, tools=self._tools.tool_specs()
            )
        except Exception as exc:
            self._emitter.emit(
                EventType.ERROR,
                span_id=model_span,
                parent_span_id=parent_span_id,
                payload={"error": repr(exc)},
            )
            self._emitter.emit(
                EventType.MODEL_ENDED,
                span_id=model_span,
                parent_span_id=parent_span_id,
                payload={"ok": False, "error": repr(exc)},
            )
            raise
        output: dict[str, object] = {
            "ok": True,
            "output": completion.text,
            "usage": completion.usage.model_dump(),
        }
        if completion.tool_calls is not None:
            output["tool_calls"] = [tc.model_dump() for tc in completion.tool_calls]
        self._emitter.emit(
            EventType.MODEL_ENDED,
            span_id=model_span,
            parent_span_id=parent_span_id,
            payload=output,
        )
        return completion

    async def _execute_tool_call(
        self, tool_call: ToolCall, *, parent_span_id: str, recovery: RecoveryPolicy
    ) -> Message:
        """执行一次工具调用，返回 ``role="tool"`` 结果消息（DEGRADED 错回灌错误文本）。

        流程：发 TOOL_CALL span → 经 M4 ``HookManager.run_before("tool_call", args)`` 挂点（可改参 /
        veto，S1 未注册真 interceptor）→ 注册表 dispatch。异常交 M6 ``RecoveryPolicy``：``SKIP``
        （DEGRADED）把错误作为 tool 结果回灌让 LLM 换路；``PROPAGATE``（FATAL / 未知）闭 span 冒泡。
        ``HookVeto``（安全门阻断）fail-closed 冒泡。
        """
        tool_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.TOOL_CALL_STARTED,
            span_id=tool_span,
            parent_span_id=parent_span_id,
            payload={"tool_name": tool_call.name, "arguments": dict(tool_call.arguments)},
        )
        try:
            arguments: dict[str, object] = dict(tool_call.arguments)
            if self._hooks is not None:
                # 挂点：interceptor 可改写入参 / 抛 HookVeto 阻断（审批门 / 注入中和的落点）。
                arguments = self._hooks.run_before(
                    _TOOL_CALL_HOOK,
                    arguments,
                    emitter=self._emitter,
                    parent_span_id=tool_span,
                )
            # 执行上下文（kernel-generic）：把 emitter + 本次 TOOL_CALL span id 递给需要它的工具，
            # 让工具内部事件挂在 TOOL_CALL 之下。kernel 不认识工具拿它做的领域事情。
            ctx = ToolContext(emitter=self._emitter, parent_span_id=tool_span)
            result = await self._tools.dispatch(tool_call.name, arguments, ctx=ctx)
        except HookVeto as exc:
            # 安全门阻断：fail-closed，闭合 span 后冒泡（绝不放行未中和的调用）。
            self._emitter.emit(
                EventType.TOOL_CALL_ENDED,
                span_id=tool_span,
                parent_span_id=parent_span_id,
                payload={"ok": False, "vetoed": True, "error": repr(exc)},
            )
            raise
        except Exception as exc:
            decision = recovery.decide(exc)
            if decision is Decision.SKIP:
                # DEGRADED：把错误作为 tool 结果回灌，让 LLM 换路重试（有界于 max_iterations）。
                self._emitter.emit(
                    EventType.TOOL_CALL_ENDED,
                    span_id=tool_span,
                    parent_span_id=parent_span_id,
                    payload={"ok": False, "recovered": True, "error": repr(exc)},
                )
                return Message(
                    role="tool",
                    tool_call_id=tool_call.id,
                    content=f"tool error: {exc}",
                )
            # PROPAGATE：ERROR 一等信号 + 闭合 span 后冒泡（由 run_agent_turn 封口 AGENT_TURN）。
            self._emitter.emit(
                EventType.ERROR,
                span_id=tool_span,
                parent_span_id=parent_span_id,
                payload={"error": repr(exc)},
            )
            self._emitter.emit(
                EventType.TOOL_CALL_ENDED,
                span_id=tool_span,
                parent_span_id=parent_span_id,
                payload={"ok": False, "error": repr(exc)},
            )
            raise
        self._emitter.emit(
            EventType.TOOL_CALL_ENDED,
            span_id=tool_span,
            parent_span_id=parent_span_id,
            payload={"ok": True, "result": result},
        )
        return Message(role="tool", tool_call_id=tool_call.id, content=result)

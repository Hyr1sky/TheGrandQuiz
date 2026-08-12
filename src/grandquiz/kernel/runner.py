"""Agent runner——两条 turn 循环发射同一条事件脊柱。

- ``run_turn``（M1）：最小、无工具的单次 turn。
- ``run_agent_turn``（R1-S1）：有界 tool-calling 循环（自由 ReAct 的机制层）——LLM 出 tool_calls →
  经注册表执行 → 结果回灌进 messages → 再想 → 直到出 final 文本或触顶 ``max_iterations``。

按 ADR-0004，核心考核链路仍是确定性 workflow（出题 / 判卷是槽）；``run_agent_turn`` 是自由 ReAct
只服务开放编排的机制层。确定性：循环有界；tool 选择即 completion 输出，走同一 record/replay 路径；
工具执行是确定性代码，每趟重跑（不进 cassette）。
"""

import asyncio

from grandquiz.kernel.context import ContextBudgetStatus, ContextBuilder
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.kernel.hooks import HookManager, HookVeto
from grandquiz.kernel.recovery import Decision, RecoveryPolicy
from grandquiz.kernel.tools import ToolContext, ToolRegistry
from grandquiz.providers.base import (
    Completion,
    Message,
    Provider,
    ProviderStreamProtocolError,
    Role,
    StreamingProvider,
    TextDelta,
    ToolCall,
)

_TOOL_CALL_HOOK = "tool_call"
# ReAct 编排固定走 basic 角色（已确认 deepseek 支持 function-calling）；显式常量避免散落字面量。
_REACT_ROLE: Role = "basic"
_STREAM_DELTA_BATCH_CHARS = 48


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
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._provider = provider
        self._emitter = emitter
        self._system_prompt = system_prompt
        # ReAct 上下文装配器（M5）：run_agent_turn 有它则经分区装配 messages（system 前言区 +
        # 学情注入分区 → history → user）。None → 退回原 system + history（向后兼容，run_turn /
        # 既有测试不破）。只作用于 ReAct 路径，不碰 run_turn。
        self._context_builder = context_builder
        # prompt 版本号进 trace（架构约束）——此处只留种子；正式 prompt registry 是后续里程碑。
        self._prompt_version = prompt_version
        # tool-calling 循环的加硬件（run_agent_turn 用；run_turn 不碰）。全可选，保持 M1 构造兼容。
        self._tools = tools if tools is not None else ToolRegistry()
        self._hooks = hooks
        self._recovery = recovery
        self._max_iterations = max_iterations
        self._history: list[Message] = []
        # C-wire 增量 2：上一轮排的历史折叠后台任务（None = 无待收口任务）。ReAct-only（run_turn
        # 不建 ContextBuilder，故此字段对它恒为 None，天然不涉及）。
        self._pending_prune: asyncio.Task[None] | None = None

    def _messages(self) -> list[Message]:
        messages: list[Message] = []
        if self._system_prompt is not None:
            messages.append(Message(role="system", content=self._system_prompt))
        messages.extend(self._history)
        return messages

    def _agent_turn_messages(self, user_message: str) -> list[Message]:
        """ReAct 一次 turn 的初始 messages：有 ContextBuilder 走分区装配（system 前言区 + 学情
        注入 → history → user），否则退回原 ``system + history + user``（向后兼容）。

        ``self._history`` 是跨轮裁剪后的历史（只 user + final assistant）；ContextBuilder 拿它 +
        当前 user 装配，故当前 user 消息由装配统一追加（不在此重复）。builder 存在时 system 前言区
        由其 system 分区提供（``self._system_prompt`` 在 ReAct 路径被 builder 接管、不再重复注入）。
        """
        if self._context_builder is not None:
            return self._context_builder.build(self._history, user_message)
        return [*self._messages(), Message(role="user", content=user_message)]

    def context_budget_status(self, user_message: str = "") -> ContextBudgetStatus | None:
        """Project the next ReAct request budget without exposing message content."""
        if self._context_builder is None:
            return None
        return self._context_builder.budget_status(self._history, user_message)

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

        C-wire 增量 2：开头先收口上一轮排的历史折叠后台任务（``_drain_pending_prune``）——必须在
        本轮装配 messages 前，让 ``SummarizingHistoryCompressor.compress()`` 读到完全落定的滚动摘要
        状态；成功收敛后本轮自己的折叠不阻塞返回，改排一个新后台任务，留给下一轮开头收口（或会话
        结束时 ``aclose()`` 收口）。
        """
        await self._drain_pending_prune()
        recovery = self._recovery if self._recovery is not None else RecoveryPolicy(self._emitter)
        turn_span = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.AGENT_TURN_STARTED,
            span_id=turn_span,
            payload={"user_message": user_message},
        )

        # 历史只在成功后提交（同 run_turn）：失败不留孤儿 user 消息。工具往返只进本地 call_messages
        call_messages = self._agent_turn_messages(user_message)
        if self._context_builder is not None:
            context_status = self._context_builder.budget_status_for_messages(call_messages)
            if context_status is not None:
                self._emitter.emit(
                    EventType.CONTEXT_PREPARED,
                    parent_span_id=turn_span,
                    payload=context_status.model_dump(),
                )
        try:
            for _ in range(self._max_iterations):
                completion = await self._generate(call_messages, parent_span_id=turn_span)
                if not completion.tool_calls:
                    # final 文本 → 终止。裁剪：只留 user + final assistant。
                    self._history.append(Message(role="user", content=user_message))
                    self._history.append(Message(role="assistant", content=completion.text))
                    self._schedule_prune()
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
        except asyncio.CancelledError:
            self._emitter.emit(
                EventType.AGENT_TURN_ENDED,
                span_id=turn_span,
                payload={
                    "ok": False,
                    "cancelled": True,
                    "status": "cancelled",
                },
            )
            raise
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

    def _schedule_prune(self) -> None:
        """把本轮的历史折叠排成后台任务，不阻塞 ``run_agent_turn`` 的返回（C-wire 增量 2）。

        无 ``context_builder`` → 无历史压缩机制可言，直接跳过。``ContextBuilder.prune`` 自己会对
        ``history_compressor`` 做能力探测（无 / 不支持 prune 的压缩器都安全空操作），故这里不重复
        判断——排任务的开销可忽略，单一判断权威留在 ``ContextBuilder``。传的是 ``self._history``
        的引用而非快照：安全前提是"任意时刻至多一个待收口任务"，由 ``run_agent_turn`` 开头的
        ``_drain_pending_prune`` 保证下一次排新任务前，上一个已经落地（成功或被隔离的失败）。
        """
        if self._context_builder is None:
            return
        self._pending_prune = asyncio.create_task(self._context_builder.prune(self._history))

    async def _drain_pending_prune(self) -> None:
        """收口上一轮排的历史折叠任务：``await`` 之，异常被隔离（同 hook observer 语义，绝不炸
        本轮）——一次失败的后台摘要不该把一个已经成功产出回复的 turn 拖成失败。

        隔离边界只吞 ``Exception``（``KeyboardInterrupt`` / ``SystemExit`` 等 ``BaseException``
        照常传播，同 ``EventSink.publish`` 的隔离哲学）。失败仍需可观测：发一条无 span 归属的
        ``ERROR`` 事件（不进任何 span 树，但仍落 trace 原始事件流，可查）。
        """
        task = self._pending_prune
        self._pending_prune = None
        if task is None:
            return
        try:
            await task
        except Exception as exc:
            self._emitter.emit(
                EventType.ERROR, payload={"error": repr(exc), "phase": "history_prune"}
            )

    async def aclose(self) -> None:
        """会话结束前的收尾：把仍未落地的历史折叠任务收口，同 ``_drain_pending_prune`` 的隔离语义。

        必须由调用方（如 ``run_react`` 的 ``finally``）显式调用——否则会话最后一轮排的任务会在
        ``asyncio.run`` 收尾取消所有挂起任务时被直接丢弃，其折叠的老轮内容永久丢失（无下一轮开头
        帮它收口）。幂等：无待收口任务时空操作。
        """
        await self._drain_pending_prune()

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
            tools = self._tools.tool_specs()
            if isinstance(self._provider, StreamingProvider):
                text_parts: list[str] = []
                pending_delta_parts: list[str] = []
                pending_delta_chars = 0
                first_delta_emitted = False
                completion: Completion | None = None

                def emit_delta(text: str) -> None:
                    self._emitter.emit(
                        EventType.MODEL_OUTPUT_DELTA,
                        span_id=model_span,
                        parent_span_id=parent_span_id,
                        payload={"text": text},
                    )

                async for stream_event in self._provider.stream_complete(
                    call_messages,
                    role=_REACT_ROLE,
                    tools=tools,
                ):
                    if isinstance(stream_event, TextDelta):
                        if completion is not None:
                            raise ProviderStreamProtocolError(
                                "CompletionFinished 之后仍收到文本增量"
                            )
                        if stream_event.text:
                            text_parts.append(stream_event.text)
                            if not first_delta_emitted:
                                emit_delta(stream_event.text)
                                first_delta_emitted = True
                            else:
                                pending_delta_parts.append(stream_event.text)
                                pending_delta_chars += len(stream_event.text)
                                if pending_delta_chars >= _STREAM_DELTA_BATCH_CHARS:
                                    emit_delta("".join(pending_delta_parts))
                                    pending_delta_parts.clear()
                                    pending_delta_chars = 0
                    else:
                        if completion is not None:
                            raise ProviderStreamProtocolError("一次流包含多个 CompletionFinished")
                        if pending_delta_parts:
                            emit_delta("".join(pending_delta_parts))
                            pending_delta_parts.clear()
                            pending_delta_chars = 0
                        completion = stream_event.completion
                if completion is None:
                    raise ProviderStreamProtocolError("Provider stream 缺少 CompletionFinished")
                if "".join(text_parts) != completion.text:
                    raise ProviderStreamProtocolError("文本增量与最终 Completion.text 不一致")
            else:
                completion = await self._provider.complete(
                    call_messages,
                    role=_REACT_ROLE,
                    tools=tools,
                )
        except asyncio.CancelledError:
            self._emitter.emit(
                EventType.MODEL_ENDED,
                span_id=model_span,
                parent_span_id=parent_span_id,
                payload={
                    "ok": False,
                    "cancelled": True,
                    "status": "cancelled",
                },
            )
            raise
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
        except asyncio.CancelledError:
            self._emitter.emit(
                EventType.TOOL_CALL_ENDED,
                span_id=tool_span,
                parent_span_id=parent_span_id,
                payload={
                    "ok": False,
                    "cancelled": True,
                    "status": "cancelled",
                },
            )
            raise
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

"""CLI REPL——M1 主界面。驱动 Runner 并呈现事件流（事件 = CLI 消费的网络投影）。"""

import asyncio
import contextlib
import uuid

from grandquiz.kernel.clock import SystemClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink
from grandquiz.kernel.runner import Runner
from grandquiz.providers.echo import DemoEchoProvider


def _format_event(event: AgentEvent) -> str:
    parent = f" <- {event.parent_span_id}" if event.parent_span_id else ""
    return f"  · [{event.seq}] {event.type} ({event.span_id}{parent})"


async def _repl(*, show_events: bool) -> None:
    sink = EventSink()
    if show_events:

        def _printer(event: AgentEvent) -> None:
            print(_format_event(event))

        sink.subscribe(_printer)

    # trace_id 是每次 run 的输入（此处 CLI 铸造，测试里固定），不属于 runner 内部确定性范围。
    emitter = EventEmitter(sink, SystemClock(), trace_id=uuid.uuid4().hex)
    runner = Runner(provider=DemoEchoProvider(), emitter=emitter)

    print("grandquiz CLI (M1, DemoEcho) — type 'exit' or Ctrl-D to quit")
    while True:
        try:
            line = (await asyncio.to_thread(input, "> ")).strip()
        except EOFError:
            break
        if line in {"exit", "quit"}:
            break
        if not line:
            continue
        reply = await runner.run_turn(line)
        print(reply)


def main(*, show_events: bool = True) -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_repl(show_events=show_events))

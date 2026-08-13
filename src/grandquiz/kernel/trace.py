"""TraceStore——把 AgentEvent 流持久化进 SQLite，并投影成 span 树。

trace = 事件的持久化（见 events.py）。一个 span 是一对事件（``*.started`` / ``*.ended``，
共享 ``span_id``）；``build_span_tree`` 是**纯函数**，把事件流折成 span 森林——它是
确定性核心（缝 2）的单元被测对象。kernel 保持领域无关：这里持久化任意 ``type`` 字符串
与不透明 payload，绝不 import ``domain/``。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from grandquiz.kernel.db import connect, migrate
from grandquiz.kernel.events import AgentEvent, EventType

_STARTED_SUFFIX = ".started"
_ENDED_SUFFIX = ".ended"


@dataclass(frozen=True)
class TraceTokenUsage:
    """Provider-neutral token totals derived from completed model spans."""

    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def summarize_token_usage(events: Iterable[AgentEvent]) -> TraceTokenUsage:
    """Project one token-usage definition from the durable event spine.

    Only ``model.ended`` is authoritative: started events have no completed usage and
    malformed provider payloads are ignored rather than leaking adapter details to
    trace consumers.
    """

    prompt_tokens = 0
    completion_tokens = 0
    for event in events:
        if event.type != EventType.MODEL_ENDED:
            continue
        usage_obj = event.payload.get("usage")
        if not isinstance(usage_obj, Mapping):
            continue
        usage = cast("Mapping[str, Any]", usage_obj)
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(prompt, int):
            prompt_tokens += prompt
        if isinstance(completion, int):
            completion_tokens += completion
    return TraceTokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _empty_children() -> list[Span]:
    # 显式类型工厂：children 确定为 list[Span]（裸 default_factory=list 会被推成 list[Unknown]）。
    return []


class Span(BaseModel):
    """一个 span：由 ``*.started`` 开启、匹配的 ``*.ended`` 关闭；error 事件挂其 ``error``。

    ``type`` 是剥掉 ``.started`` / ``.ended`` 后缀的前缀（如 ``"turn"`` / ``"model"``）。
    ``input`` = started payload，``output`` = ended payload。span 经 ``parent_span_id`` 成树。
    """

    span_id: str
    parent_span_id: str | None
    type: str
    start_seq: int
    start_ts: float
    end_ts: float | None = None
    input: Mapping[str, Any] = Field(default_factory=dict)
    output: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    children: list[Span] = Field(default_factory=_empty_children)

    @property
    def latency(self) -> float | None:
        """闭合的 span 才有时延（``end_ts - start_ts``），未闭合返回 None。"""
        if self.end_ts is None:
            return None
        return self.end_ts - self.start_ts

    @property
    def tokens(self) -> int | None:
        """从 output 的 ``usage.total_tokens`` 取 token 用量；缺省返回 None。"""
        if self.output is None:
            return None
        usage_obj = self.output.get("usage")
        if not isinstance(usage_obj, Mapping):
            return None
        usage = cast("Mapping[str, Any]", usage_obj)
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
        return None


Span.model_rebuild()


def build_span_tree(events: Iterable[AgentEvent]) -> list[Span]:
    """把事件流折成 span 森林（纯函数、无 DB）。

    ``*.started`` 开一个 Span；同 ``span_id`` 的 ``*.ended``（前缀 + ``.ended``）关闭它
    （写 ``end_ts`` / ``output``）；共享 ``span_id`` 的 error 事件写其 ``error``。
    经 ``parent_span_id`` 建森林（``parent_span_id is None`` 为根），children 按序排列。
    """
    spans: dict[str, Span] = {}
    ordered: list[Span] = []  # 保持开启顺序，用于稳定的森林 / children 排序
    for event in events:
        span_id = event.span_id
        if span_id is None:
            continue
        if event.type.endswith(_STARTED_SUFFIX):
            prefix = event.type[: -len(_STARTED_SUFFIX)]
            span = Span(
                span_id=span_id,
                parent_span_id=event.parent_span_id,
                type=prefix,
                start_seq=event.seq,
                start_ts=event.ts,
                input=event.payload,
            )
            spans[span_id] = span
            ordered.append(span)
        elif event.type.endswith(_ENDED_SUFFIX):
            prefix = event.type[: -len(_ENDED_SUFFIX)]
            span = spans.get(span_id)
            if span is not None and span.type == prefix:
                span.end_ts = event.ts
                span.output = event.payload
        elif event.type == EventType.ERROR:
            span = spans.get(span_id)
            if span is not None:
                span.error = event.payload

    roots: list[Span] = []
    for span in ordered:
        parent_id = span.parent_span_id
        parent = spans.get(parent_id) if parent_id is not None else None
        if parent is None:
            roots.append(span)  # 根，或父未见（孤儿）——都当根
        else:
            parent.children.append(span)
    for span in ordered:
        span.children.sort(key=lambda child: child.start_seq)
    roots.sort(key=lambda root: root.start_seq)
    return roots


class TraceStore:
    """订阅 AgentEvent 落 SQLite（append-only ``events`` 表），并能重建 span 树。

    用法：``sink.subscribe(store.record)``（Observer 回调）或 ``sink.register(store)``（富
    ``Processor``——见 ``on_event``）。持久化任意 ``type`` 字符串 + 不透明 payload，kernel 不认识
    领域事件类型——领域事件与 kernel 事件走同一条脊柱、同一张表。
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn = connect(db_path)
        migrate(self._conn)

    def on_event(self, event: AgentEvent) -> None:
        """``Processor`` 协议适配——委托到既有 ``record``（不改其语义）。让 TraceStore 能经
        ``EventSink.register`` 注册为富消费者，与 ``subscribe(store.record)`` 行为等价。"""
        self.record(event)

    def record(self, event: AgentEvent) -> None:
        payload_json = json.dumps(dict(event.payload), sort_keys=True, ensure_ascii=False)
        self._conn.execute(
            "INSERT INTO events "
            "(trace_id, seq, ts, type, span_id, parent_span_id, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event.trace_id,
                event.seq,
                event.ts,
                event.type,
                event.span_id,
                event.parent_span_id,
                payload_json,
            ),
        )
        self._conn.commit()

    def events(self, trace_id: str) -> list[AgentEvent]:
        cursor = self._conn.execute(
            "SELECT trace_id, seq, ts, type, span_id, parent_span_id, payload "
            "FROM events WHERE trace_id = ? ORDER BY seq",
            (trace_id,),
        )
        events: list[AgentEvent] = []
        for row in cursor.fetchall():
            payload: Any = json.loads(row[6])
            events.append(
                AgentEvent(
                    trace_id=str(row[0]),
                    seq=int(row[1]),
                    ts=float(row[2]),
                    type=str(row[3]),
                    span_id=None if row[4] is None else str(row[4]),
                    parent_span_id=None if row[5] is None else str(row[5]),
                    payload=payload,
                )
            )
        return events

    def span_tree(self, trace_id: str) -> list[Span]:
        return build_span_tree(self.events(trace_id))

    def close(self) -> None:
        self._conn.close()

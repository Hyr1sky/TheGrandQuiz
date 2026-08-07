"""Record / Replay Provider——把 LLM 响应按请求键落盘；同 key 的随机响应按录制顺序回放。

回放是"事件流回放"的一个特例：LLM 这个外部 I/O 被录进 cassette，回放不触网、不烧 token。
键覆盖 messages、role、resolved model id 与规范化工具契约。任何会影响模型决策的公开执行契约
变化都必须让旧 cassette 大声失效，不能回放出一个“看起来绿”的旧决策。同一公开请求在重试中可能
得到不同响应，因此新 cassette 对一个 key 保存 Completion 序列；旧的单条 entry 仍可重复回放。
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from grandquiz.kernel.recovery import ErrorClass
from grandquiz.providers.base import (
    Completion,
    Message,
    Provider,
    Role,
    ToolCall,
    ToolSpec,
    Usage,
)


class ReplayMiss(Exception):
    """回放时键未命中——大声失败，绝不返回一个静默的错误答案。

    ``error_class = FATAL``（决策 6）：cassette 缺录是 harness bug，kernel ``RecoveryPolicy`` 必
    ``PROPAGATE``、**绝不** ``SKIP``——否则会把 eval / replay 配置错误静默吞成"本轮跳过"。
    """

    error_class = ErrorClass.FATAL


_FINGERPRINT_VERSION = 2
CassetteEntry = dict[str, Any]
CassetteValue = CassetteEntry | list[CassetteEntry]


def _normalized_tools(tools: Sequence[ToolSpec] | None) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools or ()
        ),
        key=lambda tool: str(tool["name"]),
    )


def tool_contract_hash(tools: Sequence[ToolSpec] | None) -> str:
    canonical = json.dumps(
        _normalized_tools(tools),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def replay_key(
    messages: Sequence[Message],
    role: Role,
    model_id: str,
    *,
    tools: Sequence[ToolSpec] | None = None,
) -> str:
    """按完整公开执行信封算稳定键；工具声明顺序不影响结果。

    messages 走确定性 JSON（``sort_keys`` + 紧凑分隔符）；role / model_id 拼进被 hash 的原文，
    保证不同角色 / 模型即使 messages 相同也不撞键。

    ``exclude_none`` 是刻意的：Message 后加的 ``tool_calls`` / ``tool_call_id`` 对纯文本消息为
    None，被排除后其序列化与加 tool 字段前逐字节一致——既有 on-disk cassette（键是旧 schema 算的）
    在 tool-calling 落地后仍命中，不失效。
    """
    # 纯文本路径没有新增执行参数，继续使用 v1 key，避免无意义重录 Reader/判卷等 cassette。
    # 一旦存在工具契约即切到 v2 信封；工具增删/说明/schema 变化都会 miss。
    if not tools:
        messages_json = json.dumps(
            [message.model_dump(exclude_none=True) for message in messages],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        raw = f"{messages_json}\x00{role}\x00{model_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    envelope = json.dumps(
        {
            "fingerprint_version": _FINGERPRINT_VERSION,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "model": model_id,
            "role": role,
            "tools": _normalized_tools(tools),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(envelope.encode("utf-8")).hexdigest()


class Cassette:
    """JSON 响应库；同一请求 key 可保存有序响应序列，旧单条格式继续可读。"""

    def __init__(self, entries: dict[str, CassetteValue] | None = None) -> None:
        self._entries = entries if entries is not None else {}
        self._replay_positions: dict[str, int] = {}

    @classmethod
    def load(cls, path: str | Path) -> "Cassette":
        raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
        entries: dict[str, CassetteValue] = {str(k): v for k, v in raw.items()}
        return cls(entries)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self._entries, sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _completion(entry: CassetteEntry) -> Completion:
        usage_data: Any = entry.get("usage", {})
        tool_calls_data: Any = entry.get("tool_calls")
        tool_calls = (
            [ToolCall(**tc) for tc in tool_calls_data] if tool_calls_data is not None else None
        )
        return Completion(text=str(entry["text"]), tool_calls=tool_calls, usage=Usage(**usage_data))

    def get(self, key: str) -> Completion | None:
        """返回首条已录响应但不消费；供 ``reuse_existing`` 保持既有语义。"""

        value = self._entries.get(key)
        if value is None:
            return None
        if isinstance(value, list):
            return self._completion(value[0]) if value else None
        return self._completion(value)

    def next(self, key: str) -> tuple[Completion | None, bool]:
        """消费同 key 的下一条序列响应；bool 表示 key 存在但序列已耗尽。"""

        value = self._entries.get(key)
        if value is None:
            return None, False
        if not isinstance(value, list):
            return self._completion(value), False
        position = self._replay_positions.get(key, 0)
        if position >= len(value):
            return None, True
        self._replay_positions[key] = position + 1
        return self._completion(value[position]), False

    def put(
        self,
        key: str,
        completion: Completion,
        *,
        role: Role,
        model_id: str,
        tools: Sequence[ToolSpec] | None = None,
    ) -> None:
        entry: CassetteEntry = {
            "fingerprint_version": _FINGERPRINT_VERSION,
            "role": role,
            "model": model_id,
            "tool_contract_hash": tool_contract_hash(tools),
            "text": completion.text,
            "usage": completion.usage.model_dump(),
        }
        # 只在真有工具调用时写 tool_calls 键——纯文本 completion 落盘形状不变（既有 cassette 不脏）。
        if completion.tool_calls is not None:
            entry["tool_calls"] = [tc.model_dump() for tc in completion.tool_calls]
        existing = self._entries.get(key)
        if existing is None:
            self._entries[key] = entry
        elif isinstance(existing, list):
            existing.append(entry)
        else:
            self._entries[key] = [existing, entry]


class RecordingProvider:
    """包裹真实 provider：每次付费响应都追加到该请求 key 的有序 cassette 序列。"""

    def __init__(
        self,
        inner: Provider,
        cassette: Cassette,
        model_for_role: Mapping[Role, str],
        *,
        checkpoint_path: str | Path | None = None,
        reuse_existing: bool = False,
    ) -> None:
        self._inner = inner
        self._cassette = cassette
        self._model_for_role = model_for_role
        self._checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else None
        self._reuse_existing = reuse_existing

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        model_id = self._model_for_role[role]
        key = replay_key(messages, role, model_id, tools=tools)
        if self._reuse_existing:
            existing = self._cassette.get(key)
            if existing is not None:
                return existing
        completion = await self._inner.complete(messages, role=role, tools=tools)
        self._cassette.put(key, completion, role=role, model_id=model_id, tools=tools)
        if self._checkpoint_path is not None:
            self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._cassette.save(self._checkpoint_path)
        return completion


class ReplayProvider:
    """纯回放：同 key 序列逐次消费；旧单条响应保持可重复，未命中大声失败。"""

    def __init__(self, cassette: Cassette, model_for_role: Mapping[Role, str]) -> None:
        self._cassette = cassette
        self._model_for_role = model_for_role

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        model_id = self._model_for_role[role]
        key = replay_key(messages, role, model_id, tools=tools)
        completion, sequence_exhausted = self._cassette.next(key)
        if completion is None:
            reason = "序列已耗尽" if sequence_exhausted else "cassette 无此响应"
            raise ReplayMiss(
                f"回放未命中：role={role} model={model_id} key={key[:12]}…（{reason}）"
            )
        return completion

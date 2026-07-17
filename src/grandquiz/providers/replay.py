"""Record / Replay Provider——把 LLM 响应按键落盘，回放时直接命中。

回放是"事件流回放"的一个特例：LLM 这个外部 I/O 被录进 cassette，回放不触网、不烧 token。
键覆盖 messages、role、resolved model id 与规范化工具契约。任何会影响模型决策的公开执行契约
变化都必须让旧 cassette 大声失效，不能回放出一个“看起来绿”的旧决策。
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
    """JSON 文件形态的响应库。lookup 只用 key；每条额外存 role/model 供人肉调试。"""

    def __init__(self, entries: dict[str, dict[str, Any]] | None = None) -> None:
        self._entries: dict[str, dict[str, Any]] = entries if entries is not None else {}

    @classmethod
    def load(cls, path: str | Path) -> "Cassette":
        raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
        entries: dict[str, dict[str, Any]] = {str(k): v for k, v in raw.items()}
        return cls(entries)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self._entries, sort_keys=True, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key: str) -> Completion | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        usage_data: Any = entry.get("usage", {})
        # tool_calls 可选：只有出工具的 completion 才带此键；纯文本条目仍是旧形状（无该键）。
        tool_calls_data: Any = entry.get("tool_calls")
        tool_calls = (
            [ToolCall(**tc) for tc in tool_calls_data] if tool_calls_data is not None else None
        )
        return Completion(text=str(entry["text"]), tool_calls=tool_calls, usage=Usage(**usage_data))

    def put(
        self,
        key: str,
        completion: Completion,
        *,
        role: Role,
        model_id: str,
        tools: Sequence[ToolSpec] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
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
        self._entries[key] = entry


class RecordingProvider:
    """包裹一个真实 inner provider：complete 时算键、透传给 inner、把响应落 cassette。"""

    def __init__(
        self, inner: Provider, cassette: Cassette, model_for_role: Mapping[Role, str]
    ) -> None:
        self._inner = inner
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
        completion = await self._inner.complete(messages, role=role, tools=tools)
        self._cassette.put(key, completion, role=role, model_id=model_id, tools=tools)
        return completion


class ReplayProvider:
    """纯回放：complete 时算键查 cassette，命中即返回，未命中 raise ReplayMiss。不烧 token。"""

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
        completion = self._cassette.get(key)
        if completion is None:
            raise ReplayMiss(
                f"回放未命中：role={role} model={model_id} key={key[:12]}…（cassette 无此响应）"
            )
        return completion

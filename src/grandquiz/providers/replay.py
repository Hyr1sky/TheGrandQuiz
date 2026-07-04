"""Record / Replay Provider——把 LLM 响应按键落盘，回放时直接命中。

回放是"事件流回放"的一个特例：LLM 这个外部 I/O 被录进 cassette，回放不触网、不烧 token。
键 = ``sha256(messages)`` + ``role`` + resolved model id——**必须含 role 与 model_id**，否则
basic=deepseek 与 enrich=qwen 的相同 messages 会撞键、串错模型响应，毁掉"完全确定"。
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from grandquiz.providers.base import Completion, Message, Provider, Role, Usage


class ReplayMiss(Exception):
    """回放时键未命中——大声失败，绝不返回一个静默的错误答案。"""


def replay_key(messages: Sequence[Message], role: Role, model_id: str) -> str:
    """按 messages + role + resolved model id 算稳定键。

    messages 走确定性 JSON（``sort_keys`` + 紧凑分隔符）；role / model_id 拼进被 hash 的原文，
    保证不同角色 / 模型即使 messages 相同也不撞键。
    """
    messages_json = json.dumps(
        [m.model_dump() for m in messages],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    raw = f"{messages_json}\x00{role}\x00{model_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        return Completion(text=str(entry["text"]), usage=Usage(**usage_data))

    def put(self, key: str, completion: Completion, *, role: Role, model_id: str) -> None:
        self._entries[key] = {
            "role": role,
            "model": model_id,
            "text": completion.text,
            "usage": completion.usage.model_dump(),
        }


class RecordingProvider:
    """包裹一个真实 inner provider：complete 时算键、透传给 inner、把响应落 cassette。"""

    def __init__(
        self, inner: Provider, cassette: Cassette, model_for_role: Mapping[Role, str]
    ) -> None:
        self._inner = inner
        self._cassette = cassette
        self._model_for_role = model_for_role

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        model_id = self._model_for_role[role]
        key = replay_key(messages, role, model_id)
        completion = await self._inner.complete(messages, role=role)
        self._cassette.put(key, completion, role=role, model_id=model_id)
        return completion


class ReplayProvider:
    """纯回放：complete 时算键查 cassette，命中即返回，未命中 raise ReplayMiss。不烧 token。"""

    def __init__(self, cassette: Cassette, model_for_role: Mapping[Role, str]) -> None:
        self._cassette = cassette
        self._model_for_role = model_for_role

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        model_id = self._model_for_role[role]
        key = replay_key(messages, role, model_id)
        completion = self._cassette.get(key)
        if completion is None:
            raise ReplayMiss(
                f"回放未命中：role={role} model={model_id} key={key[:12]}…（cassette 无此响应）"
            )
        return completion

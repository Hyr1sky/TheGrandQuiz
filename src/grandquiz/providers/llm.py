"""OpenAICompatProvider——OpenAI 兼容的真实 LLM provider（basic=deepseek / enrich=qwen）。

两个命名角色各自从 ``.env`` 读 base_url / api_key / model / timeout / disable_thinking；deepseek 与
qwen 都提供 OpenAI 兼容端点，故用同一个 ``AsyncOpenAI`` 客户端类 + 各自 base_url。密钥只经环境变量
注入，绝不进代码 / git（见 CLAUDE.md 密钥纪律）。

实现 ``providers/base.py`` 的 ``Provider`` 协议——因此在 ingest / Reader 里可与 DemoEcho /
Record / Replay 互换：测试传假件、录制传 ``RecordingProvider(OpenAICompatProvider.from_env())``、
CI 回放传 ``ReplayProvider(cassette)``，调用方不变。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from grandquiz.providers.base import Completion, Message, Role, Usage

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RoleConfig:
    """一个命名角色的 LLM 配置（对应 .env 的一组 ``<PREFIX>*`` 变量）。"""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0
    # 思考模式开关。Qwen3/DashScope 用 extra_body 的 enable_thinking=False 关（非流式往往必须关）；
    # deepseek 侧是否认同名参数由 smoke 验证（见 scripts/smoke_llm.py）。
    disable_thinking: bool = False


def _read_role(prefix: str) -> RoleConfig:
    def required(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise RuntimeError(f"缺少环境变量 {name}（见 .env.example）")
        return value

    return RoleConfig(
        api_key=required(f"{prefix}API_KEY"),
        base_url=required(f"{prefix}BASE_URL"),
        model=required(f"{prefix}MODEL"),
        timeout_seconds=float(os.environ.get(f"{prefix}TIMEOUT_SECONDS", "60")),
        disable_thinking=os.environ.get(f"{prefix}DISABLE_THINKING", "").strip().lower() in _TRUTHY,
    )


class OpenAICompatProvider:
    """OpenAI 兼容 provider：按角色路由到各自的 base_url / model。"""

    def __init__(self, role_configs: dict[Role, RoleConfig]) -> None:
        self._configs = role_configs
        self._clients: dict[Role, AsyncOpenAI] = {
            role: AsyncOpenAI(
                api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout_seconds
            )
            for role, cfg in role_configs.items()
        }

    @classmethod
    def from_env(cls) -> OpenAICompatProvider:
        """从 .env 读两角色：``LLM_*`` → basic（deepseek）、``ENRICH_LLM_*`` → enrich（qwen）。"""
        return cls({"basic": _read_role("LLM_"), "enrich": _read_role("ENRICH_LLM_")})

    @property
    def model_for_role(self) -> dict[Role, str]:
        """各角色解析后的 model id——喂 Recording/Replay 算 replay 键（防跨模型串键）。"""
        return {role: cfg.model for role, cfg in self._configs.items()}

    async def complete(self, messages: Sequence[Message], *, role: Role = "basic") -> Completion:
        config = self._configs[role]
        client = self._clients[role]
        oai_messages = cast(
            "list[ChatCompletionMessageParam]",
            [{"role": m.role, "content": m.content} for m in messages],
        )
        extra_body: dict[str, object] = {}
        if config.disable_thinking:
            extra_body["enable_thinking"] = False
        response = await client.chat.completions.create(
            model=config.model,
            messages=oai_messages,
            extra_body=extra_body or None,
        )
        text = response.choices[0].message.content or ""
        usage = Usage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
        )
        return Completion(text=text, usage=usage)

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端（长生命周期 provider 退出时调用）。"""
        for client in self._clients.values():
            await client.close()

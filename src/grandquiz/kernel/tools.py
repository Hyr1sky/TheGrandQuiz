"""工具注册表——自由 ReAct 循环的机制层（R1-S1）。

一个 ``Tool`` = 名称 + 描述 + pydantic 入参 schema + async handler。``ToolRegistry`` 按名登记，
``dispatch`` 按名找工具、用 schema 校验 LLM 给的 arguments、调 handler、返回结果字符串。

**kernel 领域无关**：本模块只认 pydantic 与 kernel 自身的 ``ErrorClass``，**禁止 import domain**
（``kernel↛domain`` 的 import-linter 门会挡红）。具体工具（echo / 出题 / 判卷…）在组装点或 domain
层定义后 ``register`` 进来，registry 不认识它们的语义。

**可恢复失败走 ``ModelRetry``（DEGRADED）**：未知工具名 / 入参校验失败都抛 ``ModelRetry``，它带
``ErrorClass.DEGRADED`` 标——runner 的循环把它交给 M6 ``RecoveryPolicy`` 裁成 ``SKIP``，将错误文本
作为 tool 结果回灌给 LLM 改路重试（有界于 ``max_iterations``）。handler 自身抛的领域异常按其自带
``error_class`` 裁决（未打标 → 默认 FATAL 冒泡）。
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from grandquiz.kernel.recovery import ErrorClass


class ModelRetry(Exception):
    """可恢复的工具失败：未知工具名 / 入参校验失败。

    ``error_class = DEGRADED``——经 ``RecoveryPolicy`` 裁 ``SKIP``，错误回灌让 LLM 改参 / 改路重试
    （有界于循环的 ``max_iterations``，绝不无限重试）。与 handler 抛的领域异常区分：那些按各自
    ``error_class`` 裁决，本类专表"参数层面可让模型自我修正"的失败。
    """

    error_class = ErrorClass.DEGRADED


@dataclass(frozen=True)
class Tool:
    """一个可调用工具的声明。

    ``params`` 是 pydantic 模型类，``dispatch`` 用它校验 arguments（失败 → ``ModelRetry``）。
    ``handler`` 收**已校验**的模型实例、返回结果字符串（回灌进 messages）。异构 registry 存 ``Tool``
    （handler 落 ``Callable[[Any], ...]``）——逐工具的类型安全由各自 handler 签名保证。
    """

    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[[Any], Awaitable[str]]


class ToolRegistry:
    """按名登记 ``Tool`` 并 dispatch。注册序无关（按名查找）；重名拒绝（早失败胜过静默覆盖）。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名重复：{tool.name!r} 已注册")
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    async def dispatch(self, name: str, arguments: Mapping[str, Any]) -> str:
        """按 ``name`` 找工具 → 用其 schema 校验 ``arguments`` → 调 handler → 返回结果字符串。

        未知工具名 / 校验失败均抛 ``ModelRetry``（DEGRADED，供 runner 回灌重试）。handler 自身的
        异常原样冒泡（由 runner 交 ``RecoveryPolicy`` 按其 ``error_class`` 裁决）。
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ModelRetry(f"未知工具 {name!r}（可选：{sorted(self._tools)}）")
        try:
            validated = tool.params.model_validate(dict(arguments))
        except ValidationError as exc:
            raise ModelRetry(f"工具 {name!r} 入参校验失败：{exc}") from exc
        return await tool.handler(validated)

"""ContextBuilder（M5）——把一次生成的上下文从"system + history 临时拼装"升级成**有序分区装配**。

领域无关机制（kernel 保持领域无关，呼应"领域无关 runtime"卖点）：一个 ``ContextBuilder`` 持有
有序的 ``Partition`` 列表，每个分区 = 名字 + 内容 provider（``str`` **或** ``Callable[[], str]``）。
``build(history, user_message)`` 按序装配：**各分区（system → 注入分区如 memory）→ history →
user**，返回 ``list[Message]``。callable provider 在**每次 build 现取**——学情随考核推进刷新（同一
贯穿多回合，下一回合的 build 反映最新薄弱账），这是"记忆互通复用"的兑现关键。

kernel↛domain：分区只认名字 + 字符串 provider，从不认识 domain 的 Learning Memory / 偏好类型。
domain 侧把"渲学情文本"的闭包（捕获 memory + preferences）作为某分区的 provider 传进来
（domain→kernel 合法），kernel 只调它拿字符串。

**扩展性**：分区是列表，日后加 persona / knowledge 分区**零改本机制**——只在装配点多塞一个
``Partition``、顺序即声明序。

**预算 / 压缩接缝（本 issue 只留缝、不实现）**：
- 每个 ``Partition`` 带可选 ``budget`` 字段（每分区 token 预算），下一程 context compression 消费。
- ``ContextBuilder`` 预留 ``policy`` 钩子（``CompressionPolicy``）：非 None 时对每个分区内容调
  ``compress(partition, content)``。**默认 ``policy=None`` 恒等透传**（不裁剪任何内容）。真压缩器
  （按 budget 摘要 / 截断 / 丢历史）留待下一程接入，接缝形状此刻钉死。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from grandquiz.providers.base import Message

# 分区内容来源：静态字符串，或每次 build 现取的无参 callable（学情随考核推进刷新）。
ContentProvider = str | Callable[[], str]


@dataclass(frozen=True)
class Partition:
    """上下文的一个有序分区：名字 + 内容 provider（+ 预算接缝）。

    ``provider`` 为 ``str`` → 静态内容；为 ``Callable[[], str]`` → 每次 build 现取（如学情块）。
    分区当前恒渲染成一条 ``system`` 角色消息，置于 history 之前（system 前言区）。
    ``budget``：该分区的可选 token 预算——**压缩 / 预算接缝**，本 issue 不消费（恒不裁剪），
    下一程 context compression 按它做摘要 / 截断。
    """

    name: str
    provider: ContentProvider
    budget: int | None = None


class CompressionPolicy(Protocol):
    """按分区裁剪内容的策略接缝（本 issue 不实现具体压缩，只钉死形状）。

    ``compress`` 收到分区（含其 ``budget``）与已取到的内容，返回（可能被压缩 / 截断的）内容。
    下一程接入真压缩器（按 budget 摘要 / 截断 / 丢历史）时实现本协议、经 ``ContextBuilder`` 的
    ``policy`` 参传入即可，``build`` 的装配逻辑一行不改。
    """

    def compress(self, partition: Partition, content: str) -> str: ...


class ContextBuilder:
    """把有序分区 + history + 当前 user 消息装配成 ``list[Message]``（领域无关机制）。

    ``build`` 顺序：各分区（按声明序，空内容跳过）→ history（原样展开）→ 当前 user 消息。
    分区内容经可选 ``policy`` 钩子（默认恒等透传）——预算 / 压缩接缝留给下一程。
    """

    def __init__(
        self, partitions: Sequence[Partition], *, policy: CompressionPolicy | None = None
    ) -> None:
        self._partitions = list(partitions)
        # 压缩策略接缝：None → 恒等透传（本 issue 不压缩）；下一程传入真压缩器按 budget 裁剪。
        self._policy = policy

    def build(self, history: Sequence[Message], user_message: str) -> list[Message]:
        """按序装配：各分区（system 前言区）→ history → 当前 user 消息。

        callable provider 在此现取（每次 build 一次），故学情随考核推进刷新。空内容分区（provider
        返回空串 / 经 policy 压成空）被跳过，不塞空 system 噪声。
        """
        messages: list[Message] = []
        for partition in self._partitions:
            content = self._resolve(partition)
            if content:
                messages.append(Message(role="system", content=content))
        messages.extend(history)
        messages.append(Message(role="user", content=user_message))
        return messages

    def _resolve(self, partition: Partition) -> str:
        """取分区内容（str 直用 / callable 现取），再过压缩策略接缝（默认恒等透传）。"""
        provider = partition.provider
        content = provider() if callable(provider) else provider
        if self._policy is not None:
            content = self._policy.compress(partition, content)
        return content

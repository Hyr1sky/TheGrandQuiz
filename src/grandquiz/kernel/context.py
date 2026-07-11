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

import math
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from grandquiz.providers.base import Message

# 分区内容来源：静态字符串，或每次 build 现取的无参 callable（学情随考核推进刷新）。
ContentProvider = str | Callable[[], str]

# East-Asian Wide / Fullwidth ≈ CJK / 全角字符，在 BPE 分词里 token 密（约 1 token/字）。
_WIDE_EAW = frozenset({"W", "F"})


class TokenCounter(Protocol):
    """token 估算契约——**注入式**（同 ``Clock`` / ``Rng`` 的确定性注入思路）。

    预算裁剪要在**发请求前**估上下文占多少 token，而 ``Completion.usage`` 是**事后**才知道的真实值、
    用不上；故装配侧需要一个可注入、**确定性**的事前估算器：同串恒同值 → 预算/压缩决策可 replay。
    """

    def count(self, text: str) -> int: ...


@dataclass(frozen=True)
class HeuristicTokenCounter:
    """CJK 感知的**确定性**启发式 token 估算（预算用途，非计费）。

    刻意不用 tiktoken：deepseek / qwen 非 OpenAI 模型、编码对不齐、硬套反失真；预算只需量级正确。
    启发式 provider 无关、零依赖、纯函数（无 clock / random）→ replay 逐字节对得齐。规则：East-Asian
    Wide / Fullwidth 字符（CJK / 全角）按 ``cjk_chars_per_token`` 计（默认 ~1 token/字，token 密）、
    其余（拉丁 / 空白 / 标点）按 ``other_chars_per_token`` 计（默认 ~4 字符/token，GPT 系经验值）。
    两比率是构造参数、可调；默认偏保守（宁高估不低估，免真超模型上限）。空串 → 0。
    """

    cjk_chars_per_token: float = 1.0
    other_chars_per_token: float = 4.0

    def count(self, text: str) -> int:
        wide = sum(1 for ch in text if unicodedata.east_asian_width(ch) in _WIDE_EAW)
        other = len(text) - wide
        return math.ceil(wide / self.cjk_chars_per_token + other / self.other_chars_per_token)


@dataclass(frozen=True)
class Partition:
    """上下文的一个有序分区：名字 + 内容 provider（+ 预算接缝）。

    ``provider`` 为 ``str`` → 静态内容；为 ``Callable[[], str]`` → 每次 build 现取（如学情块）。
    分区当前恒渲染成一条 ``system`` 角色消息，置于 history 之前（system 前言区）。
    ``budget``：该分区的可选 token 预算——``BudgetCompressionPolicy`` 按它做**确定性头截断**
    （软预算：超预算截断不抛；无 budget 恒不裁剪）。
    """

    name: str
    provider: ContentProvider
    budget: int | None = None


class CompressionPolicy(Protocol):
    """按分区裁剪内容的策略接缝——``BudgetCompressionPolicy`` 是首个实现（按 budget 头截断）。

    ``compress`` 收到分区（含其 ``budget``）与已取内容，返回（可能被截断的）内容。经
    ``ContextBuilder`` 的 ``policy`` 传入、``build`` 逻辑一行不改；``policy=None`` 恒等透传。
    """

    def compress(self, partition: Partition, content: str) -> str: ...


class ContextBudgetExceeded(RuntimeError):
    """装配后上下文总 token 数超过硬上限——**大声失败**（同 ``MaxIterationsExceeded`` 哲学）。

    刻意不静默截断到残缺上下文：那会把"上下文爆了"伪装成正常回答，毁可观测 + 出题/判卷质量。分区
    软预算（``BudgetCompressionPolicy``）尽力压过后总量仍越硬上限，才抛——交调用方（装配点）处置。
    未打 ``error_class`` → 经 ``RecoveryPolicy`` 默认归 FATAL、冒泡。
    """

    def __init__(self, used: int, ceiling: int) -> None:
        super().__init__(f"上下文装配后 {used} tokens 超过硬上限 {ceiling} tokens")
        self.used = used
        self.ceiling = ceiling


@dataclass(frozen=True)
class BudgetCompressionPolicy:
    """按 ``Partition.budget`` 头截断的软预算策略（超预算截断不抛；实现 ``CompressionPolicy``）。

    无 ``budget`` 或已合身 → 原样；超预算 → 保**开头**能放下的最长字符前缀 + 截断标记 ``marker``。
    截断有损但有界、确定（二分求前缀、无 clock/random → replay 对得齐）。"保头丢尾"——分区内容
    （system / 库存清单 / 学情）通常越靠前越纲领。总硬上限的大声失败由 ``ContextBuilder`` 负责
    （``ContextBudgetExceeded``），本策略**永不抛**——软/硬分层。``marker`` 取轻（默认省略号 ≈1
    token）：标记本身吃预算，重标记会挤掉正文。
    """

    counter: TokenCounter
    marker: str = "…"

    def compress(self, partition: Partition, content: str) -> str:
        budget = partition.budget
        if budget is None or self.counter.count(content) <= budget:
            return content  # 无预算 / 已合身 → 原样（向后兼容）
        room = budget - self.counter.count(self.marker)
        if room <= 0:
            # 预算连标记都放不下：best-effort 硬截到 budget（不缀标记、仍不抛）。
            return self._fit(content, budget)
        return self._fit(content, room) + self.marker

    def _fit(self, content: str, token_budget: int) -> str:
        """二分求 ``count(前缀) <= token_budget`` 的**最长字符前缀**（确定性）。

        token 数随字符前缀单调不减，故可二分：O(log n) 次 ``count`` 而非逐字符线性试。
        """
        if token_budget <= 0:
            return ""
        lo, hi = 0, len(content)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.counter.count(content[:mid]) <= token_budget:
                lo = mid
            else:
                hi = mid - 1
        return content[:lo]


class HistoryCompressor(Protocol):
    """把（可能很长的）对话 history 压成更短一段的策略——C3 历史压缩接缝（选项 B：独立抽象）。

    与 ``CompressionPolicy``（按分区裁 ``str``）分开：history 是 ``list[Message]``、压缩语义不同（保
    最近若干轮 / 摘要老轮），故独立抽象。``ContextBuilder.build`` 在 extend history 前调它。
    """

    def compress(self, history: Sequence[Message]) -> list[Message]: ...


@dataclass(frozen=True)
class SlidingWindowHistoryCompressor:
    """只保最近 ``max_turns`` 轮对话原样、更早的丢弃（C3a：确定性、无 LLM）。

    一轮 = (user, assistant) 一对——``run_agent_turn`` 跨轮裁剪后 history 恒 user/assistant 交替，
    保最近 ``max_turns`` 轮 = 保最后 ``max_turns*2`` 条。老轮**摘要**（而非丢弃）留 C3b。纯代码、无
    clock/random → replay 稳。``max_turns=0`` → 全丢（显式挡 ``[-0:]`` 取全部的坑）。
    """

    max_turns: int = 5

    def compress(self, history: Sequence[Message]) -> list[Message]:
        keep = self.max_turns * 2
        if keep <= 0:
            return []
        return list(history[-keep:]) if len(history) > keep else list(history)


class Summarizer(Protocol):
    """把若干条消息折进已有摘要、产出新摘要的**异步**策略（注入式）——C3b 的 LLM 槽契约。

    kernel 只认此协议（收 prior_summary + 消息 → 新摘要串），不认识 domain 的 provider /
    prompt：domain 用真 LLM（summarize prompt + Record/Replay）实现并注入，测试注入 fake。
    """

    async def summarize(self, prior_summary: str, messages: Sequence[Message]) -> str: ...


class SummarizingHistoryCompressor:
    """滚动摘要 + 最近窗口（``HistoryCompressor``；LangChain summary-buffer memory 形状）。

    读 / 写分离，避开"sync 里 await 不了 LLM"：
    - ``compress``（**sync**，build 用）：返回 ``[system(滚动摘要)] + 尚未摘要的最近若干轮``——
      只读缓存 ``_summary``、**无 LLM 调用**。
    - ``prune``（**async**，``run_agent_turn`` 每轮后调）：把新被挤出窗口（超 ``max_turns`` 轮）的
      老轮经注入的 ``Summarizer`` 折进 ``_summary``、推进 ``_summarized_turns``。摘哪几轮确定性代码
      定、LLM 只产摘要文本（同"LLM 判卷、代码记账"）。

    有状态（摘要 + 已摘轮数跨回合累积）故非 frozen。一轮 = user+assistant 两条。
    """

    _SUMMARY_PREFIX = "此前对话摘要："

    def __init__(self, summarizer: Summarizer, *, max_turns: int = 5) -> None:
        self._summarizer = summarizer
        self._max_turns = max_turns
        self._summary = ""
        self._summarized_turns = 0

    def compress(self, history: Sequence[Message]) -> list[Message]:
        kept = list(history[self._summarized_turns * 2 :])  # 尚未摘要的（= prune 后的最近窗口）
        if self._summary:
            summary_msg = Message(role="system", content=self._SUMMARY_PREFIX + self._summary)
            return [summary_msg, *kept]
        return kept

    async def prune(self, history: Sequence[Message]) -> None:
        total_turns = len(history) // 2
        evict_boundary = max(0, total_turns - self._max_turns)  # 应被摘要（超窗口）的轮数
        if evict_boundary <= self._summarized_turns:
            return  # 无新老轮被挤出 → 幂等，不重复摘
        newly_evicted = list(history[self._summarized_turns * 2 : evict_boundary * 2])
        self._summary = await self._summarizer.summarize(self._summary, newly_evicted)
        self._summarized_turns = evict_boundary


class ContextBuilder:
    """把有序分区 + history + 当前 user 消息装配成 ``list[Message]``（领域无关机制）。

    ``build`` 顺序：各分区（按声明序，空内容跳过）→ history（原样展开）→ 当前 user 消息。
    分区内容经可选 ``policy``（默认恒等透传；传 ``BudgetCompressionPolicy`` 按各分区 budget 截断）。
    ``counter`` + ``total_budget`` 都给时，装配后总 token 超硬上限 → 抛 ``ContextBudgetExceeded``；
    默认全 None → 从不检查（向后兼容）。
    """

    def __init__(
        self,
        partitions: Sequence[Partition],
        *,
        policy: CompressionPolicy | None = None,
        counter: TokenCounter | None = None,
        total_budget: int | None = None,
        history_compressor: HistoryCompressor | None = None,
    ) -> None:
        self._partitions = list(partitions)
        # 分区软预算：None → 恒等透传；BudgetCompressionPolicy 按各 Partition.budget 头截断。
        self._policy = policy
        # 总硬上限（大声失败）：counter + total_budget 都给才检查——装配后总 token 超上限抛
        # ContextBudgetExceeded。默认全 None → 从不检查（向后兼容，不破现有测试 / cassette）。
        self._counter = counter
        self._total_budget = total_budget
        # 历史压缩：None → history 原样全展；SlidingWindow / Summarizing 则先压后 extend。
        self._history_compressor = history_compressor

    def build(self, history: Sequence[Message], user_message: str) -> list[Message]:
        """按序装配：各分区（system 前言区）→ history → 当前 user 消息。

        callable provider 在此现取（每次 build 一次），故学情随考核推进刷新。空内容分区（provider
        返回空串 / 经 policy 压成空）被跳过，不塞空 system 噪声。末尾校验总预算（超上限则抛）。
        """
        messages: list[Message] = []
        for partition in self._partitions:
            content = self._resolve(partition)
            if content:
                messages.append(Message(role="system", content=content))
        if self._history_compressor is not None:
            history = self._history_compressor.compress(history)  # 保最近若干轮 / 摘要老轮
        messages.extend(history)
        messages.append(Message(role="user", content=user_message))
        self._enforce_total_budget(messages)
        return messages

    def _enforce_total_budget(self, messages: Sequence[Message]) -> None:
        """装配后总 token 超硬上限 → 抛 ``ContextBudgetExceeded``（大声失败）。未设上限则跳过。"""
        if self._total_budget is None or self._counter is None:
            return
        used = sum(self._counter.count(message.content) for message in messages)
        if used > self._total_budget:
            raise ContextBudgetExceeded(used, self._total_budget)

    def _resolve(self, partition: Partition) -> str:
        """取分区内容（str 直用 / callable 现取），再过压缩策略接缝（默认恒等透传）。"""
        provider = partition.provider
        content = provider() if callable(provider) else provider
        if self._policy is not None:
            content = self._policy.compress(partition, content)
        return content

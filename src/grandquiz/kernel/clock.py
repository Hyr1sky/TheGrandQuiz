"""注入式的时间与随机源，让运行确定、可回放。

kernel 不直接调用 ``time`` / ``random``——一切走 Clock 与种子化 RNG，否则 replay 永远对不齐。
"""

import random
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    """墙上时钟，用于生产 / CLI。"""

    def now(self) -> float:
        return time.time()


class ManualClock:
    """测试 / 回放用的确定性时钟：每次 ``now()`` 返回当前值再按 ``tick`` 前进
    （于是 started < ended 自然成立）。"""

    def __init__(self, start: float = 0.0, tick: float = 1.0) -> None:
        self._t = start
        self._tick = tick

    def now(self) -> float:
        current = self._t
        self._t += self._tick
        return current


Rng = random.Random


def new_rng(seed: int) -> random.Random:
    """种子化 RNG——需要随机性的地方注入它，保证可复现。"""
    return random.Random(seed)

"""Prompt 模板加载 + 版本化——模板独立于代码存放（CLAUDE.md），trace 记版本号（消台账 #5）。

版本号 = 内容 hash（``<name>@<8hex>``）：改模板即自动换版本，无需手工 bump；且因系统提示进
messages、``replay_key`` 随之变化 → 旧 cassette 自动失效、强制重录（这正是调优后该有的行为）。
trace 记此版本号 → eval 回归可归因到具体 prompt。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    """一个版本化的 prompt 模板：名字 + 正文 + 内容 hash 版本号。"""

    name: str
    text: str
    version: str


def load_prompt(name: str) -> Prompt:
    """从 ``prompts/<name>.md`` 读模板；版本号 = ``<name>@<正文 sha256 前 8 位>``。"""
    text = (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return Prompt(name=name, text=text, version=f"{name}@{digest}")

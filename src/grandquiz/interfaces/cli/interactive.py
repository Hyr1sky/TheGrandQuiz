"""交互式作答 responder——终端里逐题问学习者（questionary），实现 domain 的 ``Responder`` 协议。

放 ``interfaces/cli``：依赖 domain 协议、由 CLI 侧提供真交互实现（分层——domain 不 import
interfaces）。选择题（``options`` 非空）→ ``questionary.select`` 单选；开放 / 追问（``options``
为 None）→ ``questionary.text`` 自由输入。两者都用 ``.ask_async()``——``assess_once`` 在 asyncio
loop 内 ``await``，用同步的 ``.ask()`` 会起嵌套 loop 崩溃。用户 Ctrl+C / ESC 取消时 ``ask_async()``
返回 None → 抛 ``KeyboardInterrupt``，由 quiz 命令捕获、优雅退出本次会话（不吞成空作答污染判卷）。
"""

from collections.abc import Sequence

import questionary


class InteractiveResponder:
    """终端逐题作答——结构上满足 ``Responder`` 协议；真机试跑留给 human（分支由 monkeypatch 测）。"""

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        if options:
            question = questionary.select(prompt, choices=list(options))
        else:
            question = questionary.text(prompt)
        reply = await question.ask_async()
        if reply is None:
            # 用户取消（Ctrl+C / ESC）：抛出让 quiz 命令捕获并优雅退出，不把 None 当空作答提交判卷。
            raise KeyboardInterrupt("用户取消作答")
        return str(reply)

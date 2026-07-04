"""答案输入原语——注入确定性作答，供测试脚本化 / 回放（照 approval.py 的可注入交互模式）。

# SKELETON: 交互式 CLI Responder（阻塞 prompt）见 docs/skeleton-ledger.md #6（后续 human 步骤）

考核循环里"学习者作答"是一次外部交互——像审批门一样，接口形状第一天就焊好、确定性假件
先顶上：``assess_once`` 只依赖 ``Responder`` 协议，把它换成真正的交互式 CLI（阻塞 prompt）或
可挂起 / 可恢复 turn 时，编排调用方不变。
"""

from typing import Protocol


class Responder(Protocol):
    """作答协议：给一道题，返回学习者的作答文本。"""

    def answer(self, question: str) -> str: ...


class ScriptedResponder:
    """确定性 responder——注入固定答案（``answer``）或按序消费的答案列表（``answers``）。

    ``answers`` 供多题场景按序作答（M3.3 逐题循环）；两者都给时 ``answers`` 优先。真实交互式
    作答是后续 human 步骤（见模块顶部骨架标记）；本类只提供协议的确定性实现供测试。
    """

    def __init__(self, *, answer: str | None = None, answers: list[str] | None = None) -> None:
        if answer is None and answers is None:
            raise ValueError("answer 与 answers 至少提供其一")
        self._answer = answer
        self._answers = list(answers) if answers is not None else None
        self._index = 0

    def answer(self, question: str) -> str:
        if self._answers is not None:
            if self._index >= len(self._answers):
                raise IndexError("脚本化答案已耗尽——注入的 answers 数少于提问次数")
            reply = self._answers[self._index]
            self._index += 1
            return reply
        if self._answer is not None:
            return self._answer
        raise AssertionError("__init__ 已保证 answer / answers 至少其一，不可达")

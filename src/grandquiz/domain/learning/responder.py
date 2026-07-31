"""答案输入原语——注入确定性作答，供测试脚本化 / 回放（照 approval.py 的可注入交互模式）。

# SKELETON: 可挂起/恢复的作答 turn 待补（凭 token 恢复）— docs/skeleton-ledger.md #6

考核循环里"学习者作答"是一次外部交互——像审批门一样，接口形状第一天就焊好，可换不同实现顶上：
确定性的 ``ScriptedResponder``（测试 / 回放）与交互式的 ``InteractiveResponder``
（``interfaces/cli``，questionary 逐题问）都满足本协议。``assess_once`` 只依赖 ``Responder`` 协议，
把它换成可挂起 / 可恢复的作答 turn（凭 token 恢复）时，编排调用方不变。

协议是 **async**：真实交互式作答（终端 / SSE）本质是 await 一次外部 I/O；``assess_once`` 已在
asyncio loop 内 ``await responder.answer(...)``，故协议第一天就按 async 定，避免日后为接真交互
再翻协议。``options`` 是选择题的候选项——非空时实现可渲染成单选（``InteractiveResponder`` 走
``questionary.select``），为 None 时是开放 / 追问的自由作答（走 ``questionary.text``）；
``ScriptedResponder`` 忽略 ``options``、恒返回注入答案。
"""

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class AnswerSubmissionMetadata(BaseModel):
    """Interface provenance captured with an answer, separate from answer text."""

    input_modality: Literal["text", "voice"] = "text"
    answer_format: Literal["choice", "natural_language", "code"]
    evidence_revealed_before_answer: bool = False


@runtime_checkable
class SubmissionMetadataProvider(Protocol):
    def last_submission_metadata(self) -> AnswerSubmissionMetadata: ...


class Responder(Protocol):
    """作答协议（async）：给一道题（选择题另给 ``options``），返回学习者的作答文本。"""

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str: ...


class ScriptedResponder:
    """确定性 responder——注入固定答案（``answer``）或按序消费的答案列表（``answers``）。

    ``answers`` 供多题场景按序作答（M3.3 逐题循环）；两者都给时 ``answers`` 优先。``answer`` 是
    async（满足 ``Responder`` 协议）但内部无 I/O、恒同步返回；**忽略 ``options``**——脚本化答案
    与题型无关（选择题 / 开放题都返回注入值）。真实交互式作答见 ``interfaces/cli`` 的
    ``InteractiveResponder``；本类只提供协议的确定性实现供测试 / 回放。
    """

    def __init__(self, *, answer: str | None = None, answers: list[str] | None = None) -> None:
        if answer is None and answers is None:
            raise ValueError("answer 与 answers 至少提供其一")
        self._answer = answer
        self._answers = list(answers) if answers is not None else None
        self._index = 0

    async def answer(self, prompt: str, *, options: Sequence[str] | None = None) -> str:
        if self._answers is not None:
            if self._index >= len(self._answers):
                raise IndexError("脚本化答案已耗尽——注入的 answers 数少于提问次数")
            reply = self._answers[self._index]
            self._index += 1
            return reply
        if self._answer is not None:
            return self._answer
        raise AssertionError("__init__ 已保证 answer / answers 至少其一，不可达")

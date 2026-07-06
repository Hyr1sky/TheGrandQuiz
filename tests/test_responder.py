"""ScriptedResponder（async）单元测试——await answer 返回注入值（忽略 options）；按序答案逐次返回。

协议 M3.6 改 async + 加 ``options`` 后，本类内部无 I/O、恒同步返回，且忽略 ``options``（脚本化答案
与题型无关）。这里锁住这两条契约，确保交互式实现（``InteractiveResponder``）与它可互换。
"""

import pytest

from grandquiz.domain.learning.responder import ScriptedResponder


async def test_fixed_answer_ignores_options() -> None:
    # 固定答案：无论 options 传选择题候选还是 None，都返回注入值。
    responder = ScriptedResponder(answer="固定作答")
    assert await responder.answer("选择题干", options=["选项A", "选项B"]) == "固定作答"
    assert await responder.answer("开放题干", options=None) == "固定作答"


async def test_sequential_answers_consumed_in_order() -> None:
    responder = ScriptedResponder(answers=["一", "二", "三"])
    assert await responder.answer("q1") == "一"
    assert await responder.answer("q2", options=["x"]) == "二"  # 忽略 options
    assert await responder.answer("q3") == "三"


async def test_answers_take_precedence_over_answer() -> None:
    responder = ScriptedResponder(answer="固定", answers=["序一"])
    assert await responder.answer("q") == "序一"


async def test_exhausted_answers_raise_index_error() -> None:
    responder = ScriptedResponder(answers=["only"])
    assert await responder.answer("q1") == "only"
    with pytest.raises(IndexError):
        await responder.answer("q2")


def test_requires_answer_or_answers() -> None:
    with pytest.raises(ValueError, match="至少提供其一"):
        ScriptedResponder()

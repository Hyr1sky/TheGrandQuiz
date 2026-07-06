"""InteractiveResponder 单元测试——monkeypatch 掉 questionary.select / text，不碰真实 tty。

只测本类的分支逻辑（options 非空走 select、为 None 走 text、取消 → KeyboardInterrupt）：把
``questionary.select`` / ``questionary.text`` 换成返回一个带 ``ask_async`` 的假 Question 对象，
断言路由 + 传参 + 返回值 + 取消语义。真机 tty 逐题作答留给 human。
"""

from collections.abc import Sequence
from typing import Any

import pytest

import grandquiz.interfaces.cli.interactive as interactive_mod
from grandquiz.interfaces.cli.interactive import InteractiveResponder


class _FakeQuestion:
    """假 questionary Question：``ask_async`` 返回注入的 reply（None 模拟用户取消）。"""

    def __init__(self, reply: str | None) -> None:
        self._reply = reply

    async def ask_async(self) -> Any:
        return self._reply


async def test_options_route_to_select(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_select(message: str, choices: Sequence[str]) -> _FakeQuestion:
        captured["message"] = message
        captured["choices"] = list(choices)
        return _FakeQuestion("选项B")

    monkeypatch.setattr(interactive_mod.questionary, "select", fake_select)
    reply = await InteractiveResponder().answer("选择题干", options=["选项A", "选项B"])

    assert reply == "选项B"
    assert captured["message"] == "选择题干"
    assert captured["choices"] == ["选项A", "选项B"]  # options 原样传给 select 的 choices


async def test_no_options_route_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_text(message: str) -> _FakeQuestion:
        captured["message"] = message
        return _FakeQuestion("我的开放作答")

    monkeypatch.setattr(interactive_mod.questionary, "text", fake_text)
    reply = await InteractiveResponder().answer("开放题干", options=None)

    assert reply == "我的开放作答"
    assert captured["message"] == "开放题干"


async def test_cancel_on_text_raises_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_text(message: str) -> _FakeQuestion:
        return _FakeQuestion(None)  # 用户 Ctrl+C / ESC

    monkeypatch.setattr(interactive_mod.questionary, "text", fake_text)
    with pytest.raises(KeyboardInterrupt):
        await InteractiveResponder().answer("题", options=None)


async def test_cancel_on_select_raises_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_select(message: str, choices: Sequence[str]) -> _FakeQuestion:
        return _FakeQuestion(None)

    monkeypatch.setattr(interactive_mod.questionary, "select", fake_select)
    with pytest.raises(KeyboardInterrupt):
        await InteractiveResponder().answer("题", options=["A", "B"])

"""导航工具的注册隔离 + 事件投影测试。"""

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.interfaces.api.navigation_tools import register_navigation_tools
from grandquiz.kernel.tools import ToolRegistry
from grandquiz.providers.base import Completion, Message, Role, ToolCall, ToolSpec, Usage

# ---- Fake providers ---- #


class _NavigationProvider:
    """第一次调用返回 start_assessment tool_call，第二次返回 final 文本。"""

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self._call_count += 1
        if tools is not None and self._call_count == 1:
            return Completion(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tc_nav_1",
                        name="start_assessment",
                        arguments={
                            "resource_id": "res-abc",
                            "rounds": 3,
                            "question_type": "选择题",
                        },
                    )
                ],
                usage=Usage(prompt_tokens=80, completion_tokens=20),
            )
        return Completion(
            text="好的，已为你启动考核。",
            usage=Usage(prompt_tokens=100, completion_tokens=15),
        )


class _OpenArticleProvider:
    """第一次调用返回 open_article tool_call，第二次返回 final 文本。"""

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self._call_count += 1
        if tools is not None and self._call_count == 1:
            return Completion(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tc_nav_2",
                        name="open_article",
                        arguments={"resource_id": "res-xyz"},
                    )
                ],
                usage=Usage(prompt_tokens=80, completion_tokens=20),
            )
        return Completion(
            text="已切换回文章阅读。",
            usage=Usage(prompt_tokens=100, completion_tokens=15),
        )


class _MixedAssessmentProvider:
    """复现实机请求：两道选择题后接一道简答题。"""

    def __init__(self) -> None:
        self._call_count = 0

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        self._call_count += 1
        if tools is not None and self._call_count == 1:
            return Completion(
                text="",
                tool_calls=[
                    ToolCall(
                        id="tc_nav_mixed",
                        name="start_assessment",
                        arguments={
                            "resource_id": "res-http",
                            "segments": [
                                {"count": 2, "question_type": "选择题"},
                                {"count": 1, "question_type": "简答题"},
                            ],
                        },
                    )
                ],
                usage=Usage(prompt_tokens=80, completion_tokens=20),
            )
        return Completion(
            text="已按顺序启动混合题型考核。",
            usage=Usage(prompt_tokens=100, completion_tokens=15),
        )


# ---- Helpers ---- #


def _app(tmp_path: Path, provider: object):
    return create_app(
        settings=ApiSettings(
            learning_db_path=tmp_path / "learning.db",
            trace_db_path=tmp_path / "trace.db",
        ),
        provider=provider,  # type: ignore[arg-type]
    )


def _wait_for_events(
    client: TestClient,
    session_id: str,
    *,
    terminal_type: str = "chat.turn_ended",
    after: int = 0,
    max_polls: int = 80,
) -> list[dict[str, Any]]:
    for _ in range(max_polls):
        response = client.get(
            f"/api/v1/chat/sessions/{session_id}/events",
            params={"after": after},
        )
        assert response.status_code == 200
        events: list[dict[str, Any]] = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        if any(e["type"] in {terminal_type, "chat.error"} for e in events):
            return events
        time.sleep(0.02)
    return []


# ---- Tests: Registration isolation ---- #


def test_navigation_tools_not_in_learning_tools() -> None:
    """导航工具名不在 register_learning_tools 注册的工具集中。"""
    nav_registry = ToolRegistry()
    register_navigation_tools(nav_registry)
    nav_names = {spec.name for spec in nav_registry.tool_specs()}
    assert "start_assessment" in nav_names
    assert "open_article" in nav_names

    # 真正的隔离断言：导航工具名 ∩ 已知 learning 工具名 = 空集
    # 不真正调用 register_learning_tools（需要太多依赖注入）；
    # 直接列出已知 learning 工具名做交集检查。
    known_learning_names = {
        "ingest_resource",
        "query_weak_concepts",
        "start_quiz",
        "grounded_answer",
        "search_nodes",
        "read_node",
        "web_search",
    }
    assert nav_names.isdisjoint(known_learning_names)


def test_cli_composition_does_not_contain_navigation_tools() -> None:
    """CLI composition（build_react_runner）不注册导航工具。

    CLI 只调用 register_learning_tools，不调用 register_navigation_tools。
    验证 CLI 的 composition.py 源码中不 import / 调用 register_navigation_tools。
    """
    import inspect

    from grandquiz.interfaces.cli import composition

    source = inspect.getsource(composition)
    assert "register_navigation_tools" not in source
    assert "navigation_tools" not in source


# ---- Tests: Navigation event projection ---- #


def test_start_assessment_projects_chat_navigation_event(tmp_path: Path) -> None:
    """start_assessment 工具调用通过 SSE 推送 chat.navigation 事件。"""
    with TestClient(_app(tmp_path, _NavigationProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "考我几道选择题"},
        )
        events = _wait_for_events(client, sid)

    types = [e["type"] for e in events]
    assert "chat.navigation" in types
    nav_event = next(e for e in events if e["type"] == "chat.navigation")
    data = nav_event["data"]
    assert isinstance(data, dict)
    assert data["target"] == "assessment"
    params = cast("dict[str, Any]", data["params"])
    assert isinstance(params, dict)
    assert params == {
        "resource_id": "res-abc",
        "question_type_plan": ["选择题", "选择题", "选择题"],
    }

    # Turn should also complete normally
    assert "chat.turn_ended" in types
    ended = next(e for e in events if e["type"] == "chat.turn_ended")
    assert isinstance(ended["data"], dict)
    assert ended["data"]["output"] == "好的，已为你启动考核。"


def test_start_assessment_projects_one_normalized_mixed_question_type_plan(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path, _MixedAssessmentProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "考我两道选择题和一道简答题"},
        )
        events = _wait_for_events(client, sid)

    nav_event = next(event for event in events if event["type"] == "chat.navigation")
    params = cast("dict[str, Any]", nav_event["data"]["params"])
    assert params == {
        "resource_id": "res-http",
        "question_type_plan": ["选择题", "选择题", "简答题"],
    }


def test_open_article_projects_chat_navigation_event(tmp_path: Path) -> None:
    """open_article 工具调用通过 SSE 推送 chat.navigation 事件（target=reading）。"""
    with TestClient(_app(tmp_path, _OpenArticleProvider())) as client:
        session = client.post("/api/v1/chat/sessions").json()
        sid = session["session_id"]
        client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            json={"text": "打开这篇文章"},
        )
        events = _wait_for_events(client, sid)

    types = [e["type"] for e in events]
    assert "chat.navigation" in types
    nav_event = next(e for e in events if e["type"] == "chat.navigation")
    data = nav_event["data"]
    assert isinstance(data, dict)
    assert data["target"] == "reading"
    params = cast("dict[str, Any]", data["params"])
    assert isinstance(params, dict)
    assert params["resource_id"] == "res-xyz"

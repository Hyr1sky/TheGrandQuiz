"""CLI 粘合 + Rich 呈现的可测部件——不碰真实 tty / 真实 LLM。

覆盖：
- ``QuizEventPrinter`` 按事件类型渲染（用 ``Console(record=True)`` 抓文本断言，无 tty）。
- ``run_ingest`` 读本地材料 → 假 Reader 深读 → 入 SQLite（provider 注入假件）。
- ``run_quiz`` 空库分支（提示先 ingest、不调 provider）与一轮 scripted 考核（薄弱落 SQLite +
  薄弱小结打印）。真机交互逐题（``grandquiz quiz`` 的 tty）留给 human。
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.interfaces.cli.app import run_ingest, run_quiz
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.kernel.events import AgentEvent
from grandquiz.providers.base import Completion, Message, Role, Usage
from grandquiz.providers.replay import ReplayMiss

_QUOTE = "闭包捕获变量而非值"
_MC_CORRECT = "正确选项"
_MC_WRONG = "干扰项"

_READER_JSON = json.dumps(
    {
        "topic": "闭包与作用域",
        "candidates": [
            {
                "concept": "闭包",
                "summary": "闭包捕获的是变量引用",
                "evidence": [{"quote": _QUOTE}],
                "confidence": 0.9,
            }
        ],
    },
    ensure_ascii=False,
)


class _ReaderProvider:
    """ingest 用假 provider：恒返回固定候选 JSON（供 run_ingest 的 Reader 槽）。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        return Completion(text=_READER_JSON, usage=Usage(prompt_tokens=7, completion_tokens=3))


class _McProvider:
    """quiz 用假 provider：enrich 出选择题（正确项恒在下标 0），basic 判卷（本测用不到）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        payload: dict[str, Any]
        if role == "enrich":
            payload = {
                "question": "闭包的核心是什么？",
                "options": [_MC_CORRECT, _MC_WRONG],
                "answer_index": 0,
                "cited_evidence": [_QUOTE],
            }
        else:
            payload = {"verdict": "错", "cited_evidence": [_QUOTE]}
        return Completion(
            text=json.dumps(payload, ensure_ascii=False),
            usage=Usage(prompt_tokens=7, completion_tokens=3),
        )


class _BrokenProvider:
    """恒返回非法输出：出题槽 3 次重试用尽 → QuestionError（触发 CLI 本轮跳过分支）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        self.calls += 1
        return Completion(text="这不是 JSON", usage=Usage(prompt_tokens=1, completion_tokens=1))


class _ReplayMissProvider:
    """出题槽即抛 ReplayMiss（FATAL）：模拟 cassette 缺录 / harness bug，必须冒泡不被跳过。"""

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        raise ReplayMiss("cassette 无此响应")


def _make_event(event_type: str, payload: dict[str, Any]) -> AgentEvent:
    return AgentEvent(type=event_type, seq=0, ts=0.0, trace_id="t", payload=payload)


# --- QuizEventPrinter 渲染 --------------------------------------------------


def test_question_asked_renders_panel_with_options() -> None:
    console = Console(record=True, width=80)
    QuizEventPrinter(console)(
        _make_event(
            LearningEvent.QUESTION_ASKED,
            {
                "item_id": "r#000",
                "question": "闭包是什么？",
                "cited_evidence": [_QUOTE],
                "question_type": "选择题",
                "options": ["A 选项内容", "B 选项内容"],
            },
        )
    )
    out = console.export_text()
    assert "闭包是什么" in out
    assert "选择题" in out  # 题型作 Panel 标题
    assert "A 选项内容" in out and "B 选项内容" in out


def test_answer_judged_renders_verdict_and_answer() -> None:
    console = Console(record=True, width=80)
    QuizEventPrinter(console)(
        _make_event(
            LearningEvent.ANSWER_JUDGED,
            {
                "item_id": "x",
                "verdict": "错",
                "weak_item_id": "x",
                "answer": "我的答案",
                "cited_evidence": [_QUOTE],
            },
        )
    )
    out = console.export_text()
    assert "错" in out and "我的答案" in out


def test_followup_renders_solution_panel() -> None:
    console = Console(record=True, width=80)
    QuizEventPrinter(console)(
        _make_event(
            LearningEvent.FOLLOWUP_GIVEN,
            {"item_id": "x", "correct_answer": "闭包：捕获变量引用（原文依据：...）"},
        )
    )
    out = console.export_text()
    assert "正解" in out and "闭包" in out


def test_concept_state_change_renders_transition() -> None:
    console = Console(record=True, width=80)
    QuizEventPrinter(console)(
        _make_event(
            LearningEvent.CONCEPT_STATE_CHANGED,
            {"item_id": "x", "from_state": None, "to_state": "薄弱", "consecutive_correct": 0},
        )
    )
    out = console.export_text()
    assert "薄弱" in out and "未追踪" in out  # from_state=None → 显示"未追踪"


def test_printer_escapes_markup_in_dynamic_text() -> None:
    # HIGH 修复：动态文本（作答/题干/选项/正解）含 markup 元字符（[/]、[bold]、未闭合的 [/red）
    # 不能让 Rich 抛 MarkupError——一律 escape（EventSink 现已隔离订阅者异常，转义仍是正确防御）。
    console = Console(record=True, width=80)
    printer = QuizEventPrinter(console)
    printer(
        _make_event(
            LearningEvent.QUESTION_ASKED,
            {
                "question": "闭包里的 [i] 标签是什么意思？",
                "question_type": "选择题",
                "options": ["[bold]选项 A[/]", "未闭合的 [/red"],
            },
        )
    )
    printer(
        _make_event(LearningEvent.ANSWER_JUDGED, {"verdict": "错", "answer": "我猜是 [green]绿[/]"})
    )
    printer(
        _make_event(
            LearningEvent.FOLLOWUP_GIVEN,
            {"item_id": "x", "correct_answer": "正解含原文 [see §2] 的方括号"},
        )
    )
    # 不抛 MarkupError 即算过；且字面文本原样呈现（escape 后不被当 markup 吞掉）。
    out = console.export_text()
    assert "[i]" in out
    assert "[bold]选项 A[/]" in out
    assert "[green]绿[/]" in out
    assert "[see §2]" in out


# --- run_ingest 粘合 --------------------------------------------------------


async def test_run_ingest_reads_material_and_persists_items(tmp_path: Path) -> None:
    material = tmp_path / "material.txt"
    material.write_text(f"{_QUOTE}，这是关于闭包的材料。", encoding="utf-8")
    db = tmp_path / "learning.db"
    console = Console(record=True, width=100)

    result = await run_ingest(
        title="React",
        material_path=material,
        db_path=db,
        provider=_ReaderProvider(),
        console=console,
    )

    assert result.status == "read"
    assert [item.concept for item in result.items] == ["闭包"]
    # 落到 SQLite：重开 store 仍在（跨会话持久，全局 KB 读）。
    store = SqliteLearningStore(db)
    assert [item.concept for item in store.all_items()] == ["闭包"]
    store.close()
    assert "闭包" in console.export_text()  # Rich 打印了抽出的知识点


# --- run_quiz 粘合 ----------------------------------------------------------


def _stock_sqlite(db: Path) -> str:
    """直接往 SQLite 塞一个 resource + 一个知识点（全局 KB），返回 item_id。"""
    store = SqliteLearningStore(db)
    resource = LearningResource.create(url="file://local/material.txt")
    store.add_resource(resource)
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        index=0,
        concept="闭包",
        summary="闭包捕获变量而非值",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )
    store.add_items([item])
    store.close()
    return item.item_id


async def test_run_quiz_empty_kb_prompts_ingest_without_calling_provider(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"
    console = Console(record=True, width=100)
    provider = _McProvider()

    await run_quiz(
        title="不存在的任务",
        rounds=3,
        db_path=db,
        provider=provider,
        responder=ScriptedResponder(answer="x"),
        console=console,
        seed=1,
    )

    out = console.export_text()
    assert "ingest" in out  # 引导先喂材料
    assert provider.calls == 0  # 空库不调任何 LLM


async def test_run_quiz_round_records_weak_and_prints_summary(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"
    item_id = _stock_sqlite(db)
    console = Console(record=True, width=100)

    # fresh memory → 选择题；选"干扰项"→ 判错 → 入薄弱。scripted responder 忽略 options、恒返回它。
    await run_quiz(
        title="React",
        rounds=1,
        db_path=db,
        provider=_McProvider(),
        responder=ScriptedResponder(answer=_MC_WRONG),
        console=console,
        seed=7,
    )

    # 薄弱账真的写进了持久 SQLite（跨会话留存）。
    memory = SqliteLearningMemory(db)
    assert memory.weak_item_ids() == {item_id}
    assert memory.state_of(item_id) == "薄弱"
    memory.close()
    # 薄弱小结列出了概念。
    out = console.export_text()
    assert "薄弱" in out and "闭包" in out


async def test_run_quiz_skips_round_on_question_error_without_crashing(tmp_path: Path) -> None:
    # 出题恒失败 → QuestionError；run_quiz 应跳过本轮、不抛异常炸掉会话，仍走到薄弱小结。
    # （dogfood 坑：QuestionError 原样冒泡会让整场 quiz 崩溃——CLI 边界兜底为"跳过本轮"。）
    db = tmp_path / "learning.db"
    _item_id = _stock_sqlite(db)
    console = Console(record=True, width=100)

    await run_quiz(
        title="React",
        rounds=1,
        db_path=db,
        provider=_BrokenProvider(),
        responder=ScriptedResponder(answer="x"),
        console=console,
        seed=3,
    )

    out = console.export_text()
    assert "本轮跳过" in out  # 打印了跳过提示、未抛异常
    # 本轮未判卷 → 无薄弱账；小结走"没有遗留薄弱点"分支。
    memory = SqliteLearningMemory(db)
    assert memory.weak_item_ids() == set()
    memory.close()


async def test_run_quiz_propagates_fatal_error_never_swallowed(tmp_path: Path) -> None:
    # ReplayMiss（FATAL）必冒泡出 run_quiz——RecoveryPolicy 裁 PROPAGATE，绝不静默"跳过本轮"
    # （决策 6：eval / replay 契约不可破）。mutation：若 CLI 无条件 continue 或 ReplayMiss 被误标
    # DEGRADED，异常会被吞、本测试红。
    db = tmp_path / "learning.db"
    _stock_sqlite(db)
    console = Console(record=True, width=100)

    with pytest.raises(ReplayMiss):
        await run_quiz(
            title="React",
            rounds=1,
            db_path=db,
            provider=_ReplayMissProvider(),
            responder=ScriptedResponder(answer="x"),
            console=console,
            seed=3,
        )

    assert "本轮跳过" not in console.export_text()  # 绝不降级为跳过

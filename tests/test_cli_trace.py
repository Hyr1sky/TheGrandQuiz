"""真机 CLI 落 trace（缝 1，事件 / trace 流）——脚本化假 provider 驱动 run_quiz / run_ingest。

覆盖 issue 02 的验收：真机 ``quiz`` / ``ingest`` 会话把 AgentEvent 流持久化到**独立 trace SQLite
库**（与 learning.db 分开的文件），每会话一个 ``trace_id``、会话结束打印它 + 库位置；断言
``TraceStore.events(trace_id)`` + ``build_span_tree`` 能从库里重建预期 span 森林。落 trace 纯经
"CLI 侧注册 TraceStore processor"实现——``assess_once`` / ``ingest_resource`` 签名逻辑一行不改。
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.domain.learning.models import (
    Evidence,
    KnowledgeItem,
    LearningResource,
)
from grandquiz.domain.learning.responder import ScriptedResponder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.interfaces.cli.app import run_ingest, run_quiz
from grandquiz.kernel.events import EventType
from grandquiz.kernel.trace import TraceStore, build_span_tree
from grandquiz.providers.base import Completion, Message, Role, Usage

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
    """quiz 用假 provider：enrich 出选择题（正确项恒在下标 0），basic 判卷（本测用不到）。

    每次 enrich 换一个题干（带自增序号）——绕开会话内"已问过"去重（同一 item 复考时逐字重复会被
    拒），让两轮都能顺利出题、各成一棵 assessment span。
    """

    def __init__(self) -> None:
        self._enrich_calls = 0

    async def complete(
        self, messages: Sequence[Message], *, role: Role = "basic", tools: object = None
    ) -> Completion:
        payload: dict[str, Any]
        if role == "enrich":
            self._enrich_calls += 1
            payload = {
                "question": f"闭包的核心是什么？（第 {self._enrich_calls} 问）",
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


def _sole_trace_id(trace_db_path: Path) -> str:
    """从独立 trace 库读出唯一的 trace_id（每会话一个 → 单库单 id）。"""
    conn = sqlite3.connect(str(trace_db_path))
    try:
        rows = conn.execute("SELECT DISTINCT trace_id FROM events").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"期望单一会话 trace_id，实得 {rows}"
    return str(rows[0][0])


def _stock_sqlite(db: Path) -> str:
    """往 learning 库塞一个 resource + 一个知识点（全局 KB），返回 item_id。"""
    store = SqliteLearningStore(db)
    resource = LearningResource.create(url="file://local/material.txt")
    store.add_resource(resource)
    item = KnowledgeItem.create(
        resource_id=resource.resource_id,
        concept="闭包",
        summary="闭包捕获变量而非值",
        evidence=[Evidence(quote=_QUOTE)],
        confidence=0.9,
    )
    store.add_items([item])
    store.close()
    return item.item_id


async def test_run_ingest_persists_trace_to_independent_db_and_prints_trace_id(
    tmp_path: Path,
) -> None:
    material = tmp_path / "material.txt"
    material.write_text(f"{_QUOTE}，这是关于闭包的材料。", encoding="utf-8")
    db = tmp_path / "learning.db"
    trace_db = tmp_path / "trace.db"
    console = Console(record=True, width=100)

    await run_ingest(
        title="React",
        material_path=material,
        db_path=db,
        provider=_ReaderProvider(),
        console=console,
        trace_db_path=trace_db,
    )

    # trace 落在独立库（与 learning.db 分开的文件）。
    assert trace_db.exists() and trace_db != db
    trace_id = _sole_trace_id(trace_db)

    store = TraceStore(trace_db)
    try:
        events = store.events(trace_id)
        types = [e.type for e in events]
        # ingest 竖切的事件流被持久化：以 ingest 开合、含 Reader 的 model span、含知识点入库。
        assert types[0] == "ingest.started"
        assert types[-1] == "ingest.ended"
        assert EventType.MODEL_STARTED in types and EventType.MODEL_ENDED in types
        assert LearningEvent.ITEM_CREATED in types
        # build_span_tree 从库里重建 span 森林：ingest 为根，Reader 的 model span 挂其下。
        roots = build_span_tree(events)
        assert len(roots) == 1
        assert roots[0].type == "ingest"
        assert [c.type for c in roots[0].children] == ["model"]
        assert store.span_tree(trace_id) == roots
    finally:
        store.close()

    # 会话结束打印了 trace_id（便于随手 `grandquiz trace <id>` 复盘）。
    assert trace_id in console.export_text()


async def test_run_quiz_persists_session_trace_forest_and_prints_trace_id(tmp_path: Path) -> None:
    db = tmp_path / "learning.db"
    trace_db = tmp_path / "trace.db"
    _stock_sqlite(db)
    console = Console(record=True, width=100)

    # 答对 → item 不入记忆（未追踪 + 对 → 不追踪），两轮都路由到选择题、确定性判卷。
    # 两轮共享一个会话 trace_id + 一个 EventEmitter，故落库后是一条 trace、两棵 assessment 根。
    await run_quiz(
        title="React",
        rounds=2,
        db_path=db,
        provider=_McProvider(),
        responder=ScriptedResponder(answer=_MC_CORRECT),
        console=console,
        seed=7,
        trace_db_path=trace_db,
    )

    assert trace_db.exists() and trace_db != db
    trace_id = _sole_trace_id(trace_db)  # 每会话一个 trace_id（跨轮唯一）

    store = TraceStore(trace_db)
    try:
        events = store.events(trace_id)
        types = [e.type for e in events]
        assert types.count("assessment.started") == 2
        assert types.count("assessment.ended") == 2
        assert LearningEvent.QUESTION_ASKED in types
        assert LearningEvent.ANSWER_JUDGED in types
        # 单会话 trace 的 span 森林：每轮一棵 assessment 根，各挂一个出题 model span。
        roots = build_span_tree(events)
        assert [r.type for r in roots] == ["assessment", "assessment"]
        for root in roots:
            assert [c.type for c in root.children] == ["model"]
    finally:
        store.close()

    assert trace_id in console.export_text()


async def test_default_trace_db_is_separate_file_not_learning_db(tmp_path: Path) -> None:
    # 不传 trace_db_path → 走默认派生。断言 trace 落在与 learning.db 分开的默认 trace.db、
    # 而非灌进 learning.db（杀"默认路径塌回 learning.db"的 mutation；此前生产路径无测）。
    material = tmp_path / "material.txt"
    material.write_text(f"{_QUOTE}，这是关于闭包的材料。", encoding="utf-8")
    db = tmp_path / "learning.db"
    console = Console(record=True, width=100)

    await run_ingest(
        title="React",
        material_path=material,
        db_path=db,
        provider=_ReaderProvider(),
        console=console,
        # 关键：不传 trace_db_path，走默认派生（db.parent/trace.db）
    )

    default_trace_db = db.parent / "trace.db"
    assert default_trace_db.exists() and default_trace_db != db
    # trace 落在独立默认库、含 ingest 事件流
    store = TraceStore(default_trace_db)
    try:
        events = store.events(_sole_trace_id(default_trace_db))
        assert next(e.type for e in events) == "ingest.started"
    finally:
        store.close()
    # learning.db 不含 events 表——trace 没灌进 learning 库
    conn = sqlite3.connect(str(db))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "events" not in tables, f"trace 不应灌进 learning.db；learning 库表：{tables}"

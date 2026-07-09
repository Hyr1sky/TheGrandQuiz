"""CLI 导出子命令（report / trace → 自包含 HTML）——缝 1 / 端到端。

覆盖 issue 04 的验收：``grandquiz report`` 跑 eval harness → 导出索引页（逐用例 pass/fail +
token + prompt 版本，行链到详情）+ 每用例一份 ``render_trace_html`` 详情（span 树 + 事件流）；
``grandquiz trace <id>`` 从独立 trace 库按 trace_id 读出会话 → 导出同款自包含 HTML，读不到 id
则大声报错。两命令共用 issue 03 的 ``render_trace_html``（不另写渲染逻辑），产出一律自包含
（无 ``<script`` / ``<link`` / ``src=`` / ``@import`` / ``url(http`` 等加载外部资源构造）。
"""

import re
from pathlib import Path

import pytest

from grandquiz.evals.harness import export_html_report, load_cases, run_all
from grandquiz.interfaces.cli.app import export_trace_html
from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.kernel.trace import TraceStore


def _assert_self_contained(document: str) -> None:
    """自包含 = 零"加载外部资源"构造（内联 CSS 在场、无 JS / 外链 / 外部 url()）。"""
    assert "<script" not in document  # 折叠交互用原生 <details>，零 JS
    assert "<link" not in document  # 无外部样式表
    assert "@import" not in document  # CSS 无外部 import
    assert "<style" in document  # 内联样式在场
    # 无"加载外部资源"：url(...) 与 src/href 不得指向 http(s):// 或协议相对 //（含引号形式）；
    # 相对链接（如 href="case1.html"）不含 // 故放行。用正则、而非脆弱的裸子串匹配。
    assert not re.search(r'url\(\s*["\']?\s*(?:https?:)?//', document), "CSS url() 指向了外部资源"
    assert not re.search(r'(?:src|href)\s*=\s*["\']?\s*(?:https?:)?//', document), (
        "src/href 指向了外部资源"
    )


async def test_report_export_index_and_per_case_details(tmp_path: Path) -> None:
    out_dir = tmp_path / "eval-report"

    index_path = await export_html_report(out_dir)

    assert index_path == out_dir / "index.html"
    index = index_path.read_text(encoding="utf-8")
    # 索引页自包含
    _assert_self_contained(index)

    # 汇总表结构：pass/fail 列 + token 列 + prompt 版本列
    assert "PASS" in index
    assert "tokens" in index and "prompts" in index
    assert "@" in index  # prompt 版本形如 name@digest（如 reader_extract@…）

    # 13 个用例：每个都在索引出现、行链到 <case_id>.html、详情文件存在且含 span 树内容
    case_ids = [c.id for c in load_cases()]
    assert len(case_ids) == 13
    for case_id in case_ids:
        assert f'href="{case_id}.html"' in index  # 索引链到该用例详情
        detail_path = out_dir / f"{case_id}.html"
        assert detail_path.exists()
        detail = detail_path.read_text(encoding="utf-8")
        _assert_self_contained(detail)
        # 每用例详情复用 render_trace_html：含可折叠 span 树（span 森林独有结构）
        assert '<details class="span"' in detail

    # 每用例详情渲染的是**该用例自己**的 trace（防"所有详情都渲染同一个用例"的回归）：
    # ingest 用例含 ingest 事件、assess 用例含 assessment 事件、互不串。
    case1_detail = (out_dir / "case1.html").read_text(encoding="utf-8")  # ingest 竖切
    case2_detail = (out_dir / "case2.html").read_text(encoding="utf-8")  # assess（空库拒答）
    assert "ingest.started" in case1_detail
    assert "assessment" in case2_detail and "ingest.started" not in case2_detail

    # token 成本列按**值**断言（非仅表头）：取 token 数最大的用例，断其值出现在索引单元里
    # （把 token 单元改成常量的 mutation 会让本断言红）。
    reports = await run_all()
    top = max(reports, key=lambda r: r.total_tokens)
    assert top.total_tokens > 0
    assert f"<td>{top.total_tokens}</td>" in index


def _write_synthetic_trace(trace_db_path: Path, trace_id: str) -> None:
    """直接经 TraceStore.record 落一条合成 trace：turn → model span + 一条领域判卷事件。"""
    events = [
        AgentEvent(
            type=EventType.TURN_STARTED,
            seq=0,
            ts=0.0,
            trace_id=trace_id,
            span_id="s0",
            parent_span_id=None,
            payload={"user_message": "hi"},
        ),
        AgentEvent(
            type=EventType.MODEL_STARTED,
            seq=1,
            ts=1.0,
            trace_id=trace_id,
            span_id="s1",
            parent_span_id="s0",
            payload={"prompt_version": "assess@abc123", "role": "enrich"},
        ),
        AgentEvent(
            type=EventType.MODEL_ENDED,
            seq=2,
            ts=2.0,
            trace_id=trace_id,
            span_id="s1",
            parent_span_id="s0",
            payload={"usage": {"total_tokens": 7}},
        ),
        AgentEvent(
            type="learning.verdict_recorded",
            seq=3,
            ts=2.5,
            trace_id=trace_id,
            span_id="s1",
            parent_span_id="s0",
            payload={"verdict": "对"},
        ),
        AgentEvent(
            type=EventType.TURN_ENDED,
            seq=4,
            ts=3.0,
            trace_id=trace_id,
            span_id="s0",
            parent_span_id=None,
            payload={"ok": True},
        ),
    ]
    store = TraceStore(trace_db_path)
    try:
        for event in events:
            store.record(event)
    finally:
        store.close()


async def test_trace_export_produces_self_contained_html(tmp_path: Path) -> None:
    trace_db = tmp_path / "trace.db"
    trace_id = "sess-1"
    _write_synthetic_trace(trace_db, trace_id)
    out = tmp_path / "trace-out.html"

    result = export_trace_html(trace_id, trace_db_path=trace_db, out_path=out)

    assert result == out and out.exists()
    document = out.read_text(encoding="utf-8")
    _assert_self_contained(document)
    # 该会话的 span 森林（turn → model）+ 底层事件流在场
    assert '<details class="span"' in document
    assert "turn" in document and "model" in document
    assert '<table class="events"' in document
    assert "learning.verdict_recorded" in document  # 领域事件进事件流
    assert trace_id in document  # meta 里带会话 trace_id


async def test_trace_export_default_out_path_beside_trace_db(tmp_path: Path) -> None:
    trace_db = tmp_path / "trace.db"
    trace_id = "sess-2"
    _write_synthetic_trace(trace_db, trace_id)

    # 不传 out_path → 默认落在 trace 库同目录的 trace-<id>.html
    result = export_trace_html(trace_id, trace_db_path=trace_db)

    assert result == trace_db.parent / f"trace-{trace_id}.html"
    assert result.exists()


async def test_trace_export_missing_id_fails_loudly(tmp_path: Path) -> None:
    trace_db = tmp_path / "trace.db"
    _write_synthetic_trace(trace_db, "sess-present")
    out = tmp_path / "should-not-exist.html"

    # 读不到该 trace_id → 大声报错（ValueError，含 trace_id），绝不静默产出空报告
    with pytest.raises(ValueError, match="missing-id"):
        export_trace_html("missing-id", trace_db_path=trace_db, out_path=out)
    assert not out.exists()  # 未静默写出空报告

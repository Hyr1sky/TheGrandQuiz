"""HTML 渲染器测试（缝 2——纯函数缝）。

喂固定的（有序 AgentEvent 列表 + build_span_tree 森林 + 汇总元数据），断言产出 HTML 的
**结构内容**存在（span 类型、token 总数、事件条数、判决值、prompt 版本），断言**自包含**
（无外链 / 无外部 script/link），断言**动态文本被 escape**（含 < > & 的不可信文本不原样注入）。
非字节级比对。
"""

from collections.abc import Mapping
from typing import Any

from grandquiz.kernel.events import AgentEvent, EventType
from grandquiz.kernel.report import render_trace_html
from grandquiz.kernel.trace import build_span_tree


def _event(
    type_: str,
    seq: int,
    ts: float,
    *,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> AgentEvent:
    return AgentEvent(
        type=type_,
        seq=seq,
        ts=ts,
        trace_id="run",
        span_id=span_id,
        parent_span_id=parent_span_id,
        payload=payload if payload is not None else {},
    )


def _fixture() -> tuple[list[AgentEvent], list[Any], dict[str, Any]]:
    """一条合成 trace：turn → model → tool，含一条领域判卷事件（payload 带不可信文本）。"""
    events = [
        _event(EventType.TURN_STARTED, 0, 0.0, span_id="s0", payload={"user_message": "hi"}),
        _event(
            EventType.MODEL_STARTED,
            1,
            1.0,
            span_id="s1",
            parent_span_id="s0",
            payload={"prompt_version": "assess@abc123", "role": "enrich"},
        ),
        _event(
            EventType.MODEL_ENDED,
            2,
            2.0,
            span_id="s1",
            parent_span_id="s0",
            payload={"usage": {"total_tokens": 7}, "ok": True},
        ),
        _event(
            "tool.started",
            3,
            2.5,
            span_id="s2",
            parent_span_id="s0",
            # 真机 trace 的 payload 常带 URL（如资源来源）——应渲染成惰性转义文本、不构成外部请求
            payload={"name": "grade", "source_url": "https://github.com/x/y"},
        ),
        _event(
            "learning.verdict_recorded",
            4,
            3.0,
            span_id="s2",
            parent_span_id="s0",
            # 不可信文本：LLM 输出 / 引文含 HTML 元字符与 Rich markup 残留
            payload={"verdict": "对", "quote": "闭包捕获变量 <b>而非值</b> & [/red]"},
        ),
        _event("tool.ended", 5, 3.5, span_id="s2", parent_span_id="s0", payload={"ok": True}),
        _event(EventType.TURN_ENDED, 6, 4.0, span_id="s0", payload={"ok": True}),
    ]
    spans = build_span_tree(events)
    meta: dict[str, Any] = {
        "trace_id": "run",
        "total_tokens": 7,
        "total_latency_s": 4.0,
        "event_count": len(events),
    }
    return events, spans, meta


def test_renders_span_types_and_event_stream() -> None:
    events, spans, meta = _fixture()

    html = render_trace_html(events, spans, meta=meta)

    # span 森林里各层 type 都出现
    assert "turn" in html
    assert "model" in html
    assert "tool" in html
    # 底层事件流：领域事件 type 出现
    assert "learning.verdict_recorded" in html
    # 汇总元数据：token 总数、事件条数
    assert "7" in html
    assert str(len(events)) in html
    # 判决值 + prompt 版本可读
    assert "assess@abc123" in html
    assert "对" in html


def test_is_self_contained_even_with_url_in_payload() -> None:
    events, spans, meta = _fixture()  # fixture payload 里含一个 https URL（真机 trace 常有）

    html = render_trace_html(events, spans, meta=meta)

    # 自包含 = 零"加载外部资源"构造（非"不含 URL 文本"）——payload 的 URL 是惰性转义文本。
    assert "<script" not in html  # 零 JS / script 标签（折叠用原生 <details>）
    assert "<link" not in html  # 无外部样式表
    assert " src=" not in html  # 无 img/iframe/script 等外部资源引用
    assert "@import" not in html  # CSS 无外部 import
    assert "url(http" not in html  # CSS 无外部 url()
    assert "<style" in html  # 内联样式在场
    # payload 的 URL 仅作惰性转义文本在场、不触发上面的"加载"构造（不误伤真机 trace）
    assert "github.com/x/y" in html


def test_escapes_untrusted_dynamic_text() -> None:
    events, spans, meta = _fixture()

    html = render_trace_html(events, spans, meta=meta)

    # 含 < > & 的不可信文本被转义、不原样注入成 HTML 标签
    assert "<b>而非值</b>" not in html
    assert "&lt;b&gt;而非值&lt;/b&gt;" in html
    assert "&amp;" in html


def test_pure_function_same_input_same_html() -> None:
    events, spans, meta = _fixture()

    first = render_trace_html(events, spans, meta=meta)
    second = render_trace_html(events, spans, meta=meta)

    assert first == second


def test_span_forest_is_rendered_with_collapsible_badges() -> None:
    # HIGH 回归：span 森林是查看器核心，其 type 不能只靠事件流的 type 蒙混——断言 span-独有的结构：
    # 可折叠 <details class="span"> + 每 span latency/token 徽章（值 span 独有、异于 meta）。
    events, spans, meta = _fixture()

    html = render_trace_html(events, spans, meta=meta)

    # 可折叠 span 结构：事件流是 <table>，只有 span 森林用 <details class="span"> + <summary>
    assert '<details class="span"' in html
    assert "<summary" in html
    # latency 徽章：span 的 1.0s → "1.000s"（meta total_latency_s=4.0 只渲染成裸 "4.0"）
    assert "1.000s" in html
    # 每 span 的 token 徽章："7 tok"（meta 的 total_tokens=7 渲染成裸 "7"、非 "7 tok"）
    assert "7 tok" in html


def test_summary_meta_block_is_rendered() -> None:
    # LOW 回归：汇总 meta 块（trace_id / token 总数 / event_count）独立呈现，不靠 span/事件流蒙混。
    events, spans, meta = _fixture()

    html = render_trace_html(events, spans, meta=meta)

    assert '<div class="meta">' in html
    assert "event_count" in html  # meta 键名只在 meta 块出现（span/事件流都没有）


def test_meta_optional() -> None:
    events, spans, _meta = _fixture()

    # 不喂 meta 也能渲染（真机 / eval 都可能只有事件 + span）
    html = render_trace_html(events, spans)

    assert "<html" in html
    assert "turn" in html

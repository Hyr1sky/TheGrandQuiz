"""HTML 渲染器——把一条 trace 渲染成自包含 HTML（"show, don't tell" 的载体）。

输入 = 一条 trace 的（有序 ``AgentEvent`` 列表 + ``build_span_tree`` 投影的 span 森林 +
汇总 token/latency 元数据），输出 = **自包含 HTML 字符串**。渲染两块：可折叠的 span 森林
（turn → model → tool → subagent，每 span 显 type / 起止 / latency / token）+ 底层
``AgentEvent`` 事件流（按 ``seq`` 有序、含领域事件），体现"脊柱是唯一真相、树只是投影"。

设计约束（见 CLAUDE.md / 本 feature PRD）：

- **纯函数**：同一 trace 数据 → 同一 HTML，不碰时钟 / 随机 / 网络；时序来自事件的 ``seq`` / ``ts``。
- **自包含**：内联全部 CSS，折叠交互用原生 ``<details>``（零 JS / 零外部请求 / CDN），可离线打开。
- **kernel 领域无关**：只 import ``AgentEvent`` + ``Span``，从不 import ``domain/``；不查看 payload
  的领域语义，只把 ``type`` + payload 键值原样（转义后）呈现。
- **动态文本一律转义**：LLM 输出 / 用户作答 / 引文 / 领域 payload 是不可信输入——经 ``html.escape``
  转义后注入，绝不原样拼接（呼应当初 Rich markup 注入的教训）。
- **token 复用 ``Span.tokens``**（其底层是 ``Usage.total_tokens`` computed_field），真实可读。
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from grandquiz.kernel.events import AgentEvent
from grandquiz.kernel.trace import Span

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.5rem;
  font: 14px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  background: #fafafa; color: #1b1b1b;
}
h1 { font-size: 1.2rem; margin: 0 0 0.75rem; }
h2 {
  font-size: 1rem; margin: 1.5rem 0 0.5rem;
  border-bottom: 1px solid #ccc; padding: 0 0 0.25rem;
}
.meta { display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem; margin-bottom: 0.5rem; }
.meta .kv { white-space: nowrap; }
.meta .k { color: #666; }
.meta .v { font-weight: 600; }
details.span {
  border-left: 2px solid #d0d0d0; margin: 0.25rem 0 0.25rem 0.5rem; padding: 0 0 0 0.5rem;
}
details.span > summary { cursor: pointer; list-style: none; padding: 0.15rem 0; }
details.span > summary::-webkit-details-marker { display: none; }
details.span > summary::before { content: "\\25B8 "; color: #888; }
details.span[open] > summary::before { content: "\\25BE "; }
.type { font-weight: 700; }
.type.err { color: #b00; }
.badge { color: #555; margin-left: 0.5rem; font-size: 0.85em; }
.badge .k { color: #999; }
.err-line { color: #b00; margin: 0.15rem 0 0.15rem 1rem; }
table.events { border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }
table.events th, table.events td {
  text-align: left; padding: 0.25rem 0.6rem; border-bottom: 1px solid #e2e2e2; vertical-align: top;
}
table.events th { color: #666; font-weight: 600; }
table.events td.seq { text-align: right; color: #999; }
.payload { color: #333; word-break: break-word; white-space: pre-wrap; }
@media (prefers-color-scheme: dark) {
  body { background: #16181d; color: #d6d6d6; }
  h2 { border-color: #333; }
  details.span { border-color: #333; }
  .meta .k, .badge .k { color: #7a7a7a; }
  table.events th, table.events td { border-color: #262a31; }
  .payload { color: #b8b8b8; }
}
"""


def _e(value: object) -> str:
    """转义任意值为安全 HTML 文本（不可信输入统一走这里）。"""
    return html.escape(str(value), quote=True)


def _fmt_latency(seconds: float | None) -> str:
    return "—" if seconds is None else f"{seconds:.3f}s"


def _fmt_tokens(tokens: int | None) -> str:
    return "—" if tokens is None else f"{tokens} tok"


def _payload_summary(payload: Mapping[str, Any]) -> str:
    """把 payload 折成紧凑 JSON 摘要（含中文，键序稳定）——纯呈现，不解释领域语义。"""
    if not payload:
        return ""
    # default=str：payload 本是 JSON-able，越界值（datetime/set）降级为 str、不炸报告。
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str)


def _render_span(span: Span) -> str:
    """递归渲染一个 span 为可折叠 ``<details>``（默认展开），显 type / 起止 / latency / token。"""
    type_cls = "type err" if span.error is not None else "type"
    badge = (
        f'<span class="badge"><span class="k">start</span> {_e(f"{span.start_ts:.3f}")}'
        f' <span class="k">end</span> {_e("—" if span.end_ts is None else f"{span.end_ts:.3f}")}'
        f' <span class="k">latency</span> {_e(_fmt_latency(span.latency))}'
        f' <span class="k">tokens</span> {_e(_fmt_tokens(span.tokens))}</span>'
    )
    parts = [
        '<details class="span" open>',
        f'<summary><span class="{type_cls}">{_e(span.type)}</span>{badge}</summary>',
    ]
    if span.error is not None:
        parts.append(f'<div class="err-line">error: {_e(_payload_summary(span.error))}</div>')
    for child in span.children:
        parts.append(_render_span(child))
    parts.append("</details>")
    return "".join(parts)


def _render_meta(meta: Mapping[str, Any]) -> str:
    if not meta:
        return ""
    kvs = "".join(
        f'<span class="kv"><span class="k">{_e(k)}</span> <span class="v">{_e(v)}</span></span>'
        for k, v in sorted(meta.items())  # 键序规范化 → 同一 trace 数据恒得同一 HTML
    )
    return f'<div class="meta">{kvs}</div>'


def _render_events(events: Sequence[AgentEvent]) -> str:
    # 在渲染器内按 seq 排序，把"事件流按 seq"这一承诺**强制**住（不只依赖调用方预排序）。
    rows = [
        "<tr>"
        f'<td class="seq">{_e(event.seq)}</td>'
        f'<td class="type">{_e(event.type)}</td>'
        f'<td class="payload">{_e(_payload_summary(event.payload))}</td>'
        "</tr>"
        for event in sorted(events, key=lambda e: e.seq)
    ]
    return (
        '<table class="events"><thead><tr>'
        "<th>seq</th><th>type</th><th>payload</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_trace_html(
    events: Sequence[AgentEvent],
    spans: Iterable[Span],
    *,
    meta: Mapping[str, Any] | None = None,
    title: str = "Trace",
) -> str:
    """把一条 trace（事件流 + span 森林 + 汇总元数据）渲染成自包含 HTML 字符串。

    纯函数：不碰时钟 / 随机 / 网络；同一输入恒得同一输出。``events`` 由渲染器内部按 ``seq`` 排序后
    呈现（不依赖调用方预排序）；``spans`` 是 ``build_span_tree`` 的森林；``meta`` 是可选的汇总键值
    （如 ``total_tokens`` / ``event_count``），按键序规范化后原样（转义后）呈现。
    """
    span_html = "".join(_render_span(span) for span in spans)
    if not span_html:
        span_html = '<div class="payload">（无 span）</div>'
    meta_html = _render_meta(meta or {})
    body = (
        f"<h1>{_e(title)}</h1>"
        f"{meta_html}"
        "<h2>Span 森林</h2>"
        f"{span_html}"
        "<h2>事件流（按 seq）</h2>"
        f"{_render_events(events)}"
    )
    return (
        "<!doctype html>"
        '<html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title>"
        f"<style>{_CSS}</style>"
        f"</head><body>{body}</body></html>"
    )

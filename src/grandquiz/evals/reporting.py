"""Text and self-contained HTML reporting for Eval suite results."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from grandquiz.evals.result import CaseReport
from grandquiz.kernel.report import render_trace_html


def render_report(reports: list[CaseReport]) -> str:
    """把报告渲染成人读文本表：双 Tier verdict、分列成本与失败明细。"""
    lines = [
        f"Eval 报告：{sum(r.passed for r in reports)}/{len(reports)} 通过",
        "-" * 88,
        f"{'case':<8}{'kind':<8}{'all':<6}{'rule':<6}{'quality':<9}{'exec':<8}{'judge':<8}rubric",
    ]
    for r in reports:
        mark = "PASS" if r.passed else "FAIL"
        rule = "PASS" if r.rule_passed else "FAIL"
        quality = "N/A" if r.quality_passed is None else "PASS" if r.quality_passed else "FAIL"
        rubric = r.quality_rubric_id or "-"
        lines.append(
            f"{r.case_id:<8}{r.kind:<8}{mark:<6}{rule:<6}{quality:<9}"
            f"{r.execution_tokens:<8}{r.judge_tokens:<8}{rubric}"
        )
        for failure in r.failures:
            lines.append(f"    ✗ {failure}")
    return "\n".join(lines)


# --- HTML 导出（附加：不改 run_case / run_all 的 pass/fail，也不改文本 render_report）-----------
#
# 复用 issue 03 的 kernel.report.render_trace_html 渲染每用例详情——一个 eval 用例本身就是一条
# trace。索引页是本报告独有的跨用例汇总表（render_trace_html 只渲染单条 trace，不提供汇总），故
# 在此另建一个小内联页；per-case 详情一律复用 render_trace_html，绝不重实现 trace 渲染。
#
# v1 静态增强（跨用例排序/筛选 + 汇总条）：仍是零依赖纯前端——排序/筛选用一段内联原生 JS
# （``_REPORT_INDEX_JS``），不加构建步骤、不引 CDN、不装 JS 框架；唯一自包含边界的变化是索引页现在
# 含一个**内联**（非外链）``<script>``，测试的自包含断言相应改为"禁止外部脚本/样式表"而非"零 JS"
# （见 ``tests/test_cli_report.py::_assert_self_contained``）。per-case 详情页（render_trace_html）
# 不受影响，仍是纯 ``<details>``、零 JS。

_REPORT_INDEX_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.5rem;
  font: 14px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  background: #fafafa; color: #1b1b1b;
}
h1 { font-size: 1.2rem; margin: 0 0 0.75rem; }
.summary { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 0 0 1rem; }
.summary .stat {
  border: 1px solid #e2e2e2; border-radius: 6px; padding: 0.35rem 0.9rem; min-width: 5rem;
}
.summary .stat .n { display: block; font-size: 1.15rem; font-weight: 700; }
.summary .stat .l { color: #666; font-size: 0.78em; }
.summary .stat.ok .n { color: #197f19; }
.summary .stat.bad .n { color: #b00; }
.controls { margin: 0 0 0.75rem; }
.controls input[type="search"] {
  font: inherit; padding: 0.3rem 0.6rem; width: 100%; max-width: 22rem;
  border: 1px solid #ccc; border-radius: 4px; background: inherit; color: inherit;
}
.controls select {
  font: inherit; padding: 0.3rem 0.6rem; margin-left: 0.5rem;
  border: 1px solid #ccc; border-radius: 4px; background: inherit; color: inherit;
}
table.cases { border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }
table.cases th, table.cases td {
  text-align: left; padding: 0.3rem 0.7rem; border-bottom: 1px solid #e2e2e2; vertical-align: top;
}
table.cases th { color: #666; font-weight: 600; }
table.cases th[data-sort-key] { cursor: pointer; user-select: none; }
table.cases th[data-sort-key]:hover { color: #1b1b1b; }
table.cases th.sort-asc::after { content: "\\2009\\25B4"; }
table.cases th.sort-desc::after { content: "\\2009\\25BE"; }
td.pass { color: #197f19; font-weight: 700; }
td.fail { color: #b00; font-weight: 700; }
tr.fail-detail td { color: #b00; }
tr.case-row.hidden, tr.fail-detail.hidden { display: none; }
a { color: inherit; }
@media (prefers-color-scheme: dark) {
  body { background: #16181d; color: #d6d6d6; }
  .summary .stat { border-color: #262a31; }
  .summary .stat .l { color: #9a9a9a; }
  .summary .stat.ok .n { color: #5fbf5f; }
  .summary .stat.bad .n { color: #ff6b6b; }
  .controls input[type="search"] { border-color: #3a3f47; }
  .controls select { border-color: #3a3f47; }
  table.cases th, table.cases td { border-color: #262a31; }
  table.cases th[data-sort-key]:hover { color: #eee; }
  td.pass { color: #5fbf5f; }
  td.fail, tr.fail-detail td { color: #ff6b6b; }
}
"""

# 索引页排序/筛选交互：纯内联 vanilla JS（无框架、无构建步骤、无外部脚本）。失败明细行
# （``tr.fail-detail``）没有独立排序键，靠与其所属用例行相同的 ``data-id`` 分组——排序 /
# 筛选按"组"（用例行 + 其后紧跟的失败明细行）整体移动，绝不拆散一条用例的失败说明。
_REPORT_INDEX_JS = """
(function () {
  var table = document.querySelector("table.cases");
  if (!table) return;
  var tbody = table.tBodies[0];
  var headers = Array.prototype.slice.call(table.querySelectorAll("th[data-sort-key]"));
  var filterInput = document.getElementById("case-filter");
  var statusFilter = document.getElementById("status-filter");
  var state = { key: null, dir: 1 };

  function rowGroups() {
    var rows = Array.prototype.slice.call(tbody.rows);
    var groups = [];
    var current = null;
    rows.forEach(function (row) {
      if (row.classList.contains("case-row")) {
        current = { key: row, rows: [row] };
        groups.push(current);
      } else if (current) {
        current.rows.push(row);
      }
    });
    return groups;
  }

  function sortValue(row, key) {
    if (key === "tokens") return parseInt(row.dataset.tokens, 10) || 0;
    if (key === "pass") return row.dataset.pass === "1" ? 1 : 0;
    return (row.dataset[key] || "").toLowerCase();
  }

  function applySort(key) {
    var dir = state.key === key ? -state.dir : 1;
    state = { key: key, dir: dir };
    var groups = rowGroups();
    groups.sort(function (a, b) {
      var va = sortValue(a.key, key);
      var vb = sortValue(b.key, key);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
    groups.forEach(function (g) {
      g.rows.forEach(function (r) { tbody.appendChild(r); });
    });
    headers.forEach(function (h) { h.classList.remove("sort-asc", "sort-desc"); });
    var active = table.querySelector('th[data-sort-key="' + key + '"]');
    if (active) active.classList.add(dir === 1 ? "sort-asc" : "sort-desc");
  }

  headers.forEach(function (h) {
    h.addEventListener("click", function () { applySort(h.dataset.sortKey); });
  });

  function applyFilters() {
    var q = filterInput ? filterInput.value.trim().toLowerCase() : "";
    var status = statusFilter ? statusFilter.value : "all";
    rowGroups().forEach(function (g) {
      var haystack = (g.key.dataset.id + " " + g.key.dataset.kind).toLowerCase();
      var textMatch = q === "" || haystack.indexOf(q) !== -1;
      var statusMatch = status === "all" ||
        (status === "pass" && g.key.dataset.pass === "1") ||
        (status === "rule-fail" && g.key.dataset.rule === "fail") ||
        (status === "quality-fail" && g.key.dataset.quality === "fail");
      g.rows.forEach(function (r) { r.classList.toggle("hidden", !(textMatch && statusMatch)); });
    });
  }
  if (filterInput) filterInput.addEventListener("input", applyFilters);
  if (statusFilter) statusFilter.addEventListener("change", applyFilters);
})();
"""


def _render_summary(reports: list[CaseReport]) -> str:
    """顶部紧凑统计条：通过 / 失败数 + 全部用例 token 总量（复用 ``_REPORT_INDEX_CSS`` 呈现美学）。

    纯呈现、无动态文本注入风险（三个数字均是内部计算的 int，无需转义）。
    """
    passed = sum(r.passed for r in reports)
    failed = len(reports) - passed
    execution_tokens = sum(r.execution_tokens for r in reports)
    judge_tokens = sum(r.judge_tokens for r in reports)
    failed_cls = "bad" if failed else "ok"
    return (
        '<div class="summary">'
        '<div class="stat ok"><span class="n">'
        f'{passed}</span><span class="l">passed</span></div>'
        f'<div class="stat {failed_cls}"><span class="n">'
        f'{failed}</span><span class="l">failed</span></div>'
        '<div class="stat"><span class="n">'
        f'{execution_tokens}</span><span class="l">execution tokens</span></div>'
        '<div class="stat"><span class="n">'
        f'{judge_tokens}</span><span class="l">judge tokens</span></div>'
        "</div>"
    )


def _render_report_index(reports: list[CaseReport]) -> str:
    """跨用例汇总索引页（自包含、内联 CSS + 内联 JS）：逐用例 pass/fail + token + prompt 版本，
    行链到详情页；附顶部通过/失败/token 汇总条，表头可点击排序（case id / kind / pass-fail /
    tokens），文本框可客户端筛选可见行。

    纯呈现：所有动态文本（case id / prompt 版本 / 失败明细）经 ``html.escape`` 转义后注入（含用作
    ``data-*`` 属性值时）；相对链接 ``<a href="{id}.html">`` 指向同目录的每用例详情（各自自包含、
    无外部请求）。排序/筛选是纯客户端行为，不改变 ``reports`` 本身、不影响 pass/fail 判定。
    """
    passed = sum(r.passed for r in reports)
    rows: list[str] = []
    for r in reports:
        mark = "PASS" if r.passed else "FAIL"
        cls = "pass" if r.passed else "fail"
        rule_mark = "PASS" if r.rule_passed else "FAIL"
        rule_cls = "pass" if r.rule_passed else "fail"
        if r.quality_passed is None:
            quality_mark = "N/A"
            quality_cls = ""
            quality_data = "na"
        else:
            quality_mark = "PASS" if r.quality_passed else "FAIL"
            quality_cls = "pass" if r.quality_passed else "fail"
            quality_data = "pass" if r.quality_passed else "fail"
        rubric = r.quality_rubric_id or "—"
        prompts = ", ".join(r.prompt_versions) if r.prompt_versions else "—"
        judge_prompts = ", ".join(r.judge_prompt_versions) if r.judge_prompt_versions else "—"
        href = html.escape(f"{r.case_id}.html", quote=True)
        case_id_attr = html.escape(r.case_id, quote=True)
        kind_attr = html.escape(r.kind, quote=True)
        rows.append(
            '<tr class="case-row" '
            f'data-id="{case_id_attr}" data-kind="{kind_attr}" '
            f'data-pass="{1 if r.passed else 0}" data-rule="{rule_cls}" '
            f'data-quality="{quality_data}" data-tokens="{r.total_tokens}">'
            f'<td><a href="{href}">{html.escape(r.case_id)}</a></td>'
            f"<td>{html.escape(r.kind)}</td>"
            f'<td class="{cls}">{mark}</td>'
            f'<td class="{rule_cls}">{rule_mark}</td>'
            f'<td class="{quality_cls}">{quality_mark}</td>'
            f"<td>{r.total_tokens}</td>"
            f"<td>{r.judge_tokens}</td>"
            f"<td>{html.escape(rubric)}</td>"
            f"<td>{html.escape(prompts)}</td>"
            f"<td>{html.escape(judge_prompts)}</td>"
            "</tr>"
        )
        for failure in r.failures:  # 失败明细挂在该行下方（红字）；同 data-id 供 JS 分组整体移动
            cell = f'<td colspan="9">✗ {html.escape(failure)}</td>'
            rows.append(f'<tr class="fail-detail" data-id="{case_id_attr}"><td></td>{cell}</tr>')
    body = (
        f"<h1>Eval 报告 · {passed}/{len(reports)} 通过</h1>"
        f"{_render_summary(reports)}"
        '<div class="controls">'
        '<input type="search" id="case-filter" placeholder="筛选 case id / kind…" '
        'aria-label="筛选用例">'
        '<select id="status-filter" aria-label="筛选状态">'
        '<option value="all">全部状态</option>'
        '<option value="pass">全部通过</option>'
        '<option value="rule-fail">Rule 失败</option>'
        '<option value="quality-fail">Quality 失败</option>'
        "</select>"
        "</div>"
        '<table class="cases"><thead><tr>'
        '<th data-sort-key="id">case</th>'
        '<th data-sort-key="kind">kind</th>'
        '<th data-sort-key="pass">pass</th>'
        "<th>Rule</th>"
        "<th>Quality</th>"
        '<th data-sort-key="tokens">execution tokens</th>'
        "<th>judge tokens</th>"
        "<th>rubric</th>"
        "<th>subject prompts</th>"
        "<th>judge prompts</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return (
        "<!doctype html>"
        '<html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Eval 报告</title>"
        f"<style>{_REPORT_INDEX_CSS}</style>"
        f"</head><body>{body}"
        f"<script>{_REPORT_INDEX_JS}</script>"
        "</body></html>"
    )


def _quality_detail_section(report: CaseReport) -> str:
    """把结构化质量判定附到 subject 详情；judge 事件树仍交给 trace renderer 单独渲染。"""
    evaluation = report.quality_evaluation
    if evaluation is None:
        return ""
    rows: list[str] = []
    for criterion in evaluation.criteria:
        rows.append(
            "<tr>"
            f"<td>{html.escape(criterion.criterion_id)}</td>"
            f"<td>{criterion.score}</td>"
            f"<td>{html.escape(criterion.rationale)}</td>"
            f"<td>{html.escape(criterion.candidate_evidence)}</td>"
            f"<td>{html.escape(criterion.reference_evidence)}</td>"
            "</tr>"
        )
    return (
        '<section class="quality-evaluation">'
        "<h2>Tier-2 Quality</h2>"
        '<div class="meta">'
        f'<span class="kv"><span class="k">rubric</span> '
        f'<span class="v">{html.escape(evaluation.rubric_id)}</span></span>'
        f'<span class="kv"><span class="k">prompt</span> '
        f'<span class="v">{html.escape(evaluation.prompt_version)}</span></span>'
        f'<span class="kv"><span class="k">judge tokens</span> '
        f'<span class="v">{evaluation.usage.total_tokens}</span></span>'
        '</div><table class="events"><thead><tr>'
        "<th>criterion</th><th>score</th><th>rationale</th>"
        "<th>candidate evidence</th><th>reference evidence</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        f"<p>{html.escape(evaluation.overall_rationale)}</p>"
        f'<p><a href="{html.escape(report.case_id, quote=True)}-quality.html">'
        "查看独立 judge trace</a></p></section>"
    )


def _append_before_body(document: str, fragment: str) -> str:
    return document.replace("</body>", f"{fragment}</body>", 1)


def export_reports_html(reports: list[CaseReport], out_dir: Path) -> Path:
    """导出可点开的自包含 HTML：索引页 + 每用例一份 render_trace_html 详情。

    多文件布局：``<out_dir>/index.html``（汇总表：逐用例 pass/fail + token + prompt 版本，链到详情）
    + ``<out_dir>/<case_id>.html``（复用 issue 03 的 ``render_trace_html`` 渲染该用例的 span 树 +
    事件流）。各文件相对链接、各自自包含、零外部请求。返回索引页路径。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        meta: dict[str, Any] = {
            "case_id": report.case_id,
            "kind": report.kind,
            "verdict": "PASS" if report.passed else "FAIL",
            "rule": "PASS" if report.rule_passed else "FAIL",
            "quality": (
                "N/A"
                if report.quality_passed is None
                else "PASS"
                if report.quality_passed
                else "FAIL"
            ),
            "execution_tokens": report.total_tokens,
            "judge_tokens": report.judge_tokens,
            "rubric": report.quality_rubric_id or "—",
            "prompt_versions": ", ".join(report.prompt_versions) if report.prompt_versions else "—",
            "event_count": len(report.subject_events),
        }
        detail = render_trace_html(
            report.subject_events,
            report.subject_spans,
            meta=meta,
            title=f"用例 {report.case_id}",
        )
        detail = _append_before_body(detail, _quality_detail_section(report))
        (out_dir / f"{report.case_id}.html").write_text(detail, encoding="utf-8")
        if report.quality_events:
            quality_trace = render_trace_html(
                report.quality_events,
                report.quality_spans,
                meta={
                    "case_id": report.case_id,
                    "rubric": (report.quality_rubric_id or "—"),
                    "judge_tokens": report.judge_tokens,
                },
                title=f"用例 {report.case_id} · Quality Judge",
            )
            (out_dir / f"{report.case_id}-quality.html").write_text(
                quality_trace,
                encoding="utf-8",
            )
    index_path = out_dir / "index.html"
    index_path.write_text(_render_report_index(reports), encoding="utf-8")
    return index_path

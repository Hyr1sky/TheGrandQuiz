"""Audit deterministic invariants in the disposable Playwright trace database."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    location_file = Path(sys.argv[1])
    runtime_dir = Path(location_file.read_text(encoding="utf-8").strip())
    trace_db = runtime_dir / "trace.db"
    connection = sqlite3.connect(trace_db)
    rows = connection.execute(
        "SELECT trace_id, seq, type FROM events ORDER BY trace_id, seq"
    ).fetchall()
    connection.close()

    traces: dict[str, list[tuple[int, str]]] = {}
    for trace_id, sequence, event_type in rows:
        traces.setdefault(str(trace_id), []).append((int(sequence), str(event_type)))

    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for trace_id, events in traces.items():
        sequences = [sequence for sequence, _ in events]
        event_types = [event_type for _, event_type in events]
        contiguous = sequences == list(range(len(sequences)))
        balanced_turns = event_types.count("agent_turn.started") == event_types.count(
            "agent_turn.ended"
        )
        assessment_starts = event_types.count("web.assessment_run.started")
        assessment_ends = event_types.count("web.assessment_run.ended")
        balanced_assessment = assessment_starts == assessment_ends
        results[trace_id] = {
            "event_count": len(events),
            "contiguous_sequences": contiguous,
            "balanced_agent_turns": balanced_turns,
            "balanced_assessment_runs": balanced_assessment,
        }
        if not all((contiguous, balanced_turns, balanced_assessment)):
            failures.append(trace_id)

    report = {
        "trace_db": str(trace_db),
        "trace_count": len(traces),
        "failures": failures,
        "traces": results,
    }
    report_path = location_file.parent / "trace-audit.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failures:
        raise SystemExit(f"trace invariant audit failed: {', '.join(failures)}")


if __name__ == "__main__":
    main()

"""Deterministic, redacted review exports derived only from the learning journal."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grandquiz.domain.learning.assessment_history import (
    project_assessment_attempts,
    project_learner,
)
from grandquiz.domain.learning.learning_facts import DEFAULT_REDACTION_PROFILE
from grandquiz.domain.learning.persistence import LearningPersistence


@dataclass(frozen=True)
class LearningReviewExport:
    out_dir: Path
    manifest: dict[str, Any]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def export_learning_review(
    *,
    db_path: str | Path,
    out_dir: str | Path,
) -> LearningReviewExport:
    """Write stable JSONL/Markdown/manifest artifacts; never read operational trace."""

    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    with LearningPersistence(db_path) as persistence:
        facts = persistence.learning_facts.facts()
    jsonl_text = "".join(
        json.dumps(
            fact.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for fact in facts
    )
    attempts = project_assessment_attempts(facts)
    item_ids = sorted({attempt.item_id for attempt in attempts})
    projections = [
        projection
        for item_id in item_ids
        if (projection := project_learner(attempts, item_id=item_id)) is not None
    ]
    summary_lines = [
        "# Learning Review",
        "",
        f"- Facts: {len(facts)}",
        f"- Attempts: {len(attempts)}",
        f"- Knowledge items with history: {len(projections)}",
        "",
        "## Attempts",
        "",
    ]
    summary_lines.extend(
        f"- `{attempt.attempt_id}` · item `{attempt.item_id}` · "
        f"{attempt.initial_verdict} → {attempt.final_verdict}"
        for attempt in attempts
    )
    if not attempts:
        summary_lines.append("- No committed assessment attempts.")
    summary_text = "\n".join(summary_lines) + "\n"
    jsonl_bytes = jsonl_text.encode("utf-8")
    summary_bytes = summary_text.encode("utf-8")
    cursors = [
        {
            "trace_id": fact.trace_id,
            "source_event_seq": fact.source_event_seq,
            "event_id": fact.event_id,
        }
        for fact in facts
    ]
    manifest: dict[str, Any] = {
        "schema_version": "learning-review-manifest.v1",
        "redaction_profile": DEFAULT_REDACTION_PROFILE,
        "fact_count": len(facts),
        "trace_ids": sorted({fact.trace_id for fact in facts}),
        "cursor": {
            "first": cursors[0] if cursors else None,
            "last": cursors[-1] if cursors else None,
        },
        "files": {
            "learning-facts.jsonl": _sha256(jsonl_bytes),
            "summary.md": _sha256(summary_bytes),
        },
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    (target / "learning-facts.jsonl").write_bytes(jsonl_bytes)
    (target / "summary.md").write_bytes(summary_bytes)
    (target / "manifest.json").write_text(manifest_text, encoding="utf-8")
    return LearningReviewExport(out_dir=target, manifest=manifest)

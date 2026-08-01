import json
from pathlib import Path

from grandquiz.domain.learning.learning_export import export_learning_review
from grandquiz.domain.learning.learning_facts import LearningFactEnvelope
from grandquiz.domain.learning.persistence import LearningPersistence


def test_learning_review_export_is_stable_and_uses_journal_not_trace(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "learning.db"
    trace_path = tmp_path / "trace.db"
    trace_path.write_text("operational trace may be deleted")
    trace_id = "trace-export-1"
    with LearningPersistence(db_path) as persistence:
        persistence.learning_facts.append(
            LearningFactEnvelope(
                event_id="fact-1",
                event_type="learning.assessment_judgement_committed",
                entity_id="attempt-1",
                trace_id=trace_id,
                source_event_seq=7,
                source_event_ts=10.0,
                payload_schema_version="assessment-judgement-committed.v1",
                payload={
                    "attempt_id": "attempt-1",
                    "assessment_span_id": "trace-export-1:s1",
                    "item_id": "item-1",
                    "question_text": "PageIndex 是什么？",
                    "answer_text": "一种长文档检索方法",
                    "initial_verdict": "对",
                    "concept_state": None,
                    "difficulty_tier": 3,
                    "adaptive_route": {
                        "format": "multiple_choice",
                        "strategy": "standard",
                    },
                    "effective_route": {
                        "format": "multiple_choice",
                        "strategy": "standard",
                    },
                    "routing_source": "adaptive",
                    "input_modality": "text",
                    "answer_format": "choice",
                    "evidence_revealed_before_answer": False,
                    "elapsed_ms": 1000,
                    "question_generation": {
                        "kind": "model",
                        "version": "question_multiple_choice@test",
                    },
                    "grading": {
                        "kind": "deterministic",
                        "version": "multiple-choice-exact.v1",
                    },
                },
            )
        )
    first_out = tmp_path / "first-export"
    second_out = tmp_path / "second-export"

    first = export_learning_review(db_path=db_path, out_dir=first_out)
    trace_path.unlink()
    second = export_learning_review(db_path=db_path, out_dir=second_out)

    assert first.manifest == second.manifest
    assert (first_out / "learning-facts.jsonl").read_bytes() == (
        second_out / "learning-facts.jsonl"
    ).read_bytes()
    assert (first_out / "summary.md").read_bytes() == (second_out / "summary.md").read_bytes()
    assert (first_out / "eval-candidates.jsonl").read_bytes() == (
        second_out / "eval-candidates.jsonl"
    ).read_bytes()
    manifest = json.loads((first_out / "manifest.json").read_text())
    assert manifest["fact_count"] == 1
    assert manifest["trace_ids"] == [trace_id]
    assert manifest["eval_candidate_count"] == 0
    assert manifest["privacy_review_required"] is True
    assert "eval-candidates.jsonl" in manifest["files"]
    jsonl = (first_out / "learning-facts.jsonl").read_text()
    assert "prompt_tokens" not in jsonl
    assert "completion_tokens" not in jsonl

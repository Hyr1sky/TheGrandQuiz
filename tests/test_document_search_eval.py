"""DS-S4 规则型 capstone eval。"""

from grandquiz.evals.document_search import run_document_search_capstone


def test_agentic_search_finds_grounded_evidence_without_reading_full_document() -> None:
    report = run_document_search_capstone()

    assert report.passed, report.failures
    assert report.candidate_count > 0
    assert report.read_chars < report.full_document_chars // 10
    assert report.citation_quote == "durable processor 失败必须阻断当前 turn"

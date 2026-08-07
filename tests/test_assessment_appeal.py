import pytest

from grandquiz.domain.learning.assessment.appeal import (
    AppealSubmission,
    AppealSubmissionConflict,
)


def test_appeal_preserves_original_answer_and_builds_stable_regrade_input() -> None:
    appeal = AppealSubmission.create(
        request_id="appeal-1",
        original_answer="首次回答。",
        supplemental_answer="我再补充一个关键边界。",
    )

    assert appeal.original_answer == "首次回答。"
    assert appeal.supplemental_answer == "我再补充一个关键边界。"
    assert appeal.answer_for_regrade == (
        "首次回答：\n首次回答。\n\n补充说明：\n我再补充一个关键边界。"
    )
    assert (
        appeal.accept_retry(
            request_id="appeal-1",
            supplemental_answer="我再补充一个关键边界。",
        )
        is appeal
    )


def test_appeal_accepts_only_one_non_blank_supplement() -> None:
    with pytest.raises(ValueError, match="补充说明不能为空"):
        AppealSubmission.create(
            request_id="appeal-1",
            original_answer="首次回答。",
            supplemental_answer="   ",
        )

    appeal = AppealSubmission.create(
        request_id="appeal-1",
        original_answer="首次回答。",
        supplemental_answer="补充。",
    )
    with pytest.raises(AppealSubmissionConflict, match="已经提交过补充说明"):
        appeal.accept_retry(
            request_id="appeal-2",
            supplemental_answer="第二次补充。",
        )

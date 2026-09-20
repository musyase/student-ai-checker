from __future__ import annotations

import pytest

from student_ai_checker.quality_evaluation import (
    compare_case, summarize_reports, validate_expected_case,
)


def actual(*results):
    return {"results": list(results)}


def result(criterion_id, score, manual=False):
    return {
        "criterion_id": criterion_id,
        "proposed_score": score,
        "manual_review_required": manual,
        "evidence_ids": ["DOC1:P:0001"],
    }


def test_acceptance_case_matches_score_manual_review_and_evidence():
    expected = {"case_id": "work-001", "criteria": {"criterion": {"score": 2, "manual_review_expected": False}}}
    report = compare_case(expected, actual(result("criterion", 2)))
    assert report["passed"] is True
    assert report["criteria"][0]["reason"] is None


def test_missing_score_for_manual_review_is_supported():
    expected = {"case_id": "work-002", "criteria": {"criterion": {"score": None, "manual_review_expected": True}}}
    report = compare_case(expected, actual(result("criterion", None, manual=True)))
    assert report["passed"] is True


def test_mismatch_report_is_explainable_and_does_not_hide_unexpected_criteria():
    expected = {"case_id": "work-003", "criteria": {"criterion": {"score": 2, "manual_review_expected": False}}}
    report = compare_case(expected, actual(result("criterion", 1), result("extra", 0)))
    assert report["passed"] is False
    assert report["unexpected_criteria"] == ["extra"]
    assert report["criteria"][0]["score_match"] is False


def test_metadata_only_fallback_is_not_real_evidence_when_evidence_is_expected():
    expected = {"case_id": "work-004", "criteria": {"criterion": {"score": 0}}}
    report = compare_case(expected, actual({
        **result("criterion", 0), "evidence_ids": ["DOC1:META:0001"],
    }))
    assert report["passed"] is False
    assert report["criteria"][0]["evidence_present"] is False
    assert "missing_real_evidence" in report["criteria"][0]["mismatch_types"]


def test_explicitly_empty_answer_can_opt_out_of_real_evidence_requirement():
    expected = {"case_id": "work-005", "criteria": {
        "criterion": {"score": 0, "evidence_expected": False},
    }}
    report = compare_case(expected, actual({
        **result("criterion", 0), "evidence_ids": ["DOC1:META:0001"],
    }))
    assert report["passed"] is True


def test_technical_failure_must_not_be_accepted_as_zero():
    expected = {"case_id": "work-006", "criteria": {"criterion": {"score": 0}}}
    report = compare_case(expected, actual({
        **result("criterion", 0), "status": "unable_to_check",
    }))
    assert report["passed"] is False
    assert report["criteria"][0]["technical_failure_as_zero"] is True


def test_summary_counts_criterion_outcomes_and_mismatch_types():
    expected = {"case_id": "work-007", "criteria": {"criterion": {"score": 2}}}
    report = compare_case(expected, actual(result("criterion", 1)))
    summary = summarize_reports([report])
    assert summary["case_count"] == 1
    assert summary["criterion_count"] == 1
    assert summary["score_matches"] == 0
    assert summary["mismatch_counts"] == {"score_mismatch": 1}


def test_expected_labels_are_checked_against_the_approved_profile():
    allowed = {"criterion": [0, 1, 2]}
    validate_expected_case({"criteria": {"criterion": {"score": 2}}}, allowed)
    with pytest.raises(ValueError, match="allowed_scores"):
        validate_expected_case({"criteria": {"criterion": {"score": 3}}}, allowed)
    with pytest.raises(ValueError, match="criteria set mismatch"):
        validate_expected_case({"criteria": {"other": {"score": 1}}}, allowed)
    with pytest.raises(ValueError, match="manual_review_expected"):
        validate_expected_case({"criteria": {"criterion": {"score": None, "manual_review_expected": False}}}, allowed)

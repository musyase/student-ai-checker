"""Compare an automatic review with a teacher-scored acceptance case."""

from __future__ import annotations

from collections.abc import Mapping
from collections import Counter
from math import isfinite
from typing import Any


def _strict_bool(value: Any, *, field: str, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be boolean")
    return value


def _valid_expected_score(value: Any) -> bool:
    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and isfinite(float(value))
    )


def validate_expected_case(expected: Mapping[str, Any], allowed_scores: Mapping[str, Any]) -> None:
    """Validate teacher labels against the approved criterion profile before inference."""
    expected_criteria = expected.get("criteria")
    if not isinstance(expected_criteria, Mapping) or not expected_criteria:
        raise ValueError("expected case must contain a non-empty object named criteria")
    expected_ids = set(expected_criteria)
    allowed_ids = set(allowed_scores)
    if expected_ids != allowed_ids:
        missing = sorted(allowed_ids - expected_ids)
        unexpected = sorted(expected_ids - allowed_ids)
        raise ValueError(f"criteria set mismatch; missing={missing}, unexpected={unexpected}")
    for criterion_id, expected_item in expected_criteria.items():
        if not isinstance(criterion_id, str) or not isinstance(expected_item, Mapping):
            raise ValueError("each expected criterion must be an object keyed by criterion ID")
        score = expected_item.get("score")
        if not _valid_expected_score(score):
            raise ValueError(f"score for {criterion_id} must be a finite number or null")
        if score is not None and score not in allowed_scores[criterion_id]:
            raise ValueError(f"score for {criterion_id} is not in allowed_scores")
        manual = _strict_bool(
            expected_item.get("manual_review_expected"),
            field=f"manual_review_expected for {criterion_id}",
            default=score is None,
        )
        if score is None and not manual:
            raise ValueError(f"null score for {criterion_id} requires manual_review_expected=true")
        _strict_bool(
            expected_item.get("evidence_expected"),
            field=f"evidence_expected for {criterion_id}",
            default=True,
        )


def compare_case(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explainable criterion-level comparison without changing results."""
    expected_criteria = expected.get("criteria")
    actual_results = actual.get("results")
    if not isinstance(expected_criteria, Mapping):
        raise ValueError("expected case must contain an object named criteria")
    if not isinstance(actual_results, list):
        raise ValueError("automatic result must contain a list named results")
    if not expected_criteria:
        raise ValueError("expected case must contain at least one criterion")

    actual_by_id: dict[str, Mapping[str, Any]] = {}
    duplicate_ids: list[str] = []
    for item in actual_results:
        if not isinstance(item, Mapping) or not isinstance(item.get("criterion_id"), str):
            continue
        criterion_id = item["criterion_id"]
        if criterion_id in actual_by_id:
            duplicate_ids.append(criterion_id)
        actual_by_id[criterion_id] = item
    if duplicate_ids:
        raise ValueError("automatic result contains duplicate criterion IDs: " + ", ".join(sorted(set(duplicate_ids))))

    criteria: list[dict[str, Any]] = []
    for criterion_id, expected_item in expected_criteria.items():
        if not isinstance(criterion_id, str) or not isinstance(expected_item, Mapping):
            raise ValueError("each expected criterion must be an object keyed by criterion ID")
        observed = actual_by_id.get(criterion_id)
        expected_score = expected_item.get("score")
        if not _valid_expected_score(expected_score):
            raise ValueError(f"score for {criterion_id} must be a finite number or null")
        expected_manual = _strict_bool(
            expected_item.get("manual_review_expected"),
            field=f"manual_review_expected for {criterion_id}",
            default=expected_score is None,
        )
        evidence_expected = _strict_bool(
            expected_item.get("evidence_expected"),
            field=f"evidence_expected for {criterion_id}",
            default=True,
        )
        if observed is None:
            criteria.append({
                "criterion_id": criterion_id,
                "passed": False,
                "score_match": False,
                "manual_review_match": False,
                "evidence_expected": evidence_expected,
                "evidence_match": False,
                "mismatch_types": ["criterion_missing"],
                "reason": "criterion_missing_from_automatic_result",
            })
            continue

        actual_score = observed.get("proposed_score")
        actual_manual = observed.get("manual_review_required")
        if not isinstance(actual_manual, bool):
            raise ValueError(f"manual_review_required for {criterion_id} must be boolean")
        score_match = actual_score == expected_score
        manual_match = actual_manual == expected_manual
        evidence_ids = observed.get("evidence_ids")
        evidence_present = (
            isinstance(evidence_ids, list)
            and all(isinstance(value, str) and bool(value.strip()) for value in evidence_ids)
            and any(not value.startswith("DOC1:META:") for value in evidence_ids)
        )
        evidence_match = not evidence_expected or evidence_present
        technical_zero = actual_score == 0 and observed.get("status") in {
            "unable_to_check", "insufficient_evidence",
        }
        mismatch_types = []
        if not score_match:
            mismatch_types.append("score_mismatch")
        if not manual_match:
            mismatch_types.append("manual_review_mismatch")
        if not evidence_match:
            mismatch_types.append("missing_real_evidence")
        if technical_zero:
            mismatch_types.append("technical_failure_as_zero")
        passed = not mismatch_types
        criteria.append({
            "criterion_id": criterion_id,
            "expected_score": expected_score,
            "actual_score": actual_score,
            "expected_manual_review": expected_manual,
            "actual_manual_review": actual_manual,
            "score_match": score_match,
            "manual_review_match": manual_match,
            "evidence_expected": evidence_expected,
            "evidence_present": evidence_present,
            "evidence_match": evidence_match,
            "technical_failure_as_zero": technical_zero,
            "mismatch_types": mismatch_types,
            "passed": passed,
            "reason": None if passed else "criterion_mismatch",
        })

    expected_ids = set(expected_criteria)
    unexpected = sorted(set(actual_by_id) - expected_ids)
    return {
        "case_id": expected.get("case_id"),
        "passed": bool(criteria) and all(item["passed"] for item in criteria) and not unexpected,
        "criteria": criteria,
        "unexpected_criteria": unexpected,
    }


def summarize_reports(reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate acceptance outcomes without hiding individual case reports."""
    criterion_reports = [
        item for report in reports for item in report.get("criteria", [])
        if isinstance(item, Mapping)
    ]
    mismatch_counts = Counter(
        mismatch for item in criterion_reports for mismatch in item.get("mismatch_types", [])
    )
    return {
        "case_count": len(reports),
        "passed_cases": sum(bool(report.get("passed")) for report in reports),
        "criterion_count": len(criterion_reports),
        "passed_criteria": sum(bool(item.get("passed")) for item in criterion_reports),
        "score_matches": sum(bool(item.get("score_match")) for item in criterion_reports),
        "manual_review_matches": sum(bool(item.get("manual_review_match")) for item in criterion_reports),
        "evidence_matches": sum(bool(item.get("evidence_match")) for item in criterion_reports),
        "unexpected_criteria_count": sum(
            len(report.get("unexpected_criteria", [])) for report in reports
            if isinstance(report.get("unexpected_criteria", []), list)
        ),
        "mismatch_counts": dict(sorted(mismatch_counts.items())),
    }

"""Safe, local teacher decisions layered on top of an automatic result."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from math import isfinite
from typing import Any

from .grading.grade_scale import GradeScale
from .models import CriteriaSet, CriterionResult

TEACHER_REVIEW_SCHEMA_VERSION = 1


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_decisions(decisions: Mapping[str, Any]) -> Mapping[str, Any]:
    value = decisions.get("decisions")
    if not isinstance(value, Mapping):
        raise ValueError("teacher decisions must contain an object named decisions")
    return value


def _score(value: Any, criterion_id: str, allowed: list[float]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError(f"teacher score for {criterion_id} must be a finite number")
    numeric = float(value)
    if numeric not in allowed:
        raise ValueError(f"teacher score for {criterion_id} is not in allowed_scores")
    return numeric


def build_teacher_review(
    automatic_result: Mapping[str, Any],
    criteria: CriteriaSet,
    decisions_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a reviewed copy without mutating automatic results or their audit.

    Every criterion may be ``confirm``ed or assigned a validated ``set_score``.
    Missing decisions intentionally leave the review pending. This prevents a
    partial form submission from looking like a teacher-approved grade.
    """
    raw_results = automatic_result.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("automatic result must contain a non-empty results list")
    cleaned_results: list[CriterionResult] = []
    for item in raw_results:
        if not isinstance(item, Mapping):
            raise ValueError("automatic result contains an invalid criterion result")
        cleaned = {key: value for key, value in item.items() if key != "status_label_ru"}
        try:
            cleaned_results.append(CriterionResult.model_validate(cleaned))
        except ValueError as exc:
            raise ValueError("automatic result contains an invalid criterion result") from exc

    criteria_by_id = criteria.by_id()
    automatic_by_id = {item.criterion_id: item for item in cleaned_results}
    if len(automatic_by_id) != len(cleaned_results):
        raise ValueError("automatic result contains duplicate criterion IDs")
    if set(automatic_by_id) != set(criteria_by_id):
        raise ValueError("automatic result criteria do not match the approved criteria")

    decisions = _read_decisions(decisions_payload)
    unknown_decisions = sorted(set(decisions) - set(criteria_by_id))
    if unknown_decisions:
        raise ValueError("teacher decisions contain unknown criteria: " + ", ".join(unknown_decisions))

    decision_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    pending = False
    for criterion in criteria.criteria:
        criterion_id = criterion.id
        automatic = automatic_by_id[criterion_id]
        raw_decision = decisions.get(criterion_id)
        if raw_decision is not None and not isinstance(raw_decision, Mapping):
            raise ValueError(f"teacher decision for {criterion_id} must be an object")

        final_score: float | None = automatic.proposed_score
        source = "automatic_unconfirmed"
        action = None
        comment = None
        if raw_decision is not None:
            action = raw_decision.get("action")
            comment_value = raw_decision.get("comment")
            if comment_value is not None and (not isinstance(comment_value, str) or len(comment_value) > 4000):
                raise ValueError(f"teacher comment for {criterion_id} must be a string of at most 4000 characters")
            comment = comment_value
            if action == "confirm":
                if automatic.proposed_score is None:
                    raise ValueError(f"cannot confirm a null automatic score for {criterion_id}; use set_score")
                if "score" in raw_decision:
                    raise ValueError(f"confirm decision for {criterion_id} must not contain score")
                final_score = float(automatic.proposed_score)
                source = "teacher_confirmed"
            elif action == "set_score":
                if "score" not in raw_decision:
                    raise ValueError(f"set_score decision for {criterion_id} requires score")
                final_score = _score(raw_decision["score"], criterion_id, criterion.allowed_scores)
                source = "teacher_override"
            else:
                raise ValueError(f"teacher action for {criterion_id} must be confirm or set_score")

            decision_rows.append({
                "criterion_id": criterion_id,
                "action": action,
                "automatic_score": automatic.proposed_score,
                "automatic_manual_review": automatic.manual_review_required,
                "final_score": final_score,
                "comment": comment,
            })

        if raw_decision is None or final_score is None:
            pending = True
            if raw_decision is None:
                source = "pending_teacher_decision"
        final_rows.append({
            "criterion_id": criterion_id,
            "title": criterion.title,
            "max_score": criterion.max_score,
            "score": final_score,
            "source": source,
            "automatic_manual_review": automatic.manual_review_required,
            "automatic_status": automatic.status.value,
            "comment": comment,
        })

    status = "pending" if pending else "completed"
    review = {
        "schema_version": TEACHER_REVIEW_SCHEMA_VERSION,
        "status": status,
        "source_result_sha256": _digest(automatic_result),
        "decisions": decision_rows,
        "final_results": final_rows,
    }
    output = copy.deepcopy(dict(automatic_result))
    output["teacher_review"] = review
    summary = output.get("summary")
    if isinstance(summary, Mapping):
        summary = dict(summary)
    else:
        summary = {}
    summary["teacher_confirmed"] = status == "completed"
    summary["teacher_review_status"] = status
    summary["final_total"] = (
        sum(row["score"] for row in final_rows if row["score"] is not None)
        if status == "completed" else None
    )
    grade_scale = GradeScale.standard()
    approval = output.get("approval")
    if isinstance(approval, Mapping) and approval.get("grade_scale") is not None:
        try:
            grade_scale = GradeScale.model_validate(approval["grade_scale"])
        except ValueError as exc:
            raise ValueError("automatic result contains an invalid grade scale") from exc
    summary["final_grade"] = (
        grade_scale.grade_for(summary["final_total"], sum(row["max_score"] for row in final_rows))
        if status == "completed" else None
    )
    summary["grade"] = summary["final_grade"]
    output["summary"] = summary
    return output

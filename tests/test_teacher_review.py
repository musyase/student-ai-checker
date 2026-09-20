from __future__ import annotations

import copy

import pytest

from student_ai_checker.models import CriteriaSet
from student_ai_checker.teacher_review import build_teacher_review


def criteria() -> CriteriaSet:
    return CriteriaSet.model_validate({
        "version": 1,
        "criteria": [
            {"id": "c1", "title": "Первый", "description": "Первый критерий",
             "max_score": 2, "allowed_scores": [0, 1, 2]},
            {"id": "c2", "title": "Второй", "description": "Второй критерий",
             "max_score": 1, "allowed_scores": [0, 1]},
        ],
    })


def automatic() -> dict:
    return {
        "schema_version": 1,
        "document": {"source_name": "student.docx", "source_sha256": "a" * 64},
        "results": [
            {"criterion_id": "c1", "title": "Первый", "max_score": 2,
             "proposed_score": None, "status": "insufficient_evidence",
             "explanation": "Нужно уточнение.", "evidence_ids": ["DOC1:META:0001"],
             "student_feedback": "", "manual_review_required": True,
             "manual_review_reason": "Неоднозначно.", "status_label_ru": "Недостаточно подтверждений"},
            {"criterion_id": "c2", "title": "Второй", "max_score": 1,
             "proposed_score": 1, "status": "completed",
             "explanation": "Подтверждено.", "evidence_ids": ["DOC1:P:0002"],
             "student_feedback": "", "manual_review_required": False,
             "manual_review_reason": None, "status_label_ru": "Выполнено"},
        ],
        "summary": {"proposed_total": None, "teacher_confirmed": False},
        "checked_scoring": {"criteria": [{"attempts": ["original model audit"]}]},
    }


def test_partial_teacher_form_stays_pending_and_preserves_automatic_result():
    source = automatic()
    before = copy.deepcopy(source)
    reviewed = build_teacher_review(source, criteria(), {"decisions": {"c1": {"action": "set_score", "score": 2}}})
    assert reviewed["teacher_review"]["status"] == "pending"
    assert reviewed["summary"]["teacher_confirmed"] is False
    assert reviewed["summary"]["final_total"] is None
    assert reviewed["results"] == source["results"]
    assert source == before
    assert reviewed["checked_scoring"] == source["checked_scoring"]
    assert reviewed["teacher_review"]["final_results"][0]["source"] == "teacher_override"
    assert reviewed["teacher_review"]["final_results"][1]["source"] == "pending_teacher_decision"


def test_full_review_confirms_and_overrides_without_rewriting_ai_results():
    source = automatic()
    reviewed = build_teacher_review(source, criteria(), {"decisions": {
        "c1": {"action": "set_score", "score": 2, "comment": "Проверено преподавателем."},
        "c2": {"action": "confirm"},
    }})
    review = reviewed["teacher_review"]
    assert review["status"] == "completed"
    assert reviewed["summary"]["teacher_confirmed"] is True
    assert reviewed["summary"]["final_total"] == 3.0
    assert [item["score"] for item in review["final_results"]] == [2.0, 1.0]
    assert [item["source"] for item in review["final_results"]] == ["teacher_override", "teacher_confirmed"]
    assert review["source_result_sha256"]
    assert reviewed["results"] == source["results"]


@pytest.mark.parametrize("decisions", [
    {"decisions": {"c1": {"action": "confirm"}, "c2": {"action": "confirm"}}},
    {"decisions": {"c1": {"action": "set_score", "score": 3}, "c2": {"action": "confirm"}}},
])
def test_invalid_confirmation_or_score_cannot_create_reviewed_result(decisions):
    with pytest.raises(ValueError):
        build_teacher_review(automatic(), criteria(), decisions)


from __future__ import annotations

from student_ai_checker.teacher_roster import build_teacher_roster, normalize_group, student_label


def test_filename_helpers_normalize_groups_and_names():
    assert normalize_group("24ИСИП9-3") == "24ИСИП-9-3"
    assert student_label("24ИСИП-9-2_Березуцкая_ПР1.docx") == "Березуцкая"
    assert student_label("Практическая №1. Мусаев Григорян.docx") == "Мусаев Григорян"


def test_roster_sorts_by_group_then_surname_and_explains_manual_review():
    manifest = {"cases": [
        {"index": 2, "group": "24ИСИП9-3", "source_name": "24ИСИП9-3_Белов_ПР1.docx",
         "source_relative_path": "Белов.docx", "status": "completed", "proposed_total": None,
         "scored_points": 10, "max_score": 25, "proposed_grade": None, "manual_review_required": True},
        {"index": 1, "group": "24ИСИП-9-2", "source_name": "24ИСИП-9-2_Афонин_ПР1.docx",
         "source_relative_path": "Афонин.docx", "status": "completed", "proposed_total": 20,
         "scored_points": 20, "max_score": 25, "proposed_grade": 5, "manual_review_required": False},
    ]}
    rows = build_teacher_roster(manifest, {"items": [
        {"index": 2, "criterion_id": "pr1.summary", "reason": "Нет собственного итога"},
    ]})
    assert [row["Фамилия / студенты"] for row in rows] == ["Афонин", "Белов"]
    assert rows[1]["Статус"] == "Нужна проверка"
    assert rows[1]["Критерии для проверки"] == "pr1.summary"


def test_roster_shows_estimated_grade_for_partial_result():
    rows = build_teacher_roster({"cases": [{
        "index": 1, "group": "24ИСИП-9-2", "source_name": "Иванов ПР2.docx",
        "source_relative_path": "Иванов.docx", "status": "completed",
        "proposed_total": None, "scored_points": 120, "max_score": 165,
        "proposed_grade": None, "estimated_grade": 4, "grade_is_estimate": True,
        "manual_review_required": True,
    }]}, {"items": []})
    assert rows[0]["Оценка"] == 4
    assert rows[0]["Оценка примерная"] is True
    assert rows[0]["Статус"] == "Нужна проверка"

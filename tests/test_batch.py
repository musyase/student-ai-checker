from __future__ import annotations

import json
from pathlib import Path

import pytest

from student_ai_checker.batch import assignment_key, resolve_assignment_profile
from student_ai_checker.exceptions import StudentAICheckerError


@pytest.mark.parametrize("name", ["ПР1.docx", "Практическая №1.docx", "Практическая работа 1.docx"])
def test_assignment_key_accepts_common_practice_names(name: str):
    assert assignment_key(Path(name)) == "pr1"


def test_profile_registry_resolves_approved_files(tmp_path: Path):
    (tmp_path / "pr1.criteria.approved.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pr1.approval.json").write_text("{}", encoding="utf-8")
    (tmp_path / "assignment_profiles.json").write_text(json.dumps({
        "schema_version": 1,
        "assignments": {"pr1": {
            "practice": "1",
            "criteria": "pr1.criteria.approved.json",
            "approval": "pr1.approval.json",
        }},
    }), encoding="utf-8")
    profile = resolve_assignment_profile(Path("ПР1.docx"), tmp_path)
    assert profile.key == "pr1"
    assert profile.practice == "1"
    assert profile.criteria.name == "pr1.criteria.approved.json"


def test_missing_profile_explains_one_time_setup(tmp_path: Path):
    with pytest.raises(StudentAICheckerError, match="нет утверждённого профиля"):
        resolve_assignment_profile(Path("ПР2.docx"), tmp_path)

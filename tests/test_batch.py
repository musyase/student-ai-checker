from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest

from student_ai_checker.batch import assignment_key, resolve_assignment_profile
from student_ai_checker.exceptions import StudentAICheckerError


_BATCH_SCRIPT = Path(__file__).parents[1] / "scripts" / "run_checked_batch.py"
_BATCH_SPEC = importlib.util.spec_from_file_location("run_checked_batch", _BATCH_SCRIPT)
assert _BATCH_SPEC and _BATCH_SPEC.loader
_BATCH = importlib.util.module_from_spec(_BATCH_SPEC)
_BATCH_SPEC.loader.exec_module(_BATCH)


@pytest.mark.parametrize("name", ["ПР1.docx", "Практическая №1.docx", "Практическая работа 1.docx"])
def test_assignment_key_accepts_common_practice_names(name: str):
    assert assignment_key(Path(name)) == "pr1"


@pytest.mark.parametrize("name", ["ПР2.docx", "Практическая №10.docx", "П 3.docx"])
def test_assignment_key_accepts_other_practice_numbers(name: str):
    expected = {"ПР2.docx": "pr2", "Практическая №10.docx": "pr10", "П 3.docx": "pr3"}[name]
    assert assignment_key(Path(name)) == expected


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


@pytest.mark.parametrize("name", ["асташоваПР2.docx", "Дашкова и ОстапенкоПР2.docx", "ПР02_Антонян.docx"])
def test_batch_discovery_accepts_practice_marker_without_separator(name: str):
    assert _BATCH.practice_pattern("2").search(name)

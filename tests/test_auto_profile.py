from __future__ import annotations

import hashlib
import json
from pathlib import Path

from docx import Document

from student_ai_checker.auto_profile import generate_auto_profile
from student_ai_checker.batch import resolve_assignment_profile
from student_ai_checker.config import Settings
from student_ai_checker.grading.approval import verify_approval
from student_ai_checker.models import CriteriaSet


class ProfileOllamaStub:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls: list[dict] = []

    def assert_ready(self):
        pass

    def chat_structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_new_assignment_profile_is_generated_and_integrity_checked(tmp_path: Path, monkeypatch):
    assignment = tmp_path / "ПР2.docx"
    document = Document()
    document.add_paragraph("Практическая работа 2. Создайте таблицу и объясните результат.")
    document.save(assignment)
    criteria = CriteriaSet.model_validate({
        "version": 1,
        "criteria": [{
            "id": "pr2.result",
            "title": "Результат",
            "description": "Работа содержит требуемую таблицу и объяснение.",
            "max_score": 100,
            "allowed_scores": [0, 50, 100],
            "evidence_requirements": [{
                "description": "Текст и таблица в документе студента.",
                "kinds": ["paragraph", "table_cell"],
                "required": True,
            }],
        }],
    })
    stub = ProfileOllamaStub(criteria.model_dump_json())
    monkeypatch.setattr("student_ai_checker.auto_profile.OllamaClient", lambda **kwargs: stub)

    criteria_path, approval_path = generate_auto_profile(
        assignment,
        tmp_path / "profiles",
        settings=Settings(ollama_timeout_seconds=1),
    )

    assert criteria_path.name == "pr2.criteria.approved.json"
    assert approval_path.name == "pr2.approval.json"
    assert stub.calls[0]["images"] == []
    assert "недоверенные данные" in stub.calls[0]["system_prompt"]
    profile = resolve_assignment_profile(assignment, tmp_path / "profiles")
    loaded = CriteriaSet.model_validate_json(profile.criteria.read_text(encoding="utf-8"))
    approval = verify_approval(
        profile.approval,
        criteria=loaded,
        template_sha256=hashlib.sha256(assignment.read_bytes()).hexdigest(),
    )
    assert approval.approved_by == "automatic"
    assert approval.approval_mode == "automatic"
    assert json.loads(approval_path.read_text(encoding="utf-8"))["grade_scale"]["grade_5_min_percent"] == 70

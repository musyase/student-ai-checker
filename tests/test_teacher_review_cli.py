from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_review_cli_writes_separate_completed_result(tmp_path: Path):
    result_path = tmp_path / "result.json"
    criteria_path = tmp_path / "criteria.json"
    decisions_path = tmp_path / "teacher-decisions.json"
    output_path = tmp_path / "reviewed-result.json"

    result_path.write_text(json.dumps({
        "schema_version": 1,
        "results": [{
            "criterion_id": "c1", "title": "Критерий", "max_score": 2,
            "proposed_score": None, "status": "insufficient_evidence",
            "explanation": "Нужно решение.", "evidence_ids": ["DOC1:META:0001"],
            "student_feedback": "", "manual_review_required": True,
            "manual_review_reason": "Неоднозначно.", "status_label_ru": "Нужно уточнение",
        }],
        "summary": {"proposed_total": None, "teacher_confirmed": False},
    }, ensure_ascii=False), encoding="utf-8")
    criteria_path.write_text(json.dumps({
        "version": 1,
        "criteria": [{"id": "c1", "title": "Критерий", "description": "Описание",
                       "max_score": 2, "allowed_scores": [0, 1, 2]}],
    }, ensure_ascii=False), encoding="utf-8")
    decisions_path.write_text(json.dumps({
        "decisions": {"c1": {"action": "set_score", "score": 1, "comment": "Проверено."}},
    }, ensure_ascii=False), encoding="utf-8")

    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run([
        sys.executable, "-m", "student_ai_checker.cli", "review",
        "--result", str(result_path), "--criteria", str(criteria_path),
        "--decisions", str(decisions_path), "--output", str(output_path),
    ], env=environment, capture_output=True, text=True, encoding="utf-8", check=False)

    assert completed.returncode == 0, completed.stderr
    reviewed = json.loads(output_path.read_text(encoding="utf-8"))
    assert reviewed["teacher_review"]["status"] == "completed"
    assert reviewed["teacher_review"]["final_results"][0]["score"] == 1.0
    assert reviewed["results"][0]["proposed_score"] is None

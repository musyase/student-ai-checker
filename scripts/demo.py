"""Run a disposable UI demo with entirely synthetic student work.

No Ollama request is made. The example result is precomputed and is not a claim
about the grading quality of any model.
"""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from docx import Document

from student_ai_checker.documents.docx_extractor import DocxExtractor
from student_ai_checker.local_app import serve
from student_ai_checker.models import CriteriaSet, CriterionResult
from student_ai_checker.teacher_roster import write_teacher_roster_csv


CRITERIA = {
    "version": 1,
    "criteria": [
        {"id": "problem", "title": "Описание проблемы", "description": "Названа проблема и её причина.",
         "max_score": 3, "allowed_scores": [0, 1, 2, 3]},
        {"id": "steps", "title": "Шаги решения", "description": "Перечислены действия для решения.",
         "max_score": 2, "allowed_scores": [0, 1, 2]},
    ],
}

STUDENTS = [
    ("Иванова А.", "31ИСИП-1-1", "При запуске проекта возникла ошибка зависимости. Я обновила библиотеку и повторила сборку.", 3, 2, False),
    ("Петров М.", "31ИСИП-1-1", "Проект не запускался. Поменял настройки, после этого заработало.", 2, None, True),
    ("Сидорова Е.", "31ИСИП-1-2", "Ошибка появилась после обновления. Я очистила кэш и проверила запуск.", 1, 1, False),
]


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _docx(path: Path, title: str, text: str) -> None:
    document = Document()
    document.add_heading(title, 0)
    document.add_paragraph(text)
    document.save(path)


def create_demo(root: Path) -> Path:
    """Create a self-contained, fictional run and return its app state directory."""
    root.mkdir(parents=True, exist_ok=True)
    works = root / "works"
    works.mkdir()
    output = root / "results"
    output.mkdir()
    profiles = root / "profiles"
    profiles.mkdir()
    state = root / "state"
    state.mkdir()
    assignment = root / "ПР1.docx"
    _docx(assignment, "Практическая работа 1", "Опишите проблему и шаги её решения.")
    criteria = CriteriaSet.model_validate(CRITERIA)
    _json(profiles / "pr1.criteria.approved.json", CRITERIA)
    _json(profiles / "pr1.approval.json", {"status": "synthetic-demo-only"})

    cases = []
    manual_items = []
    for index, (name, group, answer, problem_score, steps_score, needs_review) in enumerate(STUDENTS, 1):
        filename = f"{group} {name} ПР1.docx"
        document_path = works / filename
        _docx(document_path, "Ответ на ПР1", answer)
        extracted = DocxExtractor().extract(document_path)
        answer_evidence = next(item for item in extracted.evidence if item.text == answer)
        rows = []
        for criterion, score in zip(criteria.criteria, (problem_score, steps_score)):
            unresolved = score is None
            row = CriterionResult.model_validate({
                "criterion_id": criterion.id,
                "title": criterion.title,
                "max_score": criterion.max_score,
                "proposed_score": score,
                "status": "insufficient_evidence" if unresolved else "completed",
                "explanation": "В синтетическом ответе есть соответствующий текст." if not unresolved else "Шаги решения описаны слишком общо.",
                "evidence_ids": [answer_evidence.id],
                "manual_review_required": unresolved,
                "manual_review_reason": "Уточнить по исходному документу." if unresolved else None,
            })
            row_json = row.model_dump(mode="json")
            row_json["status_label_ru"] = row.status.label_ru
            rows.append(row_json)
        total = problem_score + steps_score if steps_score is not None else None
        grade = 5 if total == 5 else 3 if total == 2 else None
        result_path = f"cases/{index:03d}.json"
        _json(output / result_path, {
            "results": rows,
            "resolved_evidence": [{"id": answer_evidence.id, "kind": "paragraph", "text": answer}],
            "summary": {
                "proposed_total": total,
                "scored_points": problem_score + (steps_score or 0),
                "max_score": 5,
                "proposed_grade": grade,
                "grade_is_estimate": needs_review,
                "automatic_final": not needs_review,
                "final_total": total,
                "final_grade": grade,
            },
        })
        cases.append({
            "index": index,
            "source_name": filename,
            "source_relative_path": filename,
            "result_relative_path": result_path,
            "group": group,
            "status": "manual_review" if needs_review else "completed",
            "manual_review_required": needs_review,
            "proposed_total": total,
            "scored_points": problem_score + (steps_score or 0),
            "max_score": 5,
            "proposed_grade": grade,
        })
        if needs_review:
            manual_items.append({"index": index, "criterion_id": "steps", "reason": "Шаги решения описаны слишком общо."})

    manifest = {"case_count": 3, "completed_count": 2, "manual_review_count": 1, "failed_count": 0, "cases": cases}
    manual = {"items": manual_items}
    _json(output / "batch-manifest.json", manifest)
    _json(output / "manual-review.json", manual)
    write_teacher_roster_csv(output, manifest, manual)
    with (output / "results.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["name", "status", "points"])
        writer.writerows((case["source_name"], case["status"], case["scored_points"]) for case in cases)
    _json(state / "runs.json", [{
        "id": "synthetic-demo",
        "demo": True,
        "profile": "ПР1 · синтетический пример",
        "profile_mode": "existing",
        "folder": str(works),
        "assignment": str(assignment),
        "profiles_root": str(profiles),
        "output_root": str(output),
        "model": "none (synthetic demo)",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }])
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Open a disposable Student AI Checker UI demo; Ollama is not used.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="student-ai-checker-demo-") as temporary:
        state = create_demo(Path(temporary))
        print("Синтетическое демо: оценки придуманы, Ollama не запускается.", flush=True)
        serve(port=args.port, state_dir=state, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

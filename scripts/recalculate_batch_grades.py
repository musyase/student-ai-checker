"""Recalculate grade fields in an existing batch without rerunning Ollama."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from student_ai_checker.grading.grade_scale import GradeScale
from student_ai_checker.teacher_roster import write_teacher_roster_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add/update 2–5 grades in an existing batch export."
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--approval", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output_root.resolve()
    approval = json.loads(args.approval.resolve().read_text(encoding="utf-8-sig"))
    grade_scale = GradeScale.model_validate(approval["grade_scale"]) if approval.get("grade_scale") else GradeScale.standard()
    manifest_path = output / "batch-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))

    for case in manifest["cases"]:
        result_path = output / case["result_relative_path"]
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        summary = result.get("summary", {})
        total = summary.get("proposed_total")
        maximum = summary.get("max_score")
        grade = grade_scale.grade_for(total, maximum) if total is not None and maximum else None
        summary["proposed_grade"] = grade
        result["summary"] = summary
        if isinstance(result.get("approval"), dict):
            result["approval"]["grade_scale"] = grade_scale.model_dump(mode="json")
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        case["proposed_grade"] = grade
        case["grade"] = summary.get("grade")

    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["grade_scale"] = grade_scale.model_dump(mode="json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manual_path = output / "manual-review.json"
    manual_payload = json.loads(manual_path.read_text(encoding="utf-8-sig")) if manual_path.exists() else {"items": []}
    write_teacher_roster_csv(output, manifest, manual_payload)

    columns = [
        "index", "group", "source_name", "source_relative_path", "status",
        "proposed_total", "scored_points", "max_score", "proposed_grade",
        "grade", "manual_review_required", "error",
    ]
    with (output / "results.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for case in manifest["cases"]:
            writer.writerow({column: case.get(column) for column in columns})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

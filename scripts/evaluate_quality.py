"""Run a local teacher-scored acceptance set through the real CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from student_ai_checker.quality_evaluation import (
    compare_case, summarize_reports, validate_expected_case,
)


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Сравнить локальные проверки с эталонными оценками преподавателя.")
    parser.add_argument("--dataset", required=True, type=Path, help="Папка с подпапками case/student.docx и expected.json")
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--criteria", required=True, type=Path)
    parser.add_argument("--approval", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Итоговый JSON-отчёт; существующий файл не перезаписывается")
    parser.add_argument("--runs-dir", type=Path, help="Папка с отдельными result.json/stdout/stderr по кейсам")
    parser.add_argument("--model")
    parser.add_argument("--ollama-url")
    return parser


def run(args: argparse.Namespace) -> int:
    repo_root = REPO_ROOT
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    runs_dir = (args.runs_dir or output.with_name(output.stem + "-runs")).resolve()
    for path in (dataset, args.template, args.criteria, args.approval):
        if not path.exists():
            raise ValueError(f"path does not exist: {path}")
    if not dataset.is_dir():
        raise ValueError(f"dataset is not a directory: {dataset}")
    if output.exists() or runs_dir.exists():
        raise ValueError("output or runs directory already exists; choose a fresh path to preserve prior results")

    cases = sorted(path for path in dataset.iterdir() if path.is_dir())
    if not cases:
        raise ValueError("dataset contains no case directories")
    criteria_data = _json(args.criteria)
    criteria_items = criteria_data.get("criteria")
    if not isinstance(criteria_items, list) or not criteria_items:
        raise ValueError("criteria JSON must contain a non-empty criteria list")
    allowed_scores: dict[str, Any] = {}
    for item in criteria_items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("criteria JSON contains an invalid criterion")
        criterion_id = item["id"]
        scores = item.get("allowed_scores")
        if criterion_id in allowed_scores or not isinstance(scores, list) or not scores:
            raise ValueError(f"criteria JSON contains invalid allowed_scores for {criterion_id}")
        allowed_scores[criterion_id] = scores
    output.parent.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True)
    env = os.environ.copy()
    source_root = str(repo_root / "src")
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
    report_cases: list[dict[str, Any]] = []

    for case_dir in cases:
        document = case_dir / "student.docx"
        expected_path = case_dir / "expected.json"
        case_id = case_dir.name
        if not document.is_file() or not expected_path.is_file():
            report_cases.append({"case_id": case_id, "passed": False, "error": "student.docx or expected.json is missing"})
            continue
        expected = _json(expected_path)
        if expected.get("case_id") != case_id:
            report_cases.append({
                "case_id": case_id,
                "passed": False,
                "error": "expected.json case_id does not match its directory name",
            })
            continue
        try:
            validate_expected_case(expected, allowed_scores)
        except ValueError as exc:
            report_cases.append({
                "case_id": case_id,
                "passed": False,
                "error": f"invalid expected.json: {exc}",
            })
            continue
        result_path = runs_dir / f"{case_id}.result.json"
        command = [
            sys.executable,
            "-m",
            "student_ai_checker.cli",
            "check",
            "--document",
            str(document),
            "--template",
            str(args.template.resolve()),
            "--criteria",
            str(args.criteria.resolve()),
            "--approval",
            str(args.approval.resolve()),
            "--checked-scoring",
            "--output",
            str(result_path),
        ]
        if args.model:
            command.extend(["--model", args.model])
        if args.ollama_url:
            command.extend(["--ollama-url", args.ollama_url])
        completed = subprocess.run(command, cwd=repo_root, env=env, capture_output=True, text=True, encoding="utf-8")
        (runs_dir / f"{case_id}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (runs_dir / f"{case_id}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0 or not result_path.exists():
            report_cases.append({
                "case_id": case_id,
                "passed": False,
                "error": "automatic CLI check failed",
                "returncode": completed.returncode,
            })
            continue
        comparison = compare_case(expected, _json(result_path))
        report_cases.append(comparison)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "model": args.model,
        "case_count": len(report_cases),
        "passed_count": sum(bool(item.get("passed")) for item in report_cases),
        "passed": bool(report_cases) and all(bool(item.get("passed")) for item in report_cases),
        "summary": summarize_reports(report_cases),
        "cases": report_cases,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Отчёт сохранён: {output}")
    print(f"Пройдено кейсов: {report['passed_count']}/{report['case_count']}")
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except (OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

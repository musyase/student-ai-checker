from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from student_ai_checker.teacher_roster import write_teacher_roster_csv


BATCH_CACHE_VERSION = 3
ASSIGNMENT_MARKER_RE = re.compile(r"(?:практическ\w*|пр\s*[._№-]?\s*0*\d+)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run checked scoring over student DOCX files.")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--criteria", required=True, type=Path)
    parser.add_argument("--approval", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--practice", default="1")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=2,
                        help="Number of independent CLI workers (default: 2).")
    parser.add_argument("--cache-root", type=Path, default=None,
                        help="Reusable result cache; defaults beside source-root.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--generic-scoring",
        action="store_true",
        help="Use the general evidence-based checker instead of the PR1 checked pilot.",
    )
    parser.add_argument(
        "--automatic",
        action="store_true",
        help="Finalize complete scores without teacher confirmation.",
    )
    return parser.parse_args()


def practice_pattern(practice: str) -> re.Pattern[str]:
    normalized = str(practice).strip()
    if not normalized.isdigit() or int(normalized) < 1:
        raise SystemExit(f"Некорректный номер практической работы: {practice!r}.")
    number = str(int(normalized))
    return re.compile(
        rf"(?:практическ\w*\s*(?:работ\w*\s*)?(?:№\s*)?0?{number}|"
        # Student names are often written as ``ИвановПР2`` without a
        # separator.  The old look-behind rejected those valid filenames.
        rf"пр\s*[._№-]?\s*0?{number}(?![0-9])|"
        rf"п\s*0?{number}(?![0-9]))",
        re.IGNORECASE,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_manifest(path: Path, source: Path, practice: str, model: str,
                   records: list[dict], *, workers: int, cache_root: Path,
                   discovered_count: int | None = None,
                   unclassified_files: list[str] | None = None) -> None:
    payload = {
        "schema_version": 2,
        "generated_at": utc_now(),
        "source_root": str(source),
        "practice": f"PR{practice}",
        "model": model,
        "workers": workers,
        "cache_root": str(cache_root),
        "cache_version": BATCH_CACHE_VERSION,
        "discovery": {
            "matched_count": discovered_count if discovered_count is not None else len(records),
            "unclassified_count": len(unclassified_files or []),
            "unclassified_files": unclassified_files or [],
        },
        "case_count": discovered_count if discovered_count is not None else len(records),
        "completed_count": sum(record["status"] == "completed" for record in records),
        "manual_review_count": sum(record["status"] == "manual_review" for record in records),
        "failed_count": sum(record["status"] == "failed" for record in records),
        "cases": records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_batch_reports(path.parent, payload)


def write_batch_reports(output: Path, manifest: dict) -> None:
    """Create lightweight teacher-facing exports alongside the manifest."""
    rows = []
    manual_items = []
    for case in manifest["cases"]:
        result_path = output / case["result_relative_path"]
        result_payload = None
        if result_path.exists():
            try:
                result_payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result_payload = None
        rows.append({
            "index": case["index"],
            "group": case["group"],
            "source_name": case["source_name"],
            "source_relative_path": case["source_relative_path"],
            "status": case["status"],
            "proposed_total": case.get("proposed_total"),
            "scored_points": case.get("scored_points"),
            "max_score": case.get("max_score"),
            "proposed_grade": case.get("proposed_grade"),
            "estimated_grade": case.get("estimated_grade"),
            "grade_is_estimate": case.get("grade_is_estimate"),
            "grade": case.get("grade"),
            "manual_review_required": case.get("manual_review_required"),
            "error": case.get("error"),
        })
        if case["status"] == "manual_review":
            manual_items.append({
                "index": case["index"], "source_name": case["source_name"],
                "source_relative_path": case["source_relative_path"],
                "kind": "automatic_check_incomplete",
                "reason": "Автоматическая проверка не завершилась по всем критериям; проверьте работу вручную.",
            })
        if result_payload:
            for result in result_payload.get("results", []):
                if result.get("manual_review_required"):
                    manual_items.append({
                        "index": case["index"], "source_name": case["source_name"],
                        "source_relative_path": case["source_relative_path"],
                        "kind": "criterion_review", "criterion_id": result.get("criterion_id"),
                        "status": result.get("status"),
                        "reason": result.get("manual_review_reason"),
                        "result_relative_path": case["result_relative_path"],
                    })
    with (output / "results.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["index"])
        writer.writeheader()
        writer.writerows(rows)
    (output / "manual-review.json").write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": utc_now(),
            "items": manual_items,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_teacher_roster_csv(output, manifest, {"items": manual_items})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cache_key(document: Path, template: Path, criteria: Path, approval: Path,
              model: str, *, generic_scoring: bool = False,
              automatic: bool = False) -> str:
    payload = {
        "version": BATCH_CACHE_VERSION,
        "document": file_sha256(document),
        "template": file_sha256(template),
        "criteria": file_sha256(criteria),
        "approval": file_sha256(approval),
        "model": model,
        "generic_scoring": generic_scoring,
        "automatic": automatic,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def result_record(index: int, file_path: Path, source: Path, output: Path,
                  result_path: Path, key: str, practice: str) -> dict:
    return {
        "index": index,
        "source_relative_path": str(file_path.relative_to(source)),
        "group": file_path.parent.name,
        "source_name": file_path.name,
        "practice": f"PR{practice}",
        "status": "failed",
        "result_relative_path": str(result_path.relative_to(output)),
        "cache_key": key,
        "cache_hit": False,
        "proposed_total": None,
        "scored_points": None,
        "max_score": None,
        "proposed_grade": None,
        "estimated_grade": None,
        "grade_is_estimate": False,
        "grade": None,
        "manual_review_required": None,
        "error": None,
    }


def fill_record_from_result(record: dict, result_path: Path) -> dict:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    summary = result["summary"]
    record.update({
        "status": "completed",
        "proposed_total": summary.get("proposed_total"),
        "scored_points": summary.get("scored_points"),
        "max_score": summary.get("max_score"),
        "proposed_grade": summary.get("proposed_grade"),
        "estimated_grade": summary.get("estimated_grade"),
        "grade_is_estimate": summary.get("grade_is_estimate", False),
        "grade": summary.get("grade"),
        "manual_review_required": summary.get("manual_review_required"),
        "error": None,
    })
    return record


def run_case(index: int, file_path: Path, *, source: Path, template: Path,
             criteria: Path, approval: Path, output: Path, cache_root: Path,
             repo_root: Path, env: dict[str, str], practice: str, model: str,
             generic_scoring: bool, automatic: bool) -> dict:
    group_dir = output / file_path.parent.name
    group_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{index:03d}"
    result_path = group_dir / f"{stamp}.result.json"
    stdout_path = group_dir / f"{stamp}.stdout.txt"
    stderr_path = group_dir / f"{stamp}.stderr.txt"
    key = cache_key(
        file_path, template, criteria, approval, model,
        generic_scoring=generic_scoring, automatic=automatic,
    )
    record = result_record(index, file_path, source, output, result_path, key, practice)
    cached_result = cache_root / f"{key}.result.json"
    if cached_result.exists():
        shutil.copyfile(cached_result, result_path)
        record["cache_hit"] = True
        return fill_record_from_result(record, result_path)

    command = [
        sys.executable,
        *(["check"] if getattr(sys, "frozen", False) else ["-m", "student_ai_checker.cli"]),
        *( [] if getattr(sys, "frozen", False) else ["check"] ),
        "--document", str(file_path), "--template", str(template),
        "--criteria", str(criteria), "--approval", str(approval),
        *( [] if generic_scoring else ["--checked-scoring"] ),
        "--output", str(result_path), "--model", model,
        *( ["--automatic"] if automatic else [] ),
    ]
    try:
        completed = subprocess.run(
            command, cwd=repo_root, env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        if completed.returncode == 0 and result_path.exists():
            cache_root.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(result_path, cached_result)
            return fill_record_from_result(record, result_path)
        detail = completed.stderr.strip() or completed.stdout.strip() or "no CLI output"
        record["error"] = f"CLI exited with code {completed.returncode}: {detail[-1000:]}"
        if completed.returncode == 2:
            record["status"] = "manual_review"
            record["manual_review_required"] = True
    except Exception as exc:  # keep the batch moving and preserve the failure reason
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def main() -> int:
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")
    repo_root = Path(__file__).resolve().parents[1]
    source = args.source_root.resolve()
    template = args.template.resolve()
    criteria = args.criteria.resolve()
    approval = args.approval.resolve()
    output = args.output_root.resolve()
    cache_root = (args.cache_root.resolve() if args.cache_root
                  else source.parent / "_student-ai-checker-cache")

    for required in (source, template, criteria, approval):
        if not required.exists():
            raise SystemExit(f"Path not found: {required}")
    manifest_path = output / "batch-manifest.json"
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise SystemExit(f"Output directory already contains files: {output}")
    output.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    pattern = practice_pattern(args.practice)
    files = sorted(
        (path for path in source.rglob("*.docx")
         if path.is_file() and path.resolve() != template
         and pattern.search(path.name)),
        key=lambda path: str(path).casefold(),
    )
    if args.limit is not None:
        files = files[: args.limit]
    if not files:
        raise SystemExit(f"No explicitly named student DOCX files found for PR{args.practice}.")
    all_student_docx = sorted(
        (path for path in source.rglob("*.docx")
         if path.is_file() and path.resolve() != template and path.parent != source),
        key=lambda path: str(path).casefold(),
    )
    unclassified_files = [
        str(path.relative_to(source)) for path in all_student_docx
        if not ASSIGNMENT_MARKER_RE.search(path.name)
    ]

    expected_keys = {
        index: cache_key(
            file_path, template, criteria, approval, args.model,
            generic_scoring=args.generic_scoring, automatic=args.automatic,
        )
        for index, file_path in enumerate(files, 1)
    }
    records_by_index: dict[int, dict] = {}
    if args.resume:
        if not manifest_path.exists():
            raise SystemExit(f"Cannot resume without manifest: {manifest_path}")
        existing = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        for record in existing.get("cases", []):
            index = int(record.get("index", 0))
            if index in expected_keys and record.get("cache_key") == expected_keys[index]:
                if record.get("status") == "failed" and str(record.get("error", "")).startswith("CLI exited with code 2:"):
                    record["status"] = "manual_review"
                    record["manual_review_required"] = True
                records_by_index[index] = record

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(repo_root / "src") + (os.pathsep + current_pythonpath if current_pythonpath else "")
    pending = [
        (index, file_path) for index, file_path in enumerate(files, 1)
        if records_by_index.get(index, {}).get("status") in {"completed", "manual_review"}
        and (output / records_by_index[index]["result_relative_path"]).exists()
    ]
    reused = len(pending)
    pending = [(index, file_path) for index, file_path in enumerate(files, 1)
               if index not in records_by_index or index not in {i for i, _ in pending}]
    if reused:
        print(f"Reused {reused} cached batch records.", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures: dict[Future[dict], tuple[int, Path]] = {
            pool.submit(
                run_case, index, file_path, source=source, template=template,
                criteria=criteria, approval=approval, output=output,
                cache_root=cache_root, repo_root=repo_root, env=env,
                practice=args.practice, model=args.model,
                generic_scoring=args.generic_scoring,
                automatic=args.automatic,
            ): (index, file_path)
            for index, file_path in pending
        }
        for future in as_completed(futures):
            index, file_path = futures[future]
            record = future.result()
            records_by_index[index] = record
            state = "cache" if record.get("cache_hit") else record["status"]
            print(f"[{index}/{len(files)}] {state}: {file_path.relative_to(source)}", flush=True)
            write_manifest(
                manifest_path, source, args.practice, args.model,
                [records_by_index[i] for i in sorted(records_by_index)],
                workers=args.workers, cache_root=cache_root,
                discovered_count=len(files), unclassified_files=unclassified_files,
            )

    records = [records_by_index[i] for i in sorted(records_by_index)]
    completed_count = sum(record["status"] == "completed" for record in records)
    failed_count = sum(record["status"] == "failed" for record in records)
    write_manifest(manifest_path, source, args.practice, args.model, records,
                   workers=args.workers, cache_root=cache_root,
                   discovered_count=len(files), unclassified_files=unclassified_files)
    print(f"Completed: {completed_count}/{len(files)}; failed: {failed_count}; workers: {args.workers}", flush=True)
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

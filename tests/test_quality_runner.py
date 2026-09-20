from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_quality_runner_rejects_invalid_labels_before_starting_cli(tmp_path: Path):
    repo = Path(__file__).parents[1]
    dataset = tmp_path / "acceptance"
    case = dataset / "work-001"
    case.mkdir(parents=True)
    (case / "student.docx").write_bytes(b"not used because labels are invalid")
    (case / "expected.json").write_text(json.dumps({
        "case_id": "work-001",
        "criteria": {"unknown": {"score": 1}},
    }), encoding="utf-8")
    output = tmp_path / "quality-report.json"

    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run([
        sys.executable, "scripts/evaluate_quality.py",
        "--dataset", str(dataset),
        "--template", str(repo / "examples" / "criteria.json"),
        "--criteria", str(repo / "examples" / "pr1.criteria.approved.json"),
        "--approval", str(repo / "examples" / "pr1.approval.json"),
        "--output", str(output),
    ], cwd=repo, env=environment, capture_output=True, text=True, encoding="utf-8", check=False)

    assert completed.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert "invalid expected.json" in report["cases"][0]["error"]
    assert not list(output.parent.glob("quality-report-runs/*result.json"))

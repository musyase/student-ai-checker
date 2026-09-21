"""Local teacher application server.

The first desktop-ready surface is a dependency-light local web app. A future
Tauri shell can start the same server as a sidecar and load the same assets.
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from student_ai_checker.batch import resolve_assignment_profile
from student_ai_checker.auto_profile import generate_auto_profile
from student_ai_checker.config import Settings
from student_ai_checker.documents.docx_extractor import sha256_file
from student_ai_checker.exceptions import StudentAICheckerError
from student_ai_checker.grading.approval import verify_approval
from student_ai_checker.teacher_review import build_teacher_review
from student_ai_checker.teacher_roster import build_teacher_roster


APP_NAME = "Student AI Checker"
EXPORTS = {
    "results.csv": "Полные результаты",
    "teacher-roster.csv": "Реестр CSV",
    "groups-summary.csv": "Сводка по группам",
}


def runtime_root() -> Path:
    """Return the project root both from source and a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_file(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return fallback


class LocalApp:
    def __init__(self, *, repo_root: Path, state_dir: Path, settings: Settings) -> None:
        self.repo_root = repo_root.resolve()
        self.state_dir = state_dir.resolve()
        self.settings = settings
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.runs_file = self.state_dir / "runs.json"
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()

    def _load_runs(self) -> list[dict[str, Any]]:
        value = json_file(self.runs_file, [])
        return value if isinstance(value, list) else []

    def _save_runs(self, runs: list[dict[str, Any]]) -> None:
        self.runs_file.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = self._load_runs()
            for run in runs:
                run["status"] = self.run_status(run)
            self._save_runs(runs)
            return sorted(runs, key=lambda item: item.get("created_at", ""), reverse=True)

    def get_run(self, run_id: str) -> dict[str, Any]:
        for run in self.list_runs():
            if run.get("id") == run_id:
                return run
        raise StudentAICheckerError("Проверка не найдена.")

    def manifest(self, run: dict[str, Any]) -> dict[str, Any]:
        return json_file(Path(run["output_root"]) / "batch-manifest.json", {"cases": []})

    def run_status(self, run: dict[str, Any]) -> str:
        process = self._processes.get(str(run["id"]))
        if process is not None and process.poll() is None:
            return "running"
        manifest = self.manifest(run)
        if manifest.get("case_count") and manifest.get("completed_count", 0) + manifest.get("manual_review_count", 0) + manifest.get("failed_count", 0) >= manifest.get("case_count", 0):
            return "completed" if not manifest.get("failed_count") else "failed"
        return "pending" if not process else "failed"

    def run_summary(self, run: dict[str, Any]) -> dict[str, Any]:
        manifest = self.manifest(run)
        return {
            "id": run["id"],
            "profile": run.get("profile"),
            "profile_mode": run.get("profile_mode", "existing"),
            "folder": run.get("folder"),
            "assignment": run.get("assignment"),
            "output_root": run.get("output_root"),
            "status": self.run_status(run),
            "case_count": manifest.get("case_count", 0),
            "completed_count": manifest.get("completed_count", 0),
            "manual_review_count": manifest.get("manual_review_count", 0),
            "failed_count": manifest.get("failed_count", 0),
            "created_at": run.get("created_at"),
        }

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        folder = Path(str(payload.get("folder", ""))).expanduser().resolve()
        assignment = Path(str(payload.get("assignment", ""))).expanduser().resolve()
        explicit_profiles_root = bool(payload.get("profiles_root"))
        profiles_root = Path(str(payload.get("profiles_root") or self.repo_root / "examples")).expanduser().resolve()
        workers = int(payload.get("workers") or 2)
        model = str(payload.get("model") or self.settings.ollama_model)
        if not folder.is_dir():
            raise StudentAICheckerError(f"Папка работ не найдена: {folder}")
        if not assignment.is_file():
            raise StudentAICheckerError(f"Файл задания не найден: {assignment}")
        if workers < 1 or workers > 8:
            raise StudentAICheckerError("Количество потоков должно быть от 1 до 8.")
        profile_generated = False
        try:
            profile = resolve_assignment_profile(assignment, profiles_root)
        except StudentAICheckerError:
            if explicit_profiles_root:
                raise
            profiles_root = self.state_dir / "profiles"
            generate_auto_profile(
                assignment,
                profiles_root,
                settings=self.settings,
                model=model,
            )
            profile = resolve_assignment_profile(assignment, profiles_root)
            profile_generated = True
        if not explicit_profiles_root and self._automatic_profile_is_stale(profile, assignment):
            profiles_root = self.state_dir / "profiles"
            generate_auto_profile(
                assignment,
                profiles_root,
                settings=self.settings,
                model=model,
            )
            profile = resolve_assignment_profile(assignment, profiles_root)
            profile_generated = True
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        output = folder.parent / "_student-ai-checker-results" / f"{profile.key}-{run_id}"
        run = {
            "id": run_id,
            "folder": str(folder),
            "assignment": str(assignment),
            "profiles_root": str(profiles_root),
            "output_root": str(output),
            "profile": profile.key,
            "profile_mode": "automatic" if profile_generated else "existing",
            "practice": profile.practice,
            "model": model,
            "workers": workers,
            "created_at": utc_now(),
            "status": "pending",
        }
        with self._lock:
            runs = self._load_runs()
            runs.append(run)
            self._save_runs(runs)
        self._start_run(run)
        return self.run_summary(run)

    def _automatic_profile_is_stale(self, profile, assignment: Path) -> bool:
        """Regenerate only profiles explicitly created by the automatic flow."""
        try:
            approval = json_file(Path(profile.approval), {})
            if not isinstance(approval, dict) or approval.get("approval_mode") != "automatic":
                return False
            criteria = self._load_criteria(profile.criteria)
            verify_approval(
                profile.approval,
                criteria=criteria,
                template_sha256=sha256_file(assignment),
            )
            return False
        except StudentAICheckerError:
            return True

    def _start_run(self, run: dict[str, Any]) -> None:
        profile = resolve_assignment_profile(Path(run["assignment"]), Path(run["profiles_root"]))
        runner = self.repo_root / "scripts" / "run_checked_batch.py"
        command = [
            sys.executable,
            *( ["batch"] if getattr(sys, "frozen", False) else [str(runner)] ),
            "--source-root", run["folder"],
            "--template", run["assignment"],
            "--criteria", str(profile.criteria),
            "--approval", str(profile.approval),
            "--output-root", run["output_root"],
            "--practice", profile.practice,
            "--model", run["model"],
            "--workers", str(run["workers"]),
            "--resume",
        ]
        if run.get("profile_mode") == "automatic":
            command.append("--generic-scoring")
        command.append("--automatic")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = str(self.repo_root / "src") + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        output = Path(run["output_root"])
        output.mkdir(parents=True, exist_ok=True)
        log_path = output / "app-run.log"
        log = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(command, cwd=self.repo_root, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        self._processes[run["id"]] = process

        def wait_and_close() -> None:
            try:
                process.wait()
            finally:
                log.close()

        threading.Thread(target=wait_and_close, name=f"student-ai-checker-{run['id']}", daemon=True).start()

    def roster(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        manifest = self.manifest(run)
        manual = json_file(Path(run["output_root"]) / "manual-review.json", {"items": []})
        rows = build_teacher_roster(manifest, manual)
        for row in rows:
            review_path = Path(run["output_root"]) / Path(str(next(
                case["result_relative_path"] for case in manifest.get("cases", [])
                if int(case["index"]) == row["Индекс"]
            ))).with_suffix(".reviewed-result.json")
            review = json_file(review_path, {})
            summary = review.get("summary", {}) if isinstance(review, dict) else {}
            if review.get("teacher_review", {}).get("status") == "completed":
                row["Итоговые баллы"] = summary.get("final_total")
                row["Оценка"] = summary.get("final_grade")
                row["Статус"] = "Подтверждено"
        return rows

    def student(self, run: dict[str, Any], index: int) -> dict[str, Any]:
        manifest = self.manifest(run)
        case = next((item for item in manifest.get("cases", []) if int(item["index"]) == index), None)
        if case is None:
            raise StudentAICheckerError("Работа студента не найдена.")
        result_path = Path(run["output_root"]) / case["result_relative_path"]
        result = json_file(result_path, {})
        reviewed_path = result_path.with_suffix(".reviewed-result.json")
        reviewed = json_file(reviewed_path, None)
        criteria = self._load_criteria(resolve_assignment_profile(
            Path(run["assignment"]), Path(run["profiles_root"])
        ).criteria)
        return {
            "case": case,
            "result": result,
            "review": reviewed,
            "criteria": [
                {"id": item.id, "allowed_scores": item.allowed_scores, "max_score": item.max_score}
                for item in criteria.criteria
            ],
            "document_url": f"/api/runs/{run['id']}/students/{index}/file",
        }

    def save_review(self, run: dict[str, Any], index: int, decisions: dict[str, Any]) -> dict[str, Any]:
        manifest = self.manifest(run)
        case = next((item for item in manifest.get("cases", []) if int(item["index"]) == index), None)
        if case is None:
            raise StudentAICheckerError("Работа студента не найдена.")
        result_path = Path(run["output_root"]) / case["result_relative_path"]
        automatic = json_file(result_path, None)
        if not isinstance(automatic, dict):
            raise StudentAICheckerError("Автоматический результат ещё не готов.")
        criteria_path = resolve_assignment_profile(Path(run["assignment"]), Path(run["profiles_root"])).criteria
        criteria = self._load_criteria(criteria_path)
        try:
            reviewed = build_teacher_review(automatic, criteria, {"decisions": decisions})
        except ValueError as exc:
            raise StudentAICheckerError(f"Решение не сохранено: {exc}") from exc
        review_path = result_path.with_suffix(".reviewed-result.json")
        review_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
        return reviewed

    @staticmethod
    def _load_criteria(path: Path):
        from student_ai_checker.cli import _load_criteria
        return _load_criteria(path)

    def health(self) -> dict[str, Any]:
        model = self.settings.ollama_model
        try:
            response = httpx.get(self.settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=2)
            response.raise_for_status()
            names = [item.get("name") for item in response.json().get("models", [])]
            return {"ollama": True, "model": model, "model_available": model in names, "models": names}
        except (httpx.HTTPError, ValueError, KeyError):
            return {"ollama": False, "model": model, "model_available": False, "models": []}

    @staticmethod
    def pick_path(kind: str) -> str:
        """Use the OS picker when the local app is running with a desktop session."""
        if kind not in {"folder", "file"}:
            raise StudentAICheckerError("Неизвестный тип выбора пути.")
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = (
                filedialog.askdirectory(title="Выберите папку с работами")
                if kind == "folder" else
                filedialog.askopenfilename(
                    title="Выберите файл задания",
                    filetypes=[("Документы Word", "*.docx"), ("Все файлы", "*.*")],
                )
            )
            root.destroy()
            return selected or ""
        except Exception as exc:
            raise StudentAICheckerError("Системный выбор пути недоступен. Введите путь вручную.") from exc


def create_handler(app: LocalApp):
    web_root = app.repo_root / "web"

    class Handler(BaseHTTPRequestHandler):
        server_version = "StudentAIChecker/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_cors_headers(self) -> None:
            # Tauri serves the frontend from its own origin while the Python
            # sidecar is bound to loopback. No credentials are used, so a
            # wildcard keeps the local desktop shell portable across OSes.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def error(self, message: str, status: int = 400) -> None:
            self.send_json({"error": message}, status)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/api/health":
                    return self.send_json(app.health())
                if path == "/api/runs":
                    return self.send_json({"runs": [app.run_summary(run) for run in app.list_runs()]})
                if path.startswith("/api/runs/"):
                    parts = path.strip("/").split("/")
                    if len(parts) < 3:
                        raise StudentAICheckerError("Некорректный адрес проверки.")
                    run = app.get_run(parts[2])
                    if len(parts) == 3:
                        return self.send_json(app.run_summary(run))
                    if parts[3] == "roster":
                        return self.send_json({"rows": app.roster(run)})
                    if parts[3] == "exports" and len(parts) == 5:
                        return self.send_export(run, parts[4])
                    if parts[3] == "students" and len(parts) >= 5:
                        index = int(parts[4])
                        if len(parts) == 5:
                            return self.send_json(app.student(run, index))
                        if len(parts) == 6 and parts[5] == "file":
                            return self.send_file(run, index)
                return self.send_static(path)
            except (StudentAICheckerError, ValueError) as exc:
                self.error(str(exc), 404 if "не найд" in str(exc).lower() else 400)

        def send_file(self, run: dict[str, Any], index: int) -> None:
            manifest = app.manifest(run)
            case = next((item for item in manifest.get("cases", []) if int(item["index"]) == index), None)
            if not case:
                return self.error("Файл не найден.", 404)
            path = Path(run["folder"]) / case["source_relative_path"]
            if not path.is_file():
                return self.error("Исходный DOCX не найден.", 404)
            data = path.read_bytes()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_export(self, run: dict[str, Any], filename: str) -> None:
            if filename not in EXPORTS:
                return self.error("Экспорт не найден.", 404)
            path = (Path(run["output_root"]) / filename).resolve()
            output_root = Path(run["output_root"]).resolve()
            if output_root not in path.parents or not path.is_file():
                return self.error("Экспорт ещё не готов.", 404)
            data = path.read_bytes()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_static(self, path: str) -> None:
            relative = "index.html" if path in {"", "/"} else path.lstrip("/")
            candidate = (web_root / relative).resolve()
            if web_root not in candidate.parents and candidate != web_root:
                return self.error("Файл не найден.", 404)
            if not candidate.is_file():
                return self.error("Файл не найден.", 404)
            data = candidate.read_bytes()
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                parsed = urlparse(self.path)
                parts = unquote(parsed.path).strip("/").split("/")
                if parsed.path == "/api/runs":
                    return self.send_json(app.create_run(payload), 202)
                if parsed.path == "/api/pick":
                    return self.send_json({"path": app.pick_path(str(payload.get("kind", "")))})
                if len(parts) == 6 and parts[:3] == ["api", "runs", parts[2]] and parts[3] == "students" and parts[5] == "review":
                    run = app.get_run(parts[2])
                    reviewed = app.save_review(run, int(parts[4]), payload.get("decisions", {}))
                    return self.send_json({"review": reviewed["teacher_review"], "summary": reviewed["summary"]})
                self.error("Маршрут не найден.", 404)
            except (StudentAICheckerError, ValueError, json.JSONDecodeError) as exc:
                self.error(str(exc), 400)

    return Handler


def serve(*, host: str = "127.0.0.1", port: int = 8765, state_dir: Path | None = None, open_browser: bool = True) -> None:
    repo_root = runtime_root()
    settings = Settings.from_env()
    app = LocalApp(repo_root=repo_root, state_dir=state_dir or (Path.home() / ".student-ai-checker"), settings=settings)
    server = ThreadingHTTPServer((host, port), create_handler(app))
    url = f"http://{host}:{server.server_port}/"
    print(f"{APP_NAME}: {url}", flush=True)
    print("Для остановки нажмите Ctrl+C.", flush=True)
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

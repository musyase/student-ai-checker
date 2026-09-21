from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from http.server import ThreadingHTTPServer

from student_ai_checker.config import Settings
from student_ai_checker.local_app import LocalApp, create_handler
import student_ai_checker.local_app as local_app_module


def running_app(tmp_path: Path):
    app = LocalApp(
        repo_root=Path(__file__).parents[1],
        state_dir=tmp_path / "state",
        settings=Settings.from_env(),
    )
    app.health = lambda: {"ollama": True, "model": "test", "model_available": True, "models": ["test"]}
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(app))
    server.student_app = app
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def request(server, method: str, path: str, payload=None):
    connection = HTTPConnection("127.0.0.1", server.server_port)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    connection.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def test_local_app_serves_ui_and_run_list(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        status, html = request(server, "GET", "/")
        assert status == 200 and b"Student AI Checker" in html
        status, payload = request(server, "GET", "/api/runs")
        assert status == 200 and json.loads(payload) == {"runs": []}
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_local_app_rejects_invalid_run_without_starting_process(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        status, payload = request(server, "POST", "/api/runs", {"folder": "missing", "assignment": "missing.docx"})
        assert status == 400
        assert "не найдена" in json.loads(payload)["error"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_local_app_downloads_teacher_exports(tmp_path):
    server, thread = running_app(tmp_path)
    output = tmp_path / "run"
    output.mkdir()
    (output / "teacher-roster.csv").write_text("Группа,Фамилия\nИВТ-1,Иванов\n", encoding="utf-8-sig")
    app = server.student_app
    app._save_runs([{"id": "run-1", "output_root": str(output), "folder": str(tmp_path)}])
    try:
        status, payload = request(server, "GET", "/api/runs/run-1/exports/teacher-roster.csv")
        assert status == 200
        assert "Иванов" in payload.decode("utf-8-sig")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_local_app_allows_tauri_preflight(tmp_path):
    server, thread = running_app(tmp_path)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request(
            "OPTIONS",
            "/api/health",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 204
        assert response.getheader("Access-Control-Allow-Origin") == "*"
        assert "POST" in response.getheader("Access-Control-Allow-Methods", "")
        connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_new_run_does_not_pass_resume_without_a_manifest(tmp_path, monkeypatch):
    app = LocalApp(
        repo_root=Path(__file__).parents[1],
        state_dir=tmp_path / "state",
        settings=Settings.from_env(),
    )
    output = tmp_path / "output"
    captured = {}

    class DummyProcess:
        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return DummyProcess()

    monkeypatch.setattr(local_app_module.subprocess, "Popen", fake_popen)
    run = {
        "id": "fresh-run",
        "assignment": str(tmp_path / "ПР1.docx"),
        "profiles_root": str(Path(__file__).parents[1] / "examples"),
        "folder": str(tmp_path),
        "output_root": str(output),
        "practice": "1",
        "model": "test",
        "workers": 1,
        "profile_mode": "existing",
    }
    app._start_run(run)
    assert "--resume" not in captured["command"]
    assert not output.exists() or not list(output.iterdir())


def test_launcher_log_does_not_make_fresh_output_nonempty(tmp_path, monkeypatch):
    app = LocalApp(
        repo_root=Path(__file__).parents[1],
        state_dir=tmp_path / "state",
        settings=Settings.from_env(),
    )
    output = tmp_path / "output"

    class DummyProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(local_app_module.subprocess, "Popen", lambda *args, **kwargs: DummyProcess())
    run = {
        "id": "fresh-log-run",
        "assignment": str(tmp_path / "ПР1.docx"),
        "profiles_root": str(Path(__file__).parents[1] / "examples"),
        "folder": str(tmp_path),
        "output_root": str(output),
        "practice": "1",
        "model": "test",
        "workers": 1,
        "profile_mode": "existing",
    }
    app._start_run(run)
    assert not output.exists() or not list(output.iterdir())
    assert app._run_log_path(run).is_file()


def test_stale_no_manifest_run_is_not_reported_as_pending(tmp_path):
    app = LocalApp(
        repo_root=Path(__file__).parents[1],
        state_dir=tmp_path / "state",
        settings=Settings.from_env(),
    )
    output = tmp_path / "output"
    output.mkdir()
    (output / "app-run.log").write_text("Cannot resume without manifest", encoding="utf-8")
    run = {"id": "stale", "output_root": str(output)}
    assert app.run_status(run) == "failed"

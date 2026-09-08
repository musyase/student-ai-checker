from __future__ import annotations

from student_ai_checker.config import Settings


def test_settings_from_env_uses_defaults(monkeypatch) -> None:
    monkeypatch.delenv("STUDENT_AI_CHECKER_OLLAMA_URL", raising=False)
    monkeypatch.delenv("STUDENT_AI_CHECKER_MODEL", raising=False)
    monkeypatch.delenv("STUDENT_AI_CHECKER_OLLAMA_TIMEOUT", raising=False)
    settings = Settings.from_env()
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.ollama_model == "qwen3.5:4b"
    assert settings.ollama_timeout_seconds == 300.0


def test_settings_from_env_supports_overrides(monkeypatch) -> None:
    monkeypatch.setenv("STUDENT_AI_CHECKER_OLLAMA_URL", "http://localhost:9999")
    monkeypatch.setenv("STUDENT_AI_CHECKER_MODEL", "local-model:4b")
    monkeypatch.setenv("STUDENT_AI_CHECKER_OLLAMA_TIMEOUT", "42.5")
    settings = Settings.from_env()
    assert settings.ollama_base_url == "http://localhost:9999"
    assert settings.ollama_model == "local-model:4b"
    assert settings.ollama_timeout_seconds == 42.5

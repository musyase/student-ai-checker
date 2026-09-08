"""Application configuration for the local technical core."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with environment-variable overrides."""

    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_base_url=os.getenv(
                "STUDENT_AI_CHECKER_OLLAMA_URL", DEFAULT_OLLAMA_BASE_URL
            ),
            ollama_model=os.getenv("STUDENT_AI_CHECKER_MODEL", DEFAULT_OLLAMA_MODEL),
            ollama_timeout_seconds=float(
                os.getenv(
                    "STUDENT_AI_CHECKER_OLLAMA_TIMEOUT",
                    str(DEFAULT_OLLAMA_TIMEOUT_SECONDS),
                )
            ),
        )

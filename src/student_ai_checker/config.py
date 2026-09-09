"""Application configuration for the local technical core."""

from __future__ import annotations

import os
from dataclasses import dataclass

from student_ai_checker.exceptions import StudentAICheckerError

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 300.0
# Explicit per-request context, with room for the report AND model response.
DEFAULT_OLLAMA_NUM_CTX = 16_384


def validate_num_ctx(value: int) -> int:
    """Reject invalid context values before allocating an HTTP client or model."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StudentAICheckerError(
            "STUDENT_AI_CHECKER_NUM_CTX: размер контекста должен быть целым "
            "положительным числом, например 16384."
        )
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with environment-variable overrides."""

    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS

    ollama_num_ctx: int = DEFAULT_OLLAMA_NUM_CTX

    def __post_init__(self) -> None:
        validate_num_ctx(self.ollama_num_ctx)

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            num_ctx = int(os.getenv("STUDENT_AI_CHECKER_NUM_CTX", str(DEFAULT_OLLAMA_NUM_CTX)))
        except ValueError as exc:
            raise StudentAICheckerError(
                "STUDENT_AI_CHECKER_NUM_CTX: укажите целое положительное число, например 16384."
            ) from exc
        return cls(
            ollama_num_ctx=validate_num_ctx(num_ctx),
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

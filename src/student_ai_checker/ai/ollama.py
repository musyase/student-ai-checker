"""Thin, replaceable client for the local Ollama HTTP API."""

from __future__ import annotations

import base64
import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from student_ai_checker.config import DEFAULT_OLLAMA_NUM_CTX, validate_num_ctx
from student_ai_checker.exceptions import OllamaModelMissingError, OllamaResponseError, OllamaUnavailableError


LOGGER = logging.getLogger(__name__)
MAX_ERROR_DETAIL_CHARS = 600
MAX_ERROR_BODY_BYTES = 16_384
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_LONG_ENCODED_DATA = re.compile(r"[A-Za-z0-9+/=_-]{100,}")


def _error_detail(response: httpx.Response) -> str:
    """Read only the provider's bounded error field, never dump request/response JSON.

    The value is untrusted diagnostic text, not a grading result or a prompt.
    An unknown/HTML response is deliberately not printed in full.
    """
    missing = "сервер не вернул читаемое поле error; тело ответа не выводится"
    if len(response.content) > MAX_ERROR_BODY_BYTES:
        return "слишком большое сообщение error; тело ответа не выводится"
    try:
        body = response.json()
    except ValueError:
        return missing
    if not isinstance(body, dict):
        return missing
    detail = body.get("error")
    if isinstance(detail, dict):
        detail = detail.get("message")
    if not isinstance(detail, str) or not detail.strip():
        return missing
    detail = _ANSI_ESCAPE.sub("", detail)
    detail = "".join(" " if unicodedata.category(char).startswith("C") else char for char in detail)
    detail = _LONG_ENCODED_DATA.sub("[длинные закодированные данные скрыты]", detail)
    detail = " ".join(detail.split())
    if not detail:
        return missing
    if len(detail) > MAX_ERROR_DETAIL_CHARS:
        detail = detail[:MAX_ERROR_DETAIL_CHARS] + "…"
    return detail


def _http_failure(response: httpx.Response) -> str:
    return (
        f"Ollama вернул HTTP {response.status_code}; "
        f"причина Ollama: {_error_detail(response)}; "
        "автоматическая оценка не выполнялась."
    )


class OllamaClient:
    """Local Ollama adapter with no cloud fallback."""

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float = 300.0, client: httpx.Client | None = None, num_ctx: int = DEFAULT_OLLAMA_NUM_CTX) -> None:
        self.num_ctx = validate_num_ctx(num_ctx)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_models(self) -> list[str]:
        try:
            response = self._client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise OllamaUnavailableError(_http_failure(exc.response)) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaUnavailableError("Не удалось подключиться к локальному Ollama. Проверьте, что Ollama запущен.") from exc
        if not isinstance(payload, dict) or "error" in payload or not isinstance(payload.get("models"), list):
            raise OllamaUnavailableError("Ollama вернул некорректный список моделей.")
        models: list[str] = []
        for item in payload["models"]:
            if not isinstance(item, dict):
                raise OllamaUnavailableError("Ollama вернул некорректный список моделей.")
            name = item.get("name") or item.get("model")
            if isinstance(name, str):
                models.append(name)
        return models

    def model_available(self) -> bool:
        return self.model in self.list_models()

    def assert_ready(self) -> None:
        models = self.list_models()
        if self.model not in models:
            raise OllamaModelMissingError(f"Локальная модель '{self.model}' не найдена в Ollama. Установите её перед проверкой или выберите другую модель.")

    def chat_text(self, *, system_prompt: str, user_prompt: str) -> str:
        return self._chat(system_prompt=system_prompt, user_prompt=user_prompt)

    def chat_multimodal(self, *, system_prompt: str, user_prompt: str, images: Sequence[bytes]) -> str:
        return self._chat(system_prompt=system_prompt, user_prompt=user_prompt, images=images)

    def chat_structured(self, *, system_prompt: str, user_prompt: str, schema: Mapping[str, Any], images: Sequence[bytes] = ()) -> str:
        return self._chat(system_prompt=system_prompt, user_prompt=user_prompt, images=images, schema=schema)

    def _chat(self, *, system_prompt: str, user_prompt: str, images: Sequence[bytes] = (), schema: Mapping[str, Any] | None = None) -> str:
        user_message: dict[str, Any] = {"role": "user", "content": user_prompt}
        if images:
            user_message["images"] = [base64.b64encode(image).decode("ascii") for image in images]
        payload: dict[str, Any] = {"model": self.model, "stream": False, "messages": [{"role": "system", "content": system_prompt}, user_message], "options": {"temperature": 0, "num_ctx": self.num_ctx}, "think": False}
        if schema is not None:
            payload["format"] = dict(schema)
        LOGGER.info(
            "Ollama request: images=%d, image_bytes=%d, text_chars=%d, structured=%s, num_ctx=%d",
            len(images), sum(len(image) for image in images),
            len(system_prompt) + len(user_prompt), schema is not None, self.num_ctx,
        )
        try:
            response = self._client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError("Не удалось подключиться к локальному Ollama.") from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError("Ollama не ответил вовремя; автоматическая оценка не выполнялась.") from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaResponseError(_http_failure(exc.response)) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaResponseError("Не удалось прочитать ответ Ollama; автоматическая оценка не выполнялась.") from exc
        if not isinstance(body, dict):
            raise OllamaResponseError("Ollama вернул некорректную структуру ответа; автоматическая оценка не выполнялась.")
        if "error" in body:
            raise OllamaResponseError(
                f"Ollama сообщил об ошибке: {_error_detail(response)}; автоматическая оценка не выполнялась."
            )
        if body.get("done") is False or body.get("done_reason") == "length":
            raise OllamaResponseError(
                "Ollama не завершил ответ или остановился по лимиту длины; "
                "неполный результат не принимается, нужна ручная проверка."
            )
        message = body.get("message")
        if not isinstance(message, dict):
            raise OllamaResponseError("Ollama вернул некорректную структуру сообщения; автоматическая оценка не выполнялась.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaResponseError("Ollama вернул пустой ответ.")
        return content.strip()

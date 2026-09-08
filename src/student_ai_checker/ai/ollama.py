"""Thin, replaceable client for the local Ollama HTTP API."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from student_ai_checker.exceptions import OllamaModelMissingError, OllamaResponseError, OllamaUnavailableError


class OllamaClient:
    """Local Ollama adapter with no cloud fallback."""

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float = 300.0, client: httpx.Client | None = None) -> None:
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
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaUnavailableError("Не удалось подключиться к локальному Ollama. Проверьте, что Ollama запущен.") from exc
        models: list[str] = []
        for item in payload.get("models", []):
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
        payload: dict[str, Any] = {"model": self.model, "stream": False, "messages": [{"role": "system", "content": system_prompt}, user_message], "options": {"temperature": 0}, "think": False}
        if schema is not None:
            payload["format"] = dict(schema)
        try:
            response = self._client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError("Не удалось подключиться к локальному Ollama.") from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError("Ollama не ответил вовремя; автоматическая оценка не выполнялась.") from exc
        except httpx.HTTPStatusError as exc:
            raise OllamaResponseError(f"Ollama вернул HTTP {exc.response.status_code}; автоматическая оценка не выполнялась.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaResponseError("Не удалось прочитать ответ Ollama; автоматическая оценка не выполнялась.") from exc
        content = body.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaResponseError("Ollama вернул пустой ответ.")
        return content.strip()

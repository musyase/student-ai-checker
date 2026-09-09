"""HTTP responses below are test-only mocks, not real model inference."""
from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest

from student_ai_checker.ai import OllamaClient
from student_ai_checker.exceptions import OllamaResponseError, OllamaUnavailableError


def make_client(response: httpx.Response) -> tuple[OllamaClient, list[httpx.Request], httpx.Client]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaClient(base_url="http://ollama.test", model="qwen3.5:4b", client=http), calls, http


def test_http_400_exposes_server_reason_without_retry() -> None:
    reason = "request (6000 tokens) exceeds the available context size (4096 tokens)"
    client, calls, http = make_client(httpx.Response(400, json={"error": reason}))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_multimodal(system_prompt="trusted", user_prompt="private report", images=[b"first", b"second"])
    assert "HTTP 400" in str(exc.value)
    assert reason in str(exc.value)
    assert "автоматическая оценка не выполнялась" in str(exc.value)
    assert len(calls) == 1
    assert "private report" not in str(exc.value)


@pytest.mark.parametrize("reason", [
    "invalid image data", "failed to convert JSON schema to grammar",
    'think value "high" is not supported for this model',
    "this model only supports one image while more than one image requested",
])
def test_different_http_errors_are_not_relabelled_as_context(reason: str) -> None:
    client, calls, http = make_client(httpx.Response(400, json={"error": reason}))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_text(system_prompt="", user_prompt="")
    assert reason in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    {}, {"error": None}, {"error": "   "}, {"error": 4},
    {"error": ["SECRET"]}, {"error": {"detail": "SECRET"}},
    {"prompt": "SECRET"}, ["SECRET"], "SECRET", None,
])
def test_missing_or_invalid_error_never_dumps_body(payload: object) -> None:
    client, calls, http = make_client(httpx.Response(400, json=payload))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_text(system_prompt="", user_prompt="")
    assert "HTTP 400" in str(exc.value)
    assert "SECRET" not in str(exc.value)
    assert "error" in str(exc.value)
    assert len(calls) == 1


def test_non_json_body_is_not_dumped() -> None:
    client, _, http = make_client(httpx.Response(500, text="<html>SECRET</html>"))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_text(system_prompt="", user_prompt="")
    assert "HTTP 500" in str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_nested_message_is_supported_but_other_fields_are_not_exposed() -> None:
    payload = {"error": {"message": "failed to load model", "request": "SECRET"}}
    client, _, http = make_client(httpx.Response(500, json=payload))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_text(system_prompt="", user_prompt="")
    assert "failed to load model" in str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_reason_is_bounded_and_console_control_codes_are_removed() -> None:
    client, _, http = make_client(httpx.Response(400, json={"error": "\x1b[31mBAD\x1b[0m\r\n\u202e" + "x" * 2000}))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_text(system_prompt="", user_prompt="")
    message = str(exc.value)
    assert "BAD" in message
    assert "\x1b" not in message and "\u202e" not in message
    assert "\n" not in message and "\r" not in message
    assert len(message) < 850


def test_long_base64_blob_is_redacted() -> None:
    encoded = base64.b64encode(b"private pixel data" * 50).decode("ascii")
    client, _, http = make_client(httpx.Response(400, json={"error": "bad image: " + encoded}))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_text(system_prompt="", user_prompt="")
    assert "bad image" in str(exc.value)
    assert encoded[:60] not in str(exc.value)


@pytest.mark.parametrize("payload", [None, [], "bad", {"message": None}, {"message": []}, {"message": "bad"}])
def test_malformed_success_body_raises_domain_error(payload: object) -> None:
    client, _, http = make_client(httpx.Response(200, json=payload))
    with http, pytest.raises(OllamaResponseError):
        client.chat_text(system_prompt="", user_prompt="")


def test_error_inside_http_200_is_not_accepted_as_answer() -> None:
    client, _, http = make_client(httpx.Response(200, json={"error": "model load failed", "message": {"content": "{}"}}))
    with http, pytest.raises(OllamaResponseError) as exc:
        client.chat_text(system_prompt="", user_prompt="")
    assert "model load failed" in str(exc.value)


def test_get_models_includes_http_failure_reason() -> None:
    client, _, http = make_client(httpx.Response(503, json={"error": "server unavailable"}))
    with http, pytest.raises(OllamaUnavailableError) as exc:
        client.list_models()
    assert "HTTP 503" in str(exc.value)
    assert "server unavailable" in str(exc.value)


@pytest.mark.parametrize("payload", [[], {"models": None}, {"models": [None]}, {"models": "wrong"}])
def test_invalid_models_payload_fails_clearly(payload: object) -> None:
    client, _, http = make_client(httpx.Response(200, json=payload))
    with http, pytest.raises(OllamaUnavailableError):
        client.list_models()


def test_two_images_and_schema_are_preserved_and_prompts_are_not_logged(caplog) -> None:
    caplog.set_level(logging.INFO, logger="student_ai_checker.ai.ollama")
    client, calls, http = make_client(httpx.Response(200, json={"message": {"content": "{}"}}))
    images = [b"private-image-1", b"private-image-2"]
    schema = {"type": "object", "additionalProperties": False}
    with http:
        answer = client.chat_structured(system_prompt="PRIVATE-SYSTEM", user_prompt="PRIVATE-STUDENT", schema=schema, images=images)
    sent = json.loads(calls[0].content)
    assert answer == "{}"
    assert sent["format"] == schema
    assert sent["stream"] is False and sent["think"] is False
    assert sent["messages"][1]["images"] == [base64.b64encode(image).decode("ascii") for image in images]
    assert "images=2" in caplog.text
    assert "PRIVATE-STUDENT" not in caplog.text and "PRIVATE-SYSTEM" not in caplog.text
    assert "private-image" not in caplog.text

"""Context configuration and HTTP payload regressions; HTTP calls are test-only mocks."""
from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest

from student_ai_checker.ai import OllamaClient
from student_ai_checker.config import DEFAULT_OLLAMA_NUM_CTX, Settings
from student_ai_checker.exceptions import OllamaResponseError, StudentAICheckerError


def test_context_default_leaves_room_beyond_reported_pr1_request(monkeypatch) -> None:
    monkeypatch.delenv("STUDENT_AI_CHECKER_NUM_CTX", raising=False)
    settings = Settings.from_env()
    assert settings.ollama_num_ctx == DEFAULT_OLLAMA_NUM_CTX == 16_384
    # 5152 is the server-reported input length, not a client token estimate.
    assert settings.ollama_num_ctx - 5152 > 4096


@pytest.mark.parametrize("value", ["8192", "16384", "32768", " 8192 "])
def test_context_environment_override(monkeypatch, value: str) -> None:
    monkeypatch.setenv("STUDENT_AI_CHECKER_NUM_CTX", value)
    assert Settings.from_env().ollama_num_ctx == int(value)


@pytest.mark.parametrize("value", ["", "bad", "true", "4096.5", "0", "-1", "nan", "inf"])
def test_invalid_context_environment_has_clear_domain_error(monkeypatch, value: str) -> None:
    monkeypatch.setenv("STUDENT_AI_CHECKER_NUM_CTX", value)
    with pytest.raises(StudentAICheckerError, match="STUDENT_AI_CHECKER_NUM_CTX"):
        Settings.from_env()


@pytest.mark.parametrize("value", [True, False, 0, -1, 4096.5, "16384", None])
def test_invalid_context_is_rejected_before_http_client_creation(monkeypatch, value) -> None:
    def forbidden_client(*args, **kwargs):
        pytest.fail("Invalid context must be rejected before constructing the HTTP client")

    monkeypatch.setattr(httpx, "Client", forbidden_client)
    with pytest.raises(StudentAICheckerError, match="STUDENT_AI_CHECKER_NUM_CTX"):
        OllamaClient(base_url="http://ollama.test", model="vision:4b", num_ctx=value)
    with pytest.raises(StudentAICheckerError, match="STUDENT_AI_CHECKER_NUM_CTX"):
        Settings(ollama_num_ctx=value)


@pytest.mark.parametrize("method", ["chat_text", "chat_multimodal", "chat_structured"])
@pytest.mark.parametrize("context", [8192, 16384, 32768])
def test_all_chat_paths_preserve_inputs_and_send_explicit_context(method, context, caplog) -> None:
    captured = []
    system_prompt = "PRIVATE-CRITERIA " * 400
    user_prompt = "PRIVATE-REPORT " * 600
    images = [b"first-original-image", b"second-original-image"]
    schema = {"type": "object", "additionalProperties": False}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "{}"}, "done": True, "done_reason": "stop"})

    caplog.set_level(logging.INFO, logger="student_ai_checker.ai.ollama")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(base_url="http://ollama.test", model="vision:4b", client=http, num_ctx=context)
        kwargs = {"system_prompt": system_prompt, "user_prompt": user_prompt}
        if method != "chat_text":
            kwargs["images"] = images
        if method == "chat_structured":
            kwargs["schema"] = schema
        assert getattr(client, method)(**kwargs) == "{}"

    assert len(captured) == 1
    sent = captured[0]
    assert sent["options"] == {"temperature": 0, "num_ctx": context}
    assert sent["stream"] is False and sent["think"] is False
    assert sent["messages"][0]["content"] == system_prompt
    assert sent["messages"][1]["content"] == user_prompt
    if method != "chat_text":
        assert sent["messages"][1]["images"] == [base64.b64encode(image).decode("ascii") for image in images]
    if method == "chat_structured":
        assert sent["format"] == schema
    assert f"num_ctx={context}" in caplog.text
    assert "PRIVATE-REPORT" not in caplog.text and "PRIVATE-CRITERIA" not in caplog.text


def test_direct_client_default_also_sends_context() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["options"]["num_ctx"] == 16384
        return httpx.Response(200, json={"message": {"content": "{}"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(base_url="http://ollama.test", model="vision:4b", client=http)
        assert client.chat_text(system_prompt="", user_prompt="") == "{}"


@pytest.mark.parametrize("completion", [{"done": False}, {"done": True, "done_reason": "length"}])
def test_incomplete_response_is_not_accepted_even_with_valid_json(completion) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={**completion, "message": {"content": '{"results": []}'}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(base_url="http://ollama.test", model="vision:4b", client=http)
        with pytest.raises(OllamaResponseError, match="неполный результат"):
            client.chat_structured(system_prompt="", user_prompt="", schema={"type": "object"})
    assert len(calls) == 1


def test_context_error_is_not_retried_by_removing_images_or_increasing_memory() -> None:
    calls = []
    reason = json.dumps({"error": {"code": 400, "message": "request (5152 tokens) exceeds the available context size (4096 tokens), try increasing it", "type": "exceed_context_size_error", "n_prompt_tokens": 5152, "n_ctx": 4096}})

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": reason})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = OllamaClient(base_url="http://ollama.test", model="vision:4b", client=http)
        with pytest.raises(OllamaResponseError, match="exceed_context_size_error"):
            client.chat_multimodal(system_prompt="criteria", user_prompt="full report", images=[b"one", b"two"])
    assert len(calls) == 1
    assert len(json.loads(calls[0].content)["messages"][1]["images"]) == 2

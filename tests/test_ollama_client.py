from __future__ import annotations

import base64
import json

import httpx
import pytest

from student_ai_checker.ai import OllamaClient
from student_ai_checker.exceptions import OllamaModelMissingError


def test_lists_models_and_detects_configured_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient(base_url="http://ollama.test", model="qwen3.5:4b", client=http)
    assert client.model_available() is True
    client.assert_ready()


def test_missing_model_raises_clear_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient(base_url="http://ollama.test", model="qwen3.5:4b", client=http)
    with pytest.raises(OllamaModelMissingError):
        client.assert_ready()


def test_structured_multimodal_request_contains_schema_and_image() -> None:
    captured: dict[str, object] = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"message": {"content": '{"results": []}'}})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OllamaClient(base_url="http://ollama.test", model="vision:4b", client=http)
    schema = {"type": "object", "properties": {"results": {"type": "array"}}}
    image = b"png-bytes"
    answer = client.chat_structured(system_prompt="trusted", user_prompt="untrusted", schema=schema, images=[image])
    assert answer == '{"results": []}'
    assert captured["format"] == schema
    assert captured["stream"] is False
    assert captured["think"] is False
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["images"] == [base64.b64encode(image).decode("ascii")]

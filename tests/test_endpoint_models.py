"""Offline tests for explicit endpoint model-list discovery."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from openai4s.server.endpoint_models import EndpointModelError, EndpointModelFetcher


class _Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        return self.raw[:limit]


class _Opener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return _Response(payload)


def _fetch(provider, base_url, payload, api_key="secret"):
    opener = _Opener([payload])
    result = EndpointModelFetcher(opener=opener).fetch(
        provider=provider, base_url=base_url, api_key=api_key
    )
    return result, opener.calls[0][0]


def test_openai_models_use_bearer_and_fill_first_model():
    result, request = _fetch(
        "chatgpt",
        "https://api.example/v1",
        {"data": [{"id": "gpt-custom"}, {"id": "gpt-other"}]},
    )
    assert result["models"] == ["gpt-custom", "gpt-other"]
    assert result["default_model"] == "gpt-custom"
    assert request.full_url == "https://api.example/v1/models"
    assert request.get_header("Authorization") == "Bearer secret"


def test_protocol_specific_model_routes_and_auth_headers():
    _, anthropic = _fetch(
        "claude", "https://api.example", {"data": [{"id": "claude-x"}]}
    )
    assert anthropic.full_url == "https://api.example/v1/models"
    assert anthropic.get_header("X-api-key") == "secret"
    assert anthropic.get_header("Anthropic-version") == "2023-06-01"

    _, gemini = _fetch(
        "gemini",
        "https://generativelanguage.googleapis.com",
        {"models": [{"name": "models/gemini-x"}]},
    )
    assert gemini.full_url.endswith("/v1beta/models")
    assert gemini.get_header("X-goog-api-key") == "secret"


def test_ollama_falls_back_to_native_tags_route():
    opener = _Opener(
        [
            HTTPError(
                "https://ollama.example/v1/models", 404, "missing", {}, io.BytesIO()
            ),
            {"models": [{"name": "llama3.2"}]},
        ]
    )
    result = EndpointModelFetcher(opener=opener).fetch(
        provider="ollama", base_url="https://ollama.example/v1", api_key=""
    )
    assert result["models"] == ["llama3.2"]
    assert opener.calls[1][0].full_url == "https://ollama.example/api/tags"


def test_invalid_or_empty_endpoint_results_are_safe_failures():
    with pytest.raises(EndpointModelError, match="absolute http"):
        EndpointModelFetcher(opener=_Opener([])).fetch(
            provider="chatgpt", base_url="https://example/v1?key=leak", api_key="secret"
        )

    with pytest.raises(EndpointModelError, match="no model ids"):
        EndpointModelFetcher(opener=_Opener([{"data": []}])).fetch(
            provider="chatgpt", base_url="https://example/v1", api_key="secret"
        )


def test_response_is_bounded_before_model_parsing():
    opener = _Opener([{"data": [{"id": "x" * 2000}]}])
    with pytest.raises(EndpointModelError, match="too much"):
        EndpointModelFetcher(opener=opener, max_response_bytes=1024).fetch(
            provider="chatgpt", base_url="https://example/v1", api_key="secret"
        )

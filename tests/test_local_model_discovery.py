"""Contracts for bounded, non-mutating local model endpoint discovery."""

from __future__ import annotations

import io
import json

import pytest

from openai4s.server.model_discovery import (
    LocalModelDiscoveryService,
    LocalModelEndpoint,
)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class _Opener:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout, dict(request.header_items())))
        value = self.responses[request.full_url]
        if isinstance(value, Exception):
            raise value
        return _Response(json.dumps(value).encode("utf-8"))


def _candidates():
    return (
        LocalModelEndpoint(
            "ollama",
            "Ollama",
            "http://127.0.0.1:11434/v1",
            "http://127.0.0.1:11434/api/tags",
        ),
        LocalModelEndpoint(
            "studio",
            "Studio",
            "http://[::1]:1234/v1",
            "http://[::1]:1234/v1/models",
        ),
    )


def test_discovery_normalizes_openai_and_ollama_models_and_never_mutates():
    candidates = _candidates()
    opener = _Opener(
        {
            candidates[0].models_url: {
                "models": [{"name": "qwen3:8b"}, {"model": "qwen3:8b"}]
            },
            candidates[1].models_url: {
                "data": [{"id": "local/reasoner"}, {"id": "local/chat"}]
            },
        }
    )
    service = LocalModelDiscoveryService(
        endpoints=candidates,
        opener=opener,
        timeout_s=0.1,
        cache_ttl_s=10,
    )

    result = service.discover()

    assert result == {
        "endpoints": [
            {
                "kind": "ollama",
                "label": "Ollama",
                "provider": "chatgpt",
                "base_url": "http://127.0.0.1:11434/v1",
                "models": ["qwen3:8b"],
                "default_model": "qwen3:8b",
                "local": True,
                "requires_api_key": False,
            },
            {
                "kind": "studio",
                "label": "Studio",
                "provider": "chatgpt",
                "base_url": "http://[::1]:1234/v1",
                "models": ["local/reasoner", "local/chat"],
                "default_model": "local/reasoner",
                "local": True,
                "requires_api_key": False,
            },
        ],
        "probed": 2,
        "cached": False,
        "mutated_settings": False,
    }
    assert all(call[1] == 0.1 for call in opener.calls)

    cached = service.discover()
    assert cached["cached"] is True
    assert len(opener.calls) == 2


def test_unreachable_invalid_and_oversized_probes_stay_inert():
    candidates = _candidates()
    opener = _Opener(
        {
            candidates[0].models_url: OSError("offline"),
            candidates[1].models_url: ["not", "an", "object"],
        }
    )
    service = LocalModelDiscoveryService(endpoints=candidates, opener=opener)
    assert service.discover(force=True)["endpoints"] == []


def test_catalog_does_not_open_sockets_or_refresh_in_the_background():
    opener = _Opener({})
    service = LocalModelDiscoveryService(
        endpoints=_candidates(), opener=opener, cache_ttl_s=10
    )
    payload = service.catalog()
    assert opener.calls == []
    assert payload["probed"] == 0
    assert payload["contacted"] is False
    assert payload["background_refresh"] is False
    assert payload["mutated_settings"] is False
    assert not hasattr(service, "_timer")
    assert not hasattr(service, "_thread")


def test_default_discovery_opener_rejects_redirects():
    service = LocalModelDiscoveryService(endpoints=(_candidates()[0],))
    redirect_handler = next(
        handler
        for handler in service._opener.handlers
        if type(handler).__name__ == "_RejectRedirects"
    )

    assert (
        redirect_handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "https://example.com/redirected",
        )
        is None
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:8000/v1",
        "http://localhost:8000/v1",
        "https://example.com/v1",
        "http://user:secret@127.0.0.1:8000/v1",
        "http://127.0.0.1:8000/v1?target=other",
        "file:///tmp/models",
    ],
)
def test_candidate_urls_must_be_literal_loopback(url):
    endpoint = LocalModelEndpoint("bad", "Bad", url, url)
    with pytest.raises(ValueError, match="loopback"):
        LocalModelDiscoveryService(endpoints=(endpoint,))

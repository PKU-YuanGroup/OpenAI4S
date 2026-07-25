"""The typed transport must be reachable from a real ``chat()`` call.

`openai4s/llm/transport.py` was complete and well tested in isolation, and
dead in production. Every adapter called ``post_json(url, payload, headers,
timeout)`` positionally and the facade hook dropped anything else, so:

  * every ``TransportError`` reached the caller with ``provider=None`` — the
    one field that says *which* provider rate-limited you, in a product that
    routes across four wires;
  * no caller on the LLM path ever supplied ``should_cancel``, so a user's
    Stop could not interrupt a retry backoff. A provider answering 429 with a
    generous ``Retry-After`` holds the call for the whole retry budget, and
    for that entire window Stop did nothing.

These tests drive the real ``openai4s.llm.chat`` facade rather than the
transport directly, because the transport's own tests already passed while
production was unwired — proving the seam, not the plumbing, is the point.
"""
import io
import urllib.error

import pytest

from openai4s.config import LLMConfig
from openai4s.llm import chat
from openai4s.llm.models import TransportError


def _cfg(provider="chatgpt"):
    return LLMConfig(
        provider=provider,
        api_key="test-key",
        model="test-model",
        base_url="https://x.invalid/v1",
        timeout_s=5,
    )


def _http_error(code, headers=None):
    return urllib.error.HTTPError(
        url="https://x.invalid/v1",
        code=code,
        msg="err",
        hdrs=headers or {},
        fp=io.BytesIO(b"{}"),
    )


def test_a_transport_error_names_the_provider_it_came_from(monkeypatch):
    """Four wires are supported; an error that cannot say which one failed
    sends the operator to read logs the daemon deliberately does not keep."""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(_http_error(401)),
    )
    with pytest.raises(TransportError) as e:
        chat([{"role": "user", "content": "hi"}], _cfg("chatgpt"))
    assert e.value.provider == "chatgpt"
    assert e.value.status == 401


def test_stop_interrupts_a_retry_backoff(monkeypatch):
    """503 is retryable, so without cancellation this burns the full budget."""
    attempts = []

    def urlopen(*a, **k):
        attempts.append(1)
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(TransportError) as e:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(),
            should_cancel=lambda: True,
        )
    assert "cancelled" in str(e.value)
    assert len(attempts) == 1, "a cancelled call must not start a second attempt"


def test_without_cancellation_the_same_call_retries(monkeypatch):
    """The counterpart: proves the single attempt above is cancellation, not
    an unrelated failure to retry."""
    attempts = []

    def urlopen(*a, **k):
        attempts.append(1)
        raise _http_error(503)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(TransportError):
        chat([{"role": "user", "content": "hi"}], _cfg())
    assert len(attempts) > 1


def test_an_injected_four_argument_transport_still_works(monkeypatch):
    """The documented offline-injection contract: tests replace the facade
    hooks with plain four-argument callables. Binding the new context must
    not start passing keywords those cannot accept."""
    seen = {}

    def legacy_post_json(url, payload, headers, timeout):
        seen["url"] = url
        return {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }

    monkeypatch.setattr("openai4s.llm._post_json", legacy_post_json)
    reply = chat([{"role": "user", "content": "hi"}], _cfg())
    assert reply["content"] == "ok"
    assert seen["url"].endswith("/chat/completions")


def test_a_transport_accepting_the_context_receives_it(monkeypatch):
    captured = {}

    def modern_post_json(url, payload, headers, timeout, **kw):
        captured.update(kw)
        return {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }

    monkeypatch.setattr("openai4s.llm._post_json", modern_post_json)
    stop = object()
    chat([{"role": "user", "content": "hi"}], _cfg("chatgpt"), should_cancel=stop)
    assert captured["provider"] == "chatgpt"
    assert captured["should_cancel"] is stop

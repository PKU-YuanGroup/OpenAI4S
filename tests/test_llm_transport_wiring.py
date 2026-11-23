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
import json
import socket
import ssl
import urllib.error

import pytest

from openai4s.config import LLMConfig
from openai4s.llm import chat, transport
from openai4s.llm.models import TransportError


def _cfg(provider="chatgpt"):
    return LLMConfig(
        provider=provider,
        api_key="test-key",
        model="test-model",
        base_url="https://x.invalid/v1",
        timeout_s=5,
    )


def _http_error(code, headers=None, body=b"{}"):
    return urllib.error.HTTPError(
        url="https://x.invalid/v1",
        code=code,
        msg="err",
        hdrs=headers or {},
        fp=io.BytesIO(body),
    )


class _Resp(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {}


def test_a_transport_error_names_the_provider_it_came_from(monkeypatch):
    """Four wires are supported; an error that cannot say which one failed
    sends the operator to read logs the daemon deliberately does not keep."""
    monkeypatch.setattr(
        "openai4s.llm.transport._urlopen",
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

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    with pytest.raises(TransportError) as e:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(),
            should_cancel=lambda: True,
        )
    assert "cancelled" in str(e.value)
    assert len(attempts) == 0, "a pre-cancelled call must never send"


def test_without_cancellation_the_same_call_retries(monkeypatch):
    """The counterpart: proves the single attempt above is cancellation, not
    an unrelated failure to retry."""
    attempts = []

    def urlopen(*a, **k):
        attempts.append(1)
        raise _http_error(503)

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(TransportError):
        chat([{"role": "user", "content": "hi"}], _cfg())
    assert len(attempts) > 1


@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
@pytest.mark.parametrize(
    ("status", "attempt_count"), [(401, 1), (403, 1), (429, 3), (503, 3)]
)
def test_stream_transport_failure_has_one_bounded_budget_not_sse_plus_json(
    monkeypatch, provider, status, attempt_count
):
    """An exhausted stream transport must not start three blocking retries."""

    attempts = []

    def urlopen(*a, **k):
        attempts.append(1)
        # A fresh object each time: HTTPError.read() consumes its body.
        raise _http_error(status)

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    with pytest.raises(TransportError):
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(provider),
            on_delta=lambda _piece: None,
        )

    assert len(attempts) == attempt_count


def test_ark_burst_inside_http_200_sse_retries_in_the_same_bounded_budget(
    monkeypatch,
):
    """Ark can carry its real 429 as an SSE error inside an HTTP 200 stream."""

    attempts = []

    def urlopen(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            return iter(
                [
                    b'data: {"error":{"code":"RequestBurstTooFast",'
                    b'"type":"TooManyRequests","message":"private"}}\n',
                    b"\n",
                ]
            )
        return iter(
            [
                b'data: {"choices":[{"delta":{"content":"ok"},'
                b'"finish_reason":"stop"}]}\n',
                b"\n",
            ]
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    reply = chat(
        [{"role": "user", "content": "continue"}],
        _cfg("ark"),
        on_delta=lambda _piece: None,
    )

    assert reply["content"] == "ok"
    assert len(attempts) == 3


@pytest.mark.parametrize(
    "semantic",
    [
        {"usage": {"prompt_tokens": 999}, "choices": []},
        {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": "{"}}]
                    }
                }
            ]
        },
    ],
)
def test_semantic_stream_state_vetoes_a_burst_retry(monkeypatch, semantic):
    attempts = []

    def urlopen(*_args, **_kwargs):
        attempts.append(1)
        return iter(
            [
                b"data: " + json.dumps(semantic).encode() + b"\n",
                b"\n",
                b'data: {"error":{"code":"RequestBurstTooFast"}}\n',
                b"\n",
            ]
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "continue"}],
            _cfg("ark"),
            on_delta=lambda _piece: None,
        )
    assert raised.value.output_committed is True
    assert len(attempts) == 1


def test_role_only_stream_event_does_not_veto_a_safe_burst_retry(monkeypatch):
    attempts = []

    def urlopen(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            return iter(
                [
                    b'data: {"choices":[{"delta":{"role":"assistant",'
                    b'"content":""},"finish_reason":null}]}\n',
                    b"\n",
                    b'data: {"error":{"code":"RequestBurstTooFast",'
                    b'"type":"TooManyRequests","message":"private"}}\n',
                    b"\n",
                ]
            )
        return iter(
            [
                b'data: {"choices":[{"delta":{"content":"recovered"},'
                b'"finish_reason":"stop"}]}\n',
                b"\n",
            ]
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    reply = chat(
        [{"role": "user", "content": "continue"}],
        _cfg("ark"),
        on_delta=lambda _piece: None,
    )

    assert reply["content"] == "recovered"
    assert len(attempts) == 2


def test_visible_content_before_burst_is_never_replayed(monkeypatch):
    attempts = []
    deltas = []

    def urlopen(*_args, **_kwargs):
        attempts.append(1)
        return iter(
            [
                b'data: {"choices":[{"delta":{"content":"already shown"},'
                b'"finish_reason":null}]}\n',
                b"\n",
                b'data: {"error":{"code":"RequestBurstTooFast",'
                b'"type":"TooManyRequests","message":"private"}}\n',
                b"\n",
            ]
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "continue"}],
            _cfg("ark"),
            on_delta=deltas.append,
        )

    assert raised.value.output_committed is True
    assert deltas == ["already shown"]
    assert len(attempts) == 1


def test_real_http_stream_compatibility_refusal_falls_back_to_json(monkeypatch):
    attempts = []

    def urlopen(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise _http_error(400, body=b'{"error":{"code":"streaming_not_supported"}}')
        return _Resp(
            b'{"choices":[{"message":{"content":"fallback"},'
            b'"finish_reason":"stop"}],"usage":{}}'
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)

    reply = chat(
        [{"role": "user", "content": "hi"}],
        _cfg(),
        on_delta=lambda _piece: None,
    )

    assert reply["content"] == "fallback"
    assert len(attempts) == 2


def test_http_400_with_auth_code_does_not_hide_behind_stream_fallback(monkeypatch):
    attempts = []

    def urlopen(*_args, **_kwargs):
        attempts.append(1)
        raise _http_error(400, body=b'{"error":{"code":"invalid_api_key"}}')

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)

    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(),
            on_delta=lambda _piece: None,
        )

    assert raised.value.error_code == "invalid_api_key"
    assert len(attempts) == 1


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


@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
@pytest.mark.parametrize(
    "status, error, allowed",
    [
        (400, {"code": "streaming_not_supported"}, True),
        (400, {"code": "streaming_not_supported", "param": "stream_options"}, False),
        (422, {"type": "streaming_not_supported"}, True),
        (400, {"code": "unsupported_parameter", "param": "stream"}, True),
        (422, {"code": "unknown_parameter", "param": "stream"}, True),
        (400, {"code": "unsupported_parameter", "param": "stream_options"}, False),
        (400, {"code": "unsupported_parameter", "param": "tools"}, False),
        (400, {"message": "streaming_not_supported"}, False),
        (400, {}, False),
        (404, {}, False),
        (405, {}, False),
        (406, {}, False),
        (415, {}, False),
        (422, {}, False),
        (501, {"code": "streaming_not_supported"}, False),
    ],
)
def test_only_structured_stream_refusal_allows_one_json_attempt(
    monkeypatch, provider, status, error, allowed
):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(json.loads(req.data).get("stream", False))
        if len(sends) == 1:
            raise _http_error(status, body=json.dumps({"error": error}).encode())
        raise _http_error(503, {"x-request-id": "fallback-failed", "retry-after": "0"})

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(provider),
            on_delta=lambda _piece: None,
        )
    assert sends == ([True, False] if allowed else [True])
    assert raised.value.status == (503 if allowed else status)
    if allowed:
        assert raised.value.request_id == "fallback-failed"


@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
@pytest.mark.parametrize(
    "error",
    [
        # OpenAI's canonical unsupported-parameter body leaves `code` null.
        {
            "message": "Unsupported parameter: 'stream'",
            "type": "invalid_request_error",
            "param": "stream",
            "code": None,
        },
        # Anthropic reports only `type`, so an allowlist of `code` values made
        # this adapter's whole `_StreamStartError` branch unreachable.
        {"type": "invalid_request_error", "param": "stream"},
    ],
)
def test_param_named_stream_is_a_structured_refusal_whatever_the_code(
    monkeypatch, provider, error
):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(json.loads(req.data).get("stream", False))
        if len(sends) == 1:
            raise _http_error(400, body=json.dumps({"error": error}).encode())
        raise _http_error(503, {"x-request-id": "fallback-failed", "retry-after": "0"})

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(provider),
            on_delta=lambda _piece: None,
        )
    assert sends == [True, False]
    assert raised.value.request_id == "fallback-failed"


@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
@pytest.mark.parametrize("error", [{"param": "tools"}, {"message": "stream is bad"}])
def test_a_body_that_does_not_name_stream_still_refuses_compatibility(
    monkeypatch, provider, error
):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(json.loads(req.data).get("stream", False))
        raise _http_error(400, body=json.dumps({"error": error}).encode())

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(TransportError):
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(provider),
            on_delta=lambda _piece: None,
        )
    assert sends == [True]


@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
@pytest.mark.parametrize("refusal_attempt", [2, 3])
def test_stream_and_fallback_share_the_three_send_budget(
    monkeypatch, provider, refusal_attempt
):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(json.loads(req.data).get("stream", False))
        if len(sends) == refusal_attempt:
            raise _http_error(
                400,
                {"x-request-id": "refusal"},
                b'{"error":{"code":"streaming_not_supported"}}',
            )
        raise _http_error(503, {"retry-after": "0"})

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(provider),
            on_delta=lambda _piece: None,
        )
    assert sends == (
        [True, True, False] if refusal_attempt == 2 else [True, True, True]
    )
    if refusal_attempt == 3:
        assert raised.value.request_id == "refusal"
        assert raised.value.status == 400


@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
def test_cancel_between_stream_refusal_and_fallback_preserves_metadata(
    monkeypatch, provider
):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(True)
        raise _http_error(
            400,
            {"x-request-id": "cancelled-refusal", "retry-after": "2"},
            b'{"error":{"code":"streaming_not_supported"}}',
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(provider),
            on_delta=lambda _piece: None,
            should_cancel=lambda: bool(sends),
        )
    assert len(sends) == 1
    assert "cancelled" in str(raised.value)
    assert raised.value.status == 400
    assert raised.value.error_code == "streaming_not_supported"
    assert raised.value.request_id == "cancelled-refusal"
    assert raised.value.retry_after == 2


def _sends_for(monkeypatch, reason):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(True)
        raise urllib.error.URLError(reason)

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(TransportError):
        chat([{"role": "user", "content": "hi"}], _cfg(), on_delta=lambda _piece: None)
    return sends


@pytest.mark.parametrize(
    "reason",
    [
        # Prose is not evidence: a provider that *says* "connection refused"
        # has not proved the request never left this machine.
        "connection refused",
        # A bare OSError names no phase, so it proves nothing either.
        OSError("uncertain write"),
    ],
)
def test_uncertain_url_error_is_not_replayed(monkeypatch, reason):
    assert len(_sends_for(monkeypatch, reason)) == 1


@pytest.mark.parametrize(
    "reason",
    [
        ConnectionRefusedError(61, "Connection refused"),
        socket.gaierror(8, "nodename nor servname provided, or not known"),
        # A *connect* timeout. `state.sent` is what separates it from a read
        # timeout: nothing was written, so the request is replayable.
        TimeoutError(60, "Operation timed out"),
        # M-36: a reset before the first request byte is a dropped connect or
        # TLS handshake. `state.sent` separates it from a reset after the
        # write, which `test_llm_transport_typed` pins as a single attempt.
        ConnectionResetError(54, "Connection reset by peer"),
        ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
    ],
)
def test_a_connect_phase_failure_is_still_replayed(monkeypatch, reason):
    """The whole point of the retry policy: a request that provably never
    reached the provider is the one case where "no response" is safe to
    replay. Narrowing this to `ConnectionRefusedError` alone made a DNS blip
    or a connect timeout fail a turn on its first attempt."""

    assert len(_sends_for(monkeypatch, reason)) == transport.DEFAULT_MAX_ATTEMPTS


def test_separate_logical_calls_do_not_share_consumed_attempts(monkeypatch):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(True)
        if len(sends) % 3:
            raise _http_error(503, {"retry-after": "0"})
        return _Resp(
            b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    cfg = _cfg()

    def probe():
        return False

    for _ in range(2):
        assert (
            chat([{"role": "user", "content": "hi"}], cfg, should_cancel=probe)[
                "content"
            ]
            == "ok"
        )
    assert len(sends) == 6


def test_shared_backoff_budget_survives_a_transport_boundary(monkeypatch):
    from openai4s.llm import transport

    state = transport.CallState(max_attempts=4, retry_budget=1)
    sleeps = []
    sends = []

    def urlopen(req, **kwargs):
        sends.append(True)
        if len(sends) in (1, 3):
            raise _http_error(503, {"retry-after": "1", "x-request-id": "budget"})
        raise _http_error(400, body=b'{"error":{"code":"streaming_not_supported"}}')

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    with pytest.raises(TransportError):
        transport.post_sse(
            "https://x.invalid",
            {},
            {},
            5,
            lambda _: None,
            call_state=state,
            sleep=sleeps.append,
        )
    with pytest.raises(TransportError) as raised:
        transport.post_json(
            "https://x.invalid", {}, {}, 5, call_state=state, sleep=sleeps.append
        )
    assert len(sends) == 3
    assert sleeps == [1]
    assert raised.value.request_id == "budget"


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
def test_local_http_refusal_and_retry_use_one_logical_budget(provider):
    """Exercise real urllib/HTTPError bodies and headers, without a live model."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    sends = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            sends.append(payload.get("stream", False))
            status = 400 if len(sends) == 2 else 503
            body = (
                b'{"error":{"code":"streaming_not_supported"}}'
                if status == 400
                else b"{}"
            )
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "0")
            self.send_header("x-request-id", f"local-{len(sends)}")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = _cfg(provider)
        cfg.base_url = f"http://127.0.0.1:{server.server_port}/v1"
        with pytest.raises(TransportError) as raised:
            chat([{"role": "user", "content": "hi"}], cfg, on_delta=lambda _: None)
        assert sends == [True, True, False]
        assert raised.value.request_id == "local-3"
        assert raised.value.status == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "prefix",
    [
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        ],
        [{"type": "message_start", "message": {"usage": {"input_tokens": 1}}}],
        [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "thinking"},
            }
        ],
        [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": "{"},
            }
        ],
        [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hello"},
            }
        ],
    ],
)
def test_anthropic_semantic_events_cannot_be_replayed(monkeypatch, prefix):
    sends = []

    def urlopen(*args, **kwargs):
        sends.append(True)
        events = prefix + [{"type": "error", "error": {"type": "overloaded_error"}}]
        return iter(
            line
            for event in events
            for line in (b"data: " + json.dumps(event).encode() + b"\n", b"\n")
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}], _cfg("claude"), on_delta=lambda _: None
        )
    assert raised.value.output_committed
    assert len(sends) == 1


@pytest.mark.parametrize(
    "provider, error",
    [
        ("chatgpt", {"code": "RequestBurstTooFast"}),
        ("claude", {"type": "rate_limit_error"}),
    ],
)
def test_sse_error_keeps_response_metadata_through_cancellation(
    monkeypatch, provider, error
):
    from openai4s.llm import transport

    sleeps = []
    sends = []
    cancelled = []

    class Stream:
        headers = {"X-Request-Id": "stream-request-123", "Retry-After": "2"}

        def __iter__(self):
            yield b"data: " + json.dumps(
                {"type": "error", "error": error}
            ).encode() + b"\n"
            yield b"\n"

        def close(self):
            pass

    def urlopen(*args, **kwargs):
        sends.append(True)
        return Stream()

    def sleep(seconds):
        sleeps.append(seconds)
        cancelled.append(True)

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr(transport.time, "sleep", sleep)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}],
            _cfg(provider),
            on_delta=lambda _: None,
            should_cancel=lambda: bool(cancelled),
        )
    assert len(sends) == 1
    assert "cancelled" in str(raised.value)
    assert raised.value.request_id == "stream-request-123"
    assert raised.value.retry_after == 2
    assert raised.value.status == 429
    assert sleeps == [transport.CANCEL_POLL_S]


@pytest.mark.parametrize(
    "provider, event",
    [
        ("chatgpt", {"error": {"code": "service_unavailable"}}),
        ("claude", {"type": "error", "error": {"type": "overloaded_error"}}),
    ],
)
def test_sse_error_then_http_stream_refusal_uses_remaining_compatibility_send(
    monkeypatch, provider, event
):
    sends = []

    def urlopen(req, **kwargs):
        sends.append(json.loads(req.data).get("stream", False))
        if len(sends) == 1:
            return iter([b"data: " + json.dumps(event).encode() + b"\n", b"\n"])
        if len(sends) == 2:
            raise _http_error(400, body=b'{"error":{"code":"streaming_not_supported"}}')
        raise _http_error(503, {"x-request-id": "final-json"})

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(TransportError) as raised:
        chat(
            [{"role": "user", "content": "hi"}], _cfg(provider), on_delta=lambda _: None
        )
    assert sends == [True, True, False]
    assert raised.value.request_id == "final-json"


@pytest.mark.parametrize("retries", [0, 5])
def test_chat_sends_exactly_the_configured_attempts(monkeypatch, retries):
    """host.llm, titles, the reviewer and the profile probe reach the
    transport through ``chat()``'s own CallState, not the Agent runtime's."""
    import dataclasses

    sends = []

    def urlopen(req, **kwargs):
        sends.append(True)
        raise _http_error(429)

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    cfg = dataclasses.replace(_cfg(), max_retries=retries, retry_budget_s=600)

    with pytest.raises(TransportError) as raised:
        chat([{"role": "user", "content": "hi"}], cfg, on_delta=lambda _piece: None)

    assert len(sends) == retries + 1
    assert raised.value.status == 429


def test_zero_retries_still_lets_a_refused_stream_fall_back_once(monkeypatch):
    """``OPENAI4S_LLM_MAX_RETRIES=0`` means "do not retry", not "fail every
    turn behind a proxy that refuses ``stream``": the compatibility POST is a
    fallback, and a send ceiling of one used to leave it no room."""
    import dataclasses

    sends = []

    def urlopen(req, **kwargs):
        stream = bool(json.loads(req.data).get("stream"))
        sends.append(stream)
        if stream:
            raise _http_error(
                400,
                body=b'{"error":{"code":"streaming_not_supported","param":"stream"}}',
            )
        return _Resp(
            b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'
        )

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    cfg = dataclasses.replace(_cfg(), max_retries=0)

    reply = chat([{"role": "user", "content": "hi"}], cfg, on_delta=lambda _p: None)

    assert reply["content"] == "ok"
    assert sends == [True, False]


def test_zero_retries_does_not_spend_the_spare_send_on_a_retry(monkeypatch):
    import dataclasses

    sends = []

    def urlopen(req, **kwargs):
        sends.append(True)
        raise _http_error(429)

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    cfg = dataclasses.replace(_cfg(), max_retries=0)

    with pytest.raises(TransportError) as raised:
        chat([{"role": "user", "content": "hi"}], cfg, on_delta=lambda _p: None)

    assert len(sends) == 1
    assert raised.value.retries_attempted == 0


@pytest.mark.parametrize("provider", ["chatgpt", "claude"])
@pytest.mark.parametrize(
    ("retries", "prior_retry"),
    [(0, False), (1, False), (2, False), (5, False), (2, True), (5, True)],
)
def test_compatibility_limit_is_not_reported_as_exhausted_configured_retries(
    monkeypatch, provider, retries, prior_retry
):
    import dataclasses

    from openai4s.llm.models import llm_retry_outcome
    from openai4s.server.gateway import SessionRunner

    sends = []

    def urlopen(req, **kwargs):
        stream = bool(json.loads(req.data).get("stream"))
        sends.append(stream)
        if prior_retry and len(sends) == 1:
            raise _http_error(503)
        if stream:
            raise _http_error(
                400,
                body=b'{"error":{"code":"streaming_not_supported","param":"stream"}}',
            )
        raise _http_error(429)

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    cfg = dataclasses.replace(_cfg(provider), max_retries=retries)

    with pytest.raises(TransportError) as raised:
        chat([{"role": "user", "content": "hi"}], cfg, on_delta=lambda _p: None)

    assert sends == ([True, True, False] if prior_retry else [True, False])
    assert raised.value.retries_attempted == int(prior_retry)
    assert llm_retry_outcome(raised.value) == "request_limit"
    for language in ("zh", "en"):
        message = SessionRunner._friendly_error(raised.value, language=language)
        assert "OPENAI4S_LLM_MAX_RETRIES" not in message
        assert ("单次请求" if language == "zh" else "per-request") in message


def test_a_streaming_chat_honours_the_configured_per_wait_cap(monkeypatch):
    """``chat(on_delta=...)`` goes through ``post_sse``; a cap left at the old
    8 s literal there would clip a configured 60 s cap on the path the
    workbench actually uses."""
    import dataclasses

    ranges = []

    def urlopen(req, **kwargs):
        raise _http_error(429)

    def jitter(low, high):
        ranges.append((low, high))
        return low

    monkeypatch.setattr("openai4s.llm.transport._urlopen", urlopen)
    monkeypatch.setattr("openai4s.llm.transport.random.uniform", jitter)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    cfg = dataclasses.replace(
        _cfg(), max_retries=3, retry_budget_s=600, retry_max_delay_s=60
    )

    with pytest.raises(TransportError):
        chat([{"role": "user", "content": "hi"}], cfg, on_delta=lambda _p: None)

    assert ranges == [(2.0, 4.0), (4.0, 8.0), (8.0, 16.0)]

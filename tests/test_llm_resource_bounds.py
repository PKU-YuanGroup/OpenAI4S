"""Logical-call deadlines and input bounds against real local HTTP peers."""

import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openai4s.config import LLMConfig
from openai4s.llm import transport
from openai4s.llm.models import LLMError, TransportError, llm_failure_code

pytestmark = pytest.mark.stubbed_backend


@contextlib.contextmanager
def peer(respond):
    stopped = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append(self.path)
            try:
                respond(self, stopped)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/chat/completions", requests
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def headers(handler, code=200, content_type="application/json"):
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("X-Request-Id", "local-resource-probe")
    handler.end_headers()


def drip(handler, stopped, chunks):
    for chunk in chunks:
        if stopped.is_set():
            return
        handler.wfile.write(chunk)
        handler.wfile.flush()
        stopped.wait(0.02)


@pytest.mark.parametrize("stream", [False, True])
def test_idle_slow_headers_are_typed_and_never_replayed(stream):
    def respond(handler, stopped):
        stopped.wait(1)

    with peer(respond) as (url, requests):
        started = time.monotonic()
        with pytest.raises(TransportError) as caught:
            if stream:
                transport.post_sse(url, {}, {}, 0.1, lambda _: None)
            else:
                transport.post_json(url, {}, {}, 0.1)
        assert time.monotonic() - started < 0.6
        assert not caught.value.retryable
        assert llm_failure_code(caught.value) is None
        assert len(requests) == 1


def test_cancellation_interrupts_a_dripping_unterminated_sse_line():
    cancel = threading.Event()

    def respond(handler, stopped):
        headers(handler, content_type="text/event-stream")
        handler.wfile.write(b":")
        handler.wfile.flush()
        cancel.set()
        drip(handler, stopped, [b"x"] * 100)

    with peer(respond) as (url, requests):
        started = time.monotonic()
        with pytest.raises(TransportError, match="cancelled"):
            transport.post_sse(
                url, {}, {}, 1, lambda _: None, should_cancel=cancel.is_set
            )
        assert time.monotonic() - started < 0.6
        assert len(requests) == 1


@pytest.mark.parametrize("provider", ["chatgpt", "claude", "gpt"])
def test_native_terminal_returns_while_peer_keeps_connection_open(provider):
    from openai4s import llm

    if provider == "gpt":
        provider = next(
            name for name, spec in llm.PROVIDERS.items() if spec["wire"] == "responses"
        )
        events = [
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                    "output": [],
                },
            }
        ]
    elif provider == "claude":
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
    else:
        events = [
            {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
        ]

    def respond(handler, stopped):
        headers(handler, content_type="text/event-stream")
        for event in events:
            handler.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
        handler.wfile.flush()
        stopped.wait(1.5)

    with peer(respond) as (url, requests):
        cfg = LLMConfig(
            provider=provider,
            base_url=url.rsplit("/chat/completions", 1)[0],
            api_key="test-key",
            model="test-model",
        )
        started = time.monotonic()
        reply = llm.chat(
            [{"role": "user", "content": "hi"}], cfg, on_delta=lambda _: None
        )
        assert time.monotonic() - started < 0.7
        assert reply["usage"]["total_tokens"] == 4
        assert len(requests) == 1


@pytest.mark.parametrize("expire", [False, True])
def test_dns_return_cannot_start_an_expired_or_cancelled_connection(
    monkeypatch, expire
):
    import socket

    from openai4s.agent import runtime
    from openai4s.agent.runtime import ChatModel

    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    sockets = []
    budget = runtime._DetachedCallBudget(128, per_scope_limit=4)
    monkeypatch.setattr(runtime, "_PROVIDER_CALL_BUDGET", budget)

    def resolve(*_args):
        entered.set()
        assert release.wait(3)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *_args: sockets.append(1))

    class Cancel:
        def cancelled(self):
            return cancel.is_set()

    def request(_messages, cfg, **kwargs):
        probe = kwargs["should_cancel"]
        return transport.post_json(
            "http://127.0.0.1:1",
            {},
            {},
            2,
            should_cancel=probe,
            call_state=probe.call_state,
        )

    cfg = LLMConfig(total_timeout_s=1)
    model = ChatModel(cfg, request, cancellation=Cancel(), call_scope="old")
    result = []

    def own():
        try:
            result.append(model.complete([], lambda _: None))
        except LLMError as error:
            result.append(error)

    owner = threading.Thread(target=own)
    owner.start()
    try:
        assert entered.wait(1)
        if not expire:
            cancel.set()
        owner.join(1.7)
        assert not owner.is_alive()
        assert budget.outstanding("old") == 1
        release.set()
        until = time.monotonic() + 1
        while budget.outstanding("old") and time.monotonic() < until:
            time.sleep(0.01)
        assert budget.outstanding("old") == 0
        assert sockets == []
        if expire:
            assert llm_failure_code(result[0]) == "llm_deadline_exceeded"
        else:
            assert result[0]["finish_reason"] == "cancelled"
    finally:
        release.set()
        owner.join(1)


@pytest.mark.parametrize("kind", ["headers", "json", "error", "heartbeat"])
def test_one_deadline_bounds_slow_headers_body_error_and_heartbeat(kind):
    def respond(handler, stopped):
        if kind == "headers":
            raw = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{}"
            drip(handler, stopped, [bytes([byte]) for byte in raw])
        elif kind == "heartbeat":
            headers(handler, content_type="text/event-stream")
            drip(handler, stopped, [b": heartbeat\n\n"] * 70)
        else:
            headers(handler, 401 if kind == "error" else 200)
            body = json.dumps({"content": "x" * 70}).encode()
            drip(handler, stopped, [bytes([byte]) for byte in body])

    state = transport.CallState()
    with peer(respond) as (url, requests):
        state.deadline = time.monotonic() + 0.25
        started = time.monotonic()
        with pytest.raises(LLMError) as caught:
            if kind == "heartbeat":
                transport.post_sse(url, {}, {}, 0.15, lambda _e: None, call_state=state)
            else:
                transport.post_json(url, {}, {}, 0.15, call_state=state)
        assert llm_failure_code(caught.value) == "llm_deadline_exceeded"
        assert time.monotonic() - started < 0.9
        assert len(requests) == 1
        if kind == "error":
            assert caught.value.status == 401
            assert caught.value.request_id == "local-resource-probe"


@pytest.mark.parametrize("kind", ["json", "line", "event", "total"])
def test_streamed_input_is_bounded_before_a_tool_event_is_dispatched(monkeypatch, kind):
    seen = []
    if kind == "json":
        monkeypatch.setattr(transport, "MAX_JSON_BYTES", 128, raising=False)
        payload = json.dumps({"arguments": "x" * 1024}).encode()
    elif kind == "line":
        monkeypatch.setattr(transport, "MAX_SSE_LINE_BYTES", 64, raising=False)
        payload = b'data: {"arguments":"' + b"x" * 1024 + b'"}\n\n'
    elif kind == "event":
        monkeypatch.setattr(transport, "MAX_SSE_EVENT_BYTES", 128, raising=False)
        raw = json.dumps({"arguments": ["x" * 24] * 30}, indent=2)
        payload = (
            b"\n".join(b"data: " + line.encode() for line in raw.splitlines()) + b"\n\n"
        )
    else:
        monkeypatch.setattr(transport, "MAX_SSE_BYTES", 128, raising=False)
        payload = (
            b": heartbeat\n\n" * 30 + b'data: {"arguments":"must not execute"}\n\n'
        )

    def respond(handler, _stopped):
        headers(handler)
        handler.wfile.write(payload)

    with peer(respond) as (url, requests):
        with pytest.raises(LLMError) as caught:
            if kind == "json":
                transport.post_json(url, {}, {}, 2)
            else:
                transport.post_sse(url, {}, {}, 2, seen.append)
        assert llm_failure_code(caught.value) == "llm_response_too_large"
        assert seen == []
        assert len(requests) == 1


def test_error_body_is_capped_without_losing_status_or_request_identity(monkeypatch):
    monkeypatch.setattr(transport, "MAX_ERROR_BYTES", 128, raising=False)

    def respond(handler, _stopped):
        headers(handler, 401)
        handler.wfile.write(b"x" * 8192)

    with peer(respond) as (url, requests):
        with pytest.raises(TransportError) as caught:
            transport.post_json(url, {}, {}, 2)
        error = caught.value
        assert error.status == 401 and not error.retryable
        assert error.request_id == "local-resource-probe"
        assert len(error.body.encode()) < 256
        assert "omitted" in error.body.lower()
        assert len(requests) == 1


def test_done_ends_the_stream_without_waiting_for_connection_close():
    def respond(handler, stopped):
        headers(handler, content_type="text/event-stream")
        handler.wfile.write(b'data: {"ok":true}\n\ndata: [DONE]\n\n')
        handler.wfile.flush()
        stopped.wait(1.2)

    with peer(respond) as (url, _requests):
        seen = []
        started = time.monotonic()
        transport.post_sse(url, {}, {}, 2, seen.append)
        assert seen == [{"ok": True}]
        assert time.monotonic() - started < 0.6


@pytest.mark.parametrize("seconds", [1, 600, 3600])
def test_total_timeout_configuration_accepts_the_documented_range(seconds):
    assert LLMConfig(total_timeout_s=seconds).total_timeout_s == seconds


@pytest.mark.parametrize("seconds", [0, -1, 3601, float("nan"), float("inf"), True])
def test_total_timeout_configuration_rejects_invalid_values(seconds):
    with pytest.raises(ValueError):
        LLMConfig(total_timeout_s=seconds)


@pytest.mark.parametrize("raw", ["", "abc", "0", "4000"])
def test_total_timeout_environment_value_reports_its_own_range(monkeypatch, raw):
    """The factory must not parse: a ``float()`` there runs inside ``__init__``
    and raises before ``__post_init__`` can name the range, so a blank or
    non-numeric env var used to abort the daemon's boot with a bare
    "could not convert string to float"."""

    monkeypatch.setenv("OPENAI4S_LLM_TOTAL_TIMEOUT", raw)
    with pytest.raises(ValueError, match="finite number from 1 to 3600 seconds"):
        LLMConfig()


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True, "x"])
def test_request_timeout_is_refused_before_it_reaches_the_exchange(seconds):
    """``timeout_s`` becomes ``HTTPExchangeDeadline(idle_timeout=...)``, which
    refuses a non-positive value with a bare ``ValueError`` that no transport
    handler catches and ``llm_failure_code`` cannot classify."""

    with pytest.raises(ValueError, match="greater than 0 seconds"):
        LLMConfig(timeout_s=seconds)


def test_valid_request_timeout_is_normalized_to_a_float():
    assert LLMConfig(timeout_s=30).timeout_s == 30.0


def test_text_backpressure_preserves_unicode_and_bounds_pending_bytes():
    import queue

    from openai4s.agent.stream_buffer import TextBuffer

    finished = threading.Event()
    buffer = TextBuffer(cancelled=lambda: False, remaining=lambda: 5.0, limit=17)
    original = "甲🙂abc" * 50

    def produce():
        try:
            buffer.put(original)
        finally:
            finished.set()

    thread = threading.Thread(target=produce, daemon=True)
    thread.start()
    assert not finished.wait(0.05), "a fast producer bypassed backpressure"
    pieces = []
    while not finished.is_set() or not buffer.empty():
        try:
            pieces.append(buffer.get(timeout=0.2))
        except queue.Empty:
            pass
    thread.join(timeout=2)
    assert "".join(pieces) == original
    assert buffer.peak_bytes <= 17 and buffer.pending_bytes == 0


@pytest.mark.parametrize("stop_kind", ["cancel", "deadline", "close"])
def test_waiting_text_producer_is_released_on_cancel_deadline_or_close(stop_kind):
    from openai4s.agent.stream_buffer import TextBuffer
    from openai4s.llm.models import LLMDeadlineExceeded

    stop = threading.Event()
    entered = threading.Event()
    finished = threading.Event()
    errors = []
    state = transport.CallState()
    buffer = TextBuffer(cancelled=stop.is_set, remaining=state.remaining, limit=16)
    buffer.put("x" * 16)

    def produce():
        entered.set()
        try:
            buffer.put("y")
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    thread = threading.Thread(target=produce, daemon=True)
    thread.start()
    assert entered.wait(2)
    assert not finished.wait(0.05)
    if stop_kind == "cancel":
        stop.set()
    elif stop_kind == "deadline":
        state.deadline = time.monotonic() - 1
    else:
        buffer.close()
    assert finished.wait(1), "an abandoned producer kept waiting for its consumer"
    thread.join(timeout=1)
    assert bool(errors) is (stop_kind == "deadline")
    if errors:
        assert isinstance(errors[0], LLMDeadlineExceeded)


def test_a_legacy_model_without_cancel_keyword_remains_callable():
    from openai4s.agent.runtime import ChatModel

    def legacy(messages, cfg, *, tools):
        assert messages == [{"role": "user", "content": "hi"}] and tools == ()
        return {"content": "ok"}

    model = ChatModel(LLMConfig(), legacy)
    assert model.complete([{"role": "user", "content": "hi"}], lambda _x: None) == {
        "content": "ok"
    }


def test_consumer_exception_releases_a_blocked_model_producer():
    from openai4s.agent.runtime import ChatModel

    finished = threading.Event()

    def provider(messages, cfg, *, on_delta, **_kwargs):
        try:
            on_delta("🙂" * (1024 * 1024))
            return {"content": "unused"}
        finally:
            finished.set()

    def reject(_text):
        raise ValueError("consumer stopped")

    model = ChatModel(LLMConfig(), provider, stream=True)
    with pytest.raises(ValueError, match="consumer stopped"):
        model.complete([], reject)
    assert finished.wait(1), "producer stayed blocked after its owner raised"

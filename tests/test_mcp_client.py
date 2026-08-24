"""Offline MCP protocol and child-environment contracts."""

from __future__ import annotations

import errno
import http.client
import json
import os
import socket
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from openai4s import mcp_client, mcp_http
from openai4s.http_deadline import (
    HTTPExchangeDeadline,
    HTTPExchangeTimeout,
    _DeadlineHTTPSConnection,
)
from openai4s.mcp_client import (
    OPENAI4S_PYTHON,
    MCPConnection,
    MCPError,
    MCPManager,
    _connector_environment,
)
from openai4s.mcp_http import MCPHTTPConnection
from openai4s.mcp_protocol import MCPOversizedResponse, MCPTimeout
from openai4s.mcp_servers.example_server import RESOURCE_URI


class _HTTPHeaders(dict):
    def get(self, key, default=None):
        wanted = str(key).casefold()
        return next(
            (value for name, value in self.items() if str(name).casefold() == wanted),
            default,
        )


def test_portable_openai4s_python_is_resolved_only_at_spawn_time():
    stored = [OPENAI4S_PYTHON, "-m", "openai4s.mcp_servers.example_server"]

    argv = MCPManager._argv({"command": stored, "args": ["--stdio"]})

    assert stored[0] == OPENAI4S_PYTHON
    assert argv == [
        sys.executable,
        "-m",
        "openai4s.mcp_servers.example_server",
        "--stdio",
    ]


def test_an_explicit_interpreter_for_an_in_tree_module_is_not_overridden():
    """Only our own token is ours to expand.

    Matching on the module name alone also captured a row an operator wrote by
    hand -- a conda interpreter chosen because it carries the scientific stack
    -- and ran it under the daemon's venv while the connector editor kept
    displaying the path they typed. The configuration shown and the one
    executed have to be the same configuration.
    """
    operator_choice = [
        "/opt/conda/envs/protein/bin/python",
        "-m",
        "openai4s.mcp_servers.protein_design",
    ]

    assert MCPManager._argv({"command": list(operator_choice)}) == operator_choice


class _BoundedTransport:
    """The read-bound carrier every socket-backed response has beneath it.

    The reader refuses to read a body it cannot bound, so a double that omits
    this is not a cheaper fake of an HTTP response -- it is a fake of a state
    the transport does not produce.
    """

    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class _HTTPResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = body
        self._offset = 0
        self.headers = _HTTPHeaders(headers or {})
        self.socket = _BoundedTransport()
        self.fp = types.SimpleNamespace(raw=types.SimpleNamespace(_sock=self.socket))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _rpc_response(message, *, session=None, media_type="application/json"):
    body = json.dumps(message).encode("utf-8")
    if media_type == "text/event-stream":
        body = b"event: message\ndata: " + body + b"\n\n"
    headers = {"Content-Type": media_type}
    if session is not None:
        headers["Mcp-Session-Id"] = session
    return _HTTPResponse(200, body, headers)


def _permit_fake_http(monkeypatch, opener):
    from openai4s import egress, webtools

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: opener)
    monkeypatch.setattr(webtools, "network_allowed", lambda: True)
    monkeypatch.setattr(webtools, "guard_url", lambda _url: None)
    monkeypatch.setattr(egress, "check_url", lambda _url: None)


def _socketpair_deadline_opener(
    exchange: HTTPExchangeDeadline,
    client: socket.socket,
) -> urllib.request.OpenerDirector:
    """urllib opener whose HTTPS connection uses one deterministic socketpair."""

    class _SocketpairConnection(_DeadlineHTTPSConnection):
        def connect(self) -> None:
            self.sock = client
            self._absolute_deadline._register_socket(client)
            client.settimeout(self._absolute_deadline.remaining())

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        exchange.https_handler(_SocketpairConnection),
    )


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "initial_response",
    [
        b"HTTP/1.1 200 OK\r\nX-Slow: unfinished",
        b"HTTP/1.",
    ],
)
def test_absolute_exchange_deadline_interrupts_opener_during_response_headers(
    initial_response,
):
    """A slow header cannot turn urllib's idle timeout into an infinite open.

    The peer supplies a valid status and starts a header, but never terminates
    that header. The body reader is never reached: only closing the socket used
    inside ``HTTPResponse.begin()`` can make this deterministic call return.
    """

    secret = "timer-secret-canary"
    client, peer = socket.socketpair()
    peer.sendall(initial_response)
    stop_drip = threading.Event()

    def drip_header() -> None:
        # Every byte arrives well inside urllib's relative socket timeout. A
        # legacy opener therefore remains in ``HTTPResponse.begin`` for the
        # whole two-second producer lifetime instead of timing out at 150 ms.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not stop_drip.wait(0.025):
            try:
                peer.sendall(b"x")
            except OSError:
                return

    producer = threading.Thread(target=drip_header, name="slow-header-upstream")
    producer.start()
    exchange = HTTPExchangeDeadline(0.15)
    started = time.monotonic()
    timer = None
    try:
        with pytest.raises(HTTPExchangeTimeout) as caught:
            with exchange:
                timer = exchange._timer
                opener = _socketpair_deadline_opener(exchange, client)
                exchange.open(
                    opener,
                    urllib.request.Request(
                        "https://deadline.invalid/slow-headers",
                        headers={"Authorization": f"Bearer {secret}"},
                    ),
                )
    finally:
        stop_drip.set()
        peer.close()
        client.close()
        producer.join(1.0)

    elapsed = time.monotonic() - started
    assert 0.10 <= elapsed < 1.0
    assert not producer.is_alive()
    assert timer is not None and not timer.is_alive()
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in repr(exchange.__dict__)


@pytest.mark.stubbed_backend
def test_absolute_exchange_deadline_preserves_http_status_errors():
    exchange = HTTPExchangeDeadline(1.0)
    status_error = urllib.error.HTTPError(
        "https://deadline.invalid/status",
        404,
        "not found",
        {},
        None,
    )

    class _Opener:
        def open(self, _request, timeout=None):
            assert timeout is not None and timeout > 0
            exchange._expire()
            raise status_error

    with pytest.raises(urllib.error.HTTPError) as caught:
        exchange.open(
            _Opener(),
            urllib.request.Request("https://deadline.invalid/status"),
        )

    assert caught.value is status_error


@pytest.mark.stubbed_backend
def test_absolute_exchange_deadline_closes_a_response_rejected_after_open():
    exchange = HTTPExchangeDeadline(1.0)

    class _Response:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    response = _Response()

    class _Opener:
        def open(self, _request, timeout=None):
            assert timeout is not None and timeout > 0
            exchange._expire()
            return response

    with pytest.raises(HTTPExchangeTimeout):
        exchange.open(
            _Opener(),
            urllib.request.Request("https://deadline.invalid/rejected-response"),
        )

    assert response.close_calls == 1


@pytest.mark.stubbed_backend
def test_absolute_exchange_deadline_preserves_a_normal_header_and_body_response():
    """The same opener path admits an ordinary response and cancels its timer."""

    client, peer = socket.socketpair()
    peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    exchange = HTTPExchangeDeadline(1.0)
    timer = None
    try:
        with exchange:
            timer = exchange._timer
            opener = _socketpair_deadline_opener(exchange, client)
            with exchange.open(
                opener,
                urllib.request.Request("https://deadline.invalid/complete"),
            ) as response:
                assert response.status == 200
                assert response.read() == b"{}"
    finally:
        peer.close()
        client.close()

    assert timer is not None and not timer.is_alive()


@pytest.mark.stubbed_backend
def test_streamable_http_uses_the_exchange_deadline_during_initialize(
    monkeypatch,
):
    """The MCP transport, not just its helper, owns the header watchdog."""

    from openai4s import egress, mcp_http, webtools

    client, peer = socket.socketpair()
    peer.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: unfinished")
    exchanges = []

    class _SocketpairExchange(HTTPExchangeDeadline):
        def __init__(self, timeout):
            super().__init__(timeout)
            exchanges.append(self)

        def build_opener(self, *_handlers):
            return _socketpair_deadline_opener(self, client)

    monkeypatch.setattr(mcp_http, "HTTPExchangeDeadline", _SocketpairExchange)
    monkeypatch.setattr(webtools, "network_allowed", lambda: True)
    monkeypatch.setattr(webtools, "guard_url", lambda _url: None)
    monkeypatch.setattr(egress, "check_url", lambda _url: None)
    secret = "mcp-deadline-secret-canary"
    try:
        with pytest.raises(MCPTimeout) as caught:
            MCPHTTPConnection(
                "https://deadline.invalid/mcp",
                headers={"X-Agent-Plan-Key": secret},
                timeout=0.15,
            )
    finally:
        peer.close()
        client.close()

    assert len(exchanges) == 1
    assert exchanges[0]._timer is not None
    assert not exchanges[0]._timer.is_alive()
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in repr(exchanges[0].__dict__)


@pytest.mark.stubbed_backend
def test_streamable_http_body_reader_prefers_one_raw_read_per_deadline_check():
    """A response with ``read1`` must never fall back to buffered ``read``."""

    class _Socket:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    class _Raw:
        def __init__(self, sock):
            self._sock = sock

    class _FP:
        def __init__(self, sock):
            self.raw = _Raw(sock)

    class _Read1OnlyResponse:
        def __init__(self):
            self.headers = _HTTPHeaders()
            self.socket = _Socket()
            self.fp = _FP(self.socket)
            self.chunks = iter((b"one", b"-read", b""))
            self.read1_calls = 0

        def read1(self, _size):
            self.read1_calls += 1
            return next(self.chunks)

        def read(self, _size=-1):
            raise AssertionError("buffered read must not run when read1 exists")

    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 2.0
    response = _Read1OnlyResponse()

    exchange = HTTPExchangeDeadline(2.0)
    body = connection._read_body(response, exchange)

    assert body == b"one-read"
    assert response.read1_calls == 3
    assert len(response.socket.timeouts) == 3
    assert all(0 < value <= 2.0 for value in response.socket.timeouts)


@pytest.mark.stubbed_backend
def test_streamable_http_body_reader_stops_when_the_transport_retires():
    """A complete Content-Length body must not fail on its own closed socket.

    CPython 3.11+ ``HTTPResponse.read1`` calls ``_close_conn()`` on the same
    call that returns the final content byte, and urllib closed the connection
    socket back when the headers arrived, so that read drops the last I/O
    reference and the descriptor goes away.  Arming a read timeout afterwards
    raised ``OSError`` (EBADF) and turned a fully-read JSON-RPC reply into a
    transport failure -- invisible to every fake-socket test, because only a
    real socket refuses.
    """

    class _RetiredSocket:
        def __init__(self):
            self.timeouts = []
            self.fd = 9

        def settimeout(self, value):
            if self.fd < 0:
                raise OSError(errno.EBADF, "Bad file descriptor")
            self.timeouts.append(value)

        def fileno(self):
            return self.fd

    class _Raw:
        def __init__(self, sock):
            self._sock = sock

    class _FP:
        def __init__(self, sock):
            self.raw = _Raw(sock)

    class _EndOfBodyClosingResponse:
        def __init__(self, body):
            self.headers = _HTTPHeaders({"Content-Length": str(len(body))})
            self.socket = _RetiredSocket()
            self.fp = _FP(self.socket)
            self._body = body
            self._offset = 0
            self.length = len(body)
            self.read1_calls = 0

        def isclosed(self):
            return self.fp is None

        def read1(self, size=-1):
            # The stdlib signature, so a reader that drops the size argument
            # or passes the default -1 is modelled instead of silently
            # returning b"" from a backwards slice.
            self.read1_calls += 1
            if self.fp is None:
                return b""
            if size is None or size < 0:
                size = self.length
            chunk = self._body[self._offset : self._offset + min(size, 8)]
            self._offset += len(chunk)
            self.length -= len(chunk)
            if not chunk or not self.length:
                self.fp = None
                self.socket.fd = -1
            return chunk

        def read(self, _size=-1):
            raise AssertionError("buffered read must not run when read1 exists")

    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()
    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 2.0
    response = _EndOfBodyClosingResponse(payload)

    exchange = HTTPExchangeDeadline(2.0)
    body = connection._read_body(response, exchange)

    assert body == payload
    assert response.isclosed() is True
    assert response.socket.fileno() == -1
    assert len(response.socket.timeouts) == response.read1_calls
    assert all(0 < value <= 2.0 for value in response.socket.timeouts)


@pytest.mark.stubbed_backend
def test_streamable_http_body_watchdog_interrupts_a_chunked_slow_drip():
    client, peer = socket.socketpair()
    peer.sendall(
        b"HTTP/1.1 200 OK\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Content-Type: application/json\r\n"
        b"\r\n"
    )
    stop_drip = threading.Event()

    def drip_partial_chunk_line() -> None:
        for piece in (b"1", b"\r"):
            if stop_drip.wait(0.05):
                return
            try:
                peer.sendall(piece)
            except OSError:
                return

    producer = threading.Thread(
        target=drip_partial_chunk_line,
        name="slow-chunked-body-upstream",
    )
    producer.start()
    exchange = HTTPExchangeDeadline(0.2)
    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 0.2
    started = time.monotonic()
    try:
        with pytest.raises(MCPTimeout, match="exceeded 0.2s"):
            with exchange:
                opener = _socketpair_deadline_opener(exchange, client)
                with exchange.open(
                    opener,
                    urllib.request.Request("https://deadline.invalid/slow-body"),
                ) as response:
                    connection._read_body(response, exchange)
    finally:
        stop_drip.set()
        peer.close()
        client.close()
        producer.join(1.0)

    assert 0.15 <= time.monotonic() - started < 1.0
    assert exchange.expired is True
    assert not producer.is_alive()


@pytest.mark.stubbed_backend
def test_streamable_http_slow_drip_cannot_refresh_the_absolute_timeout(monkeypatch):
    """Each byte may arrive in time, but the whole body still has one budget."""

    # The clock the bounded reader consults now that the loop is shared: the
    # budget is a property of the reader, not of either transport's module.
    from openai4s import http_deadline

    class _Clock:
        now = 100.0

        @classmethod
        def monotonic(cls):
            return cls.now

    class _Socket:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    class _Raw:
        def __init__(self, sock):
            self._sock = sock

    class _FP:
        def __init__(self, sock):
            self.raw = _Raw(sock)

    class _SlowDripResponse:
        def __init__(self):
            self.headers = _HTTPHeaders()
            self.socket = _Socket()
            self.fp = _FP(self.socket)
            self.read1_calls = 0

        def read1(self, _size):
            self.read1_calls += 1
            _Clock.now += 0.4
            return b"x"

        def read(self, _size=-1):
            raise AssertionError("slow-drip protection requires read1")

    monkeypatch.setattr(http_deadline.time, "monotonic", _Clock.monotonic)
    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 1.0
    response = _SlowDripResponse()

    exchange = HTTPExchangeDeadline(1.0)
    with pytest.raises(MCPTimeout, match="exceeded 1s"):
        connection._read_body(response, exchange)

    assert response.read1_calls == 3
    assert response.socket.timeouts == pytest.approx([1.0, 0.6, 0.2])
    assert all(
        later < earlier
        for earlier, later in zip(
            response.socket.timeouts, response.socket.timeouts[1:]
        )
    )


@pytest.mark.stubbed_backend
def test_streamable_http_rejects_truncated_and_unbounded_bodies():
    class _ShortResponse(_HTTPResponse):
        def __init__(self, body, declared):
            super().__init__(
                200,
                body,
                {"Content-Length": str(declared)},
            )
            self.length = declared

        def read(self, size=-1):
            chunk = super().read(size)
            self.length -= len(chunk)
            return chunk

    class _UnboundedResponse:
        headers = _HTTPHeaders()

        def read(self, _size=-1):
            raise AssertionError("an unbounded response must not be read")

    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 2.0
    body = b'{"jsonrpc":"2.0"}'
    short = _ShortResponse(body, len(body) + 9)

    with pytest.raises(MCPError, match="ended before its declared length"):
        connection._read_body(short, HTTPExchangeDeadline(2.0))
    assert short.length == 9

    with pytest.raises(MCPError, match="no bounded read transport"):
        connection._read_body(_UnboundedResponse(), HTTPExchangeDeadline(2.0))


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("failure_point", ["arm", "read", "empty"])
def test_streamable_http_watchdog_failures_remain_timeouts(failure_point):
    exchange = HTTPExchangeDeadline(2.0)

    class _Transport:
        def settimeout(self, _value):
            if failure_point == "arm":
                exchange._expire()
                raise OSError(errno.EBADF, "watchdog closed the socket")

    class _Response:
        headers = _HTTPHeaders(
            {"Content-Length": "1"} if failure_point == "empty" else {}
        )
        length = 1 if failure_point == "empty" else None

        def __init__(self):
            transport = _Transport()
            self.fp = types.SimpleNamespace(raw=types.SimpleNamespace(_sock=transport))

        def read1(self, _size):
            exchange._expire()
            if failure_point == "read":
                raise http.client.IncompleteRead(b"")
            return b""

    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 2.0

    with pytest.raises(MCPTimeout, match="exceeded 2s"):
        connection._read_body(_Response(), exchange)


@pytest.mark.stubbed_backend
def test_streamable_http_complete_body_survives_a_late_clock_without_expiry():
    response = _HTTPResponse(200, b"", {"Content-Length": "0"})
    response.length = 0
    exchange = HTTPExchangeDeadline(2.0)
    exchange.deadline = time.monotonic() - 1.0
    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 2.0

    assert connection._read_body(response, exchange) == b""
    assert exchange.expired is False


@pytest.mark.stubbed_backend
def test_streamable_http_observed_truncation_survives_a_late_clock_without_expiry():
    response = _HTTPResponse(200, b"", {"Content-Length": "1"})
    response.length = 1
    response.isclosed = lambda: True
    exchange = HTTPExchangeDeadline(2.0)
    exchange.deadline = time.monotonic() - 1.0
    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 2.0

    with pytest.raises(MCPError, match="ended before its declared length"):
        connection._read_body(response, exchange)
    assert exchange.expired is False


@pytest.mark.stubbed_backend
def test_streamable_http_body_cap_covers_header_stream_and_exact_boundary(
    monkeypatch,
):
    monkeypatch.setattr(mcp_http, "_MAX_FRAME_BYTES", 4)
    connection = object.__new__(MCPHTTPConnection)
    connection._timeout = 2.0

    announced = _HTTPResponse(200, b"1234", {"Content-Length": "5"})
    with pytest.raises(MCPOversizedResponse):
        connection._read_body(announced, HTTPExchangeDeadline(2.0))

    streamed = _HTTPResponse(200, b"12345")
    with pytest.raises(MCPOversizedResponse):
        connection._read_body(streamed, HTTPExchangeDeadline(2.0))

    exact = _HTTPResponse(200, b"1234")
    assert connection._read_body(exact, HTTPExchangeDeadline(2.0)) == b"1234"


def test_connector_environment_is_allowlisted_and_explicit_env_is_the_secret_boundary():
    source = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "LANG": "en_US.UTF-8",
        "OPENAI4S_LLM_API_KEY": "daemon-provider-secret",
        "AWS_SECRET_ACCESS_KEY": "daemon-cloud-secret",
        "HTTP_PROXY": "https://user:password@proxy.invalid",
        "PYTHONPATH": "/untrusted/imports",
        "NODE_OPTIONS": "--require=/untrusted/bootstrap.js",
    }

    env = _connector_environment(
        {"SCIENCE_MCP_TOKEN": "connector-secret", "MODE": 7},
        source=source,
    )

    assert env["PATH"] == "/safe/bin"
    assert env["HOME"] == "/safe/home"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["SCIENCE_MCP_TOKEN"] == "connector-secret"
    assert env["MODE"] == "7"
    assert set(env).isdisjoint(
        {
            "OPENAI4S_LLM_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "HTTP_PROXY",
            "PYTHONPATH",
            "NODE_OPTIONS",
        }
    )


def test_connector_environment_has_a_path_fallback_and_rejects_invalid_entries():
    assert _connector_environment(source={})["PATH"] == os.defpath

    with pytest.raises(MCPError, match="must be an object"):
        _connector_environment([("TOKEN", "value")], source={})
    with pytest.raises(MCPError, match="invalid connector env name"):
        _connector_environment({"BAD=NAME": "value"}, source={})
    with pytest.raises(MCPError, match="cannot be null"):
        _connector_environment({"TOKEN": None}, source={})
    with pytest.raises(MCPError, match="contains NUL"):
        _connector_environment({"TOKEN": "bad\x00value"}, source={})


def test_manager_connect_never_passes_ambient_secrets_to_popen(monkeypatch):
    captured = {}

    class CapturingConnection:
        def __init__(self, command, env=None, cwd=None, *, timeout=None):
            captured.update(command=command, env=env, cwd=cwd, timeout=timeout)

    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", "ambient-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-cloud-secret")
    monkeypatch.setattr(mcp_client, "MCPConnection", CapturingConnection)

    manager = MCPManager()
    connection = manager._connect(
        {
            "command": ["science-mcp"],
            "args": ["--stdio"],
            "env": {"SCIENCE_MCP_TOKEN": "declared-secret"},
            "cwd": "/connector/workspace",
        }
    )

    assert isinstance(connection, CapturingConnection)
    assert captured["command"] == ["science-mcp", "--stdio"]
    assert captured["cwd"] == "/connector/workspace"
    assert captured["env"]["SCIENCE_MCP_TOKEN"] == "declared-secret"
    assert "OPENAI4S_LLM_API_KEY" not in captured["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in captured["env"]


def test_connection_uses_standard_resource_and_prompt_method_shapes():
    connection = object.__new__(MCPConnection)
    calls: list[tuple[str, dict | None]] = []

    def request(method, params=None):
        calls.append((method, params))
        return {
            "resources/list": {"resources": [], "nextCursor": "r-next"},
            "resources/read": {"contents": []},
            "prompts/list": {"prompts": [], "nextCursor": "p-next"},
            "prompts/get": {"messages": []},
        }[method]

    connection._request = request

    assert connection.list_resources("r-1")["nextCursor"] == "r-next"
    assert connection.read_resource("science://dataset") == {"contents": []}
    assert connection.list_prompts("p-1")["nextCursor"] == "p-next"
    assert connection.get_prompt("analyze", {"kind": "fast"}) == {"messages": []}
    assert calls == [
        ("resources/list", {"cursor": "r-1"}),
        ("resources/read", {"uri": "science://dataset"}),
        ("prompts/list", {"cursor": "p-1"}),
        (
            "prompts/get",
            {"name": "analyze", "arguments": {"kind": "fast"}},
        ),
    ]


def test_bundled_server_supports_resources_and_prompts_end_to_end():
    manager = MCPManager()
    config = {
        "command": [
            OPENAI4S_PYTHON,
            "-m",
            "openai4s.mcp_servers.example_server",
        ]
    }
    try:
        resources = manager.list_resources("example", config)
        assert resources["resources"][0]["uri"] == RESOURCE_URI

        content = manager.read_resource("example", config, RESOURCE_URI)
        assert content["contents"][0]["uri"] == RESOURCE_URI
        assert "third-party packages" in content["contents"][0]["text"]

        prompts = manager.list_prompts("example", config)
        assert prompts["prompts"][0]["name"] == "summarize"

        rendered = manager.get_prompt(
            "example",
            config,
            "summarize",
            {"text": "alpha beta gamma"},
        )
        message = rendered["messages"][0]
        assert message["role"] == "user"
        assert "alpha beta gamma" in message["content"]["text"]
    finally:
        manager.shutdown()


def _silent_server_config(tmp_path, *, behaviour: str) -> dict:
    """A connector that answers `initialize` then misbehaves in one exact way."""
    script = tmp_path / f"srv_{behaviour}.py"
    script.write_text(
        "import json, sys, time\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    msg = json.loads(line)\n"
        "    mid = msg.get('id')\n"
        "    if mid is None:\n"
        "        continue\n"
        "    if msg.get('method') == 'initialize':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "        continue\n"
        f"    behaviour = {behaviour!r}\n"
        "    if behaviour == 'silent':\n"
        "        time.sleep(120)\n"
        "    elif behaviour == 'late':\n"
        "        time.sleep(1.5)\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{'tools':[{'name':'STALE'}]}}) + '\\n')\n"
        "        sys.stdout.flush()\n",
        encoding="utf-8",
    )
    return {"command": [sys.executable, str(script)]}


def test_a_silent_connector_times_out_instead_of_holding_its_caller_forever(tmp_path):
    """`_read_reply` looped on `readline()` with no deadline.

    A connector that accepted a request and never answered held its caller for
    the life of the process. Worse, `MCPManager.get` takes its own lock across
    connect, so one silent connector wedged every other connector too -- and a
    hung call from a kernel cell survived cell recovery, leaving MCP dead
    process-wide.
    """
    connection = MCPConnection(
        _silent_server_config(tmp_path, behaviour="silent")["command"], timeout=1.0
    )
    try:
        started = time.monotonic()
        with pytest.raises(MCPError) as raised:
            connection.list_tools()
        elapsed = time.monotonic() - started
        assert elapsed < 20, f"took {elapsed:.1f}s -- the deadline did not apply"
        assert "exceeded" in str(raised.value)
    finally:
        connection.close()


def test_a_late_reply_is_discarded_rather_than_read_as_the_next_answer(tmp_path):
    """The reason a bare `readline` timeout would have been a regression.

    After a request gives up, the server may still answer it. With one shared
    stream and no demux, that stale reply is sitting in the pipe for the NEXT
    request to read as its own -- a caller asking B and being handed A's answer,
    silently and with the right JSON shape.
    """
    connection = MCPConnection(
        _silent_server_config(tmp_path, behaviour="late")["command"], timeout=5.0
    )
    # Process startup is not the latency claim in this test. Keep the request
    # deadline short only after the real subprocess has completed its handshake.
    connection._timeout = 0.3
    try:
        with pytest.raises(MCPError):
            connection.list_tools()  # abandons id 2; the server answers it later
        time.sleep(2.0)  # the stale reply lands while nobody is waiting

        # The next request must not be handed the abandoned one's result.
        with pytest.raises(MCPError) as raised:
            connection.list_tools()
        assert "STALE" not in str(raised.value)
    finally:
        connection.close()


def test_a_closed_connection_wakes_its_waiters(tmp_path):
    """Closing must not leave a caller blocked on a reply that cannot come."""
    connection = MCPConnection(
        _silent_server_config(tmp_path, behaviour="silent")["command"], timeout=30.0
    )
    results: list[str] = []

    def call():
        try:
            connection.list_tools()
        except MCPError as error:
            results.append(str(error))

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    time.sleep(0.3)
    connection.close()
    worker.join(timeout=10)
    assert not worker.is_alive(), "close() left a caller blocked"
    assert results and "closed" in results[0].lower()


def test_editing_or_disabling_a_connector_drops_its_cached_process(tmp_path):
    """A cached connection outlived the configuration that created it.

    Only DELETE called `disconnect`. Editing a connector's command or env wrote
    the new row and left the old child running and answering, so the connector
    the user just reconfigured kept serving from the previous configuration --
    including the previous credentials. Disabling one wrote `enabled=0` and
    likewise left the process alive, so "off" meant "hidden", not "stopped".
    """
    from openai4s.config import Config, LLMConfig
    from openai4s.server import gateway as gateway_mod

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            return None

    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
        handler = object.__new__(handler_cls)
        replies: list = []
        handler._query = lambda: {}
        handler._json = lambda obj, code=200: replies.append((code, obj))

        dropped: list[str] = []
        real = mcp_client.manager()
        original = real.disconnect
        real.disconnect = lambda cid: dropped.append(cid)  # type: ignore[assignment]
        try:
            handler._body = lambda: {
                "connector_id": "c-1",
                "name": "c1",
                "command": ["true"],
            }
            handler._api("POST", "/connectors")
            assert dropped == ["c-1"], "an edit must drop the stale process first"

            dropped.clear()
            handler._body = lambda: {"enabled": False}
            handler._api("PUT", "/connectors/c-1/enabled")
            assert dropped == ["c-1"], "disabling must stop the process"

            dropped.clear()
            handler._body = lambda: {"enabled": True}
            handler._api("PUT", "/connectors/c-1/enabled")
            assert dropped == [], "enabling need not drop anything"
        finally:
            real.disconnect = original  # type: ignore[assignment]
    finally:
        runner.close()


@pytest.mark.stubbed_backend
def test_streamable_http_uses_fresh_headers_and_preserves_structured_content(
    monkeypatch,
):
    """The cached connection resolves secrets for each outbound POST.

    This also drives both response media types and the 202 notification shape;
    initialize/tools-list success alone is deliberately not interpreted here.
    """

    requests = []
    keys = iter(
        [
            "plan-init",
            "plan-notify",
            "plan-list",
            "plan-init-more-secret",
        ]
    )

    class _Opener:
        def open(self, request, timeout=None):
            payload = json.loads(request.data)
            headers = {name.casefold(): value for name, value in request.header_items()}
            requests.append((payload, headers, timeout))
            method = payload.get("method")
            if method == "initialize":
                return _rpc_response(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {"protocolVersion": "2025-06-18"},
                    },
                    session="session-a",
                )
            if method == "notifications/initialized":
                return _HTTPResponse(202)
            if method == "tools/list":
                return _rpc_response(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {"tools": [{"name": "dataPro_search"}]},
                    },
                    media_type="text/event-stream",
                )
            reflected = headers["x-agent-plan-key"]
            return _rpc_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [{"type": "text", "text": "real result"}],
                        "structuredContent": {
                            "code": 0,
                            "data": ["record"],
                            "echo": reflected,
                            reflected: "reflected-key",
                            "[REDACTED]": "literal-redacted-key-value",
                            "prior": "plan-init",
                            "plan-init": "prior-key",
                            "extended": "plan-init-more-secret",
                        },
                    },
                }
            )

    _permit_fake_http(monkeypatch, _Opener())
    manager = MCPManager()
    config = {
        "transport": "streamable_http",
        "url": "https://dataset.example/mcp",
        "headers_provider": lambda: {"X-Agent-Plan-Key": next(keys)},
        "timeout": 2,
    }
    try:
        tools = manager.list_tools("datapro", config)
        result = manager.call_tool(
            "datapro", config, "dataPro_search", {"query": "question"}
        )
        cached = manager._conns[("datapro", "")]
        assert cached._reflection_secrets == [
            "plan-init",
            "plan-notify",
            "plan-list",
            "plan-init-more-secret",
        ], "each distinct sent Key must occupy one bounded scrub entry"
    finally:
        manager.shutdown()

    assert tools == [{"name": "dataPro_search"}]
    structured = result["raw"]["structuredContent"]
    assert structured["code"] == 0
    assert structured["data"] == ["record"]
    assert structured["echo"] == "[REDACTED]"
    assert "[REDACTED]" in structured
    assert structured["[REDACTED]"] == "reflected-key"
    assert structured["[REDACTED]#2"] == "literal-redacted-key-value"
    assert structured["prior"] == "[REDACTED]"
    assert structured["extended"] == "[REDACTED]"
    assert "plan-init" not in json.dumps(result)
    assert "plan-init-more-secret" not in json.dumps(result)
    assert [entry[1]["x-agent-plan-key"] for entry in requests] == [
        "plan-init",
        "plan-notify",
        "plan-list",
        "plan-init-more-secret",
    ]
    assert requests[2][1]["mcp-session-id"] == "session-a"
    assert requests[2][1]["mcp-protocol-version"] == "2025-06-18"
    assert requests[0][1]["accept"] == "application/json, text/event-stream"
    assert manager._conns.get(("datapro", "")) is None


@pytest.mark.stubbed_backend
def test_shutdown_epoch_rejects_an_old_store_connection_that_finishes_late(
    monkeypatch,
):
    """A pre-shutdown DataPro header provider cannot re-enter the cache."""

    started = threading.Event()
    release = threading.Event()
    created = []
    old_errors = []

    class _Connection:
        command = ["streamable_http"]

        def __init__(self, generation):
            self.generation = generation
            self.closed = False

        def faulted(self):
            return self.closed

        def list_tools(self):
            return [{"name": self.generation}]

        def close(self):
            self.closed = True
            return True

    def connect(config):
        connection = _Connection(config["generation"])
        created.append(connection)
        if connection.generation == "old":
            started.set()
            assert release.wait(5)
        return connection

    manager = MCPManager()
    monkeypatch.setattr(manager, "_connect", connect)

    def old_call():
        try:
            manager.list_tools("volcengine-datapro", {"generation": "old"})
        except Exception as error:  # noqa: BLE001 - asserted below
            old_errors.append(error)

    thread = threading.Thread(target=old_call)
    thread.start()
    assert started.wait(5)
    manager.shutdown()
    assert manager.list_tools("volcengine-datapro", {"generation": "new"}) == [
        {"name": "new"}
    ]
    release.set()
    thread.join(5)

    assert not thread.is_alive()
    assert len(old_errors) == 1
    assert isinstance(old_errors[0], MCPError)
    assert created[0].closed is True
    assert manager._conns[("volcengine-datapro", "")].generation == "new"
    assert manager.list_tools("volcengine-datapro", {"generation": "new"}) == [
        {"name": "new"}
    ]
    manager.shutdown()


@pytest.mark.stubbed_backend
def test_disconnect_epoch_rejects_inflight_connection_from_pre_rotation_config(
    monkeypatch,
):
    """Saving a replacement Key invalidates a connection still initializing."""

    started = threading.Event()
    release = threading.Event()
    errors = []

    class _Connection:
        command = ["streamable_http"]

        def __init__(self, generation):
            self.generation = generation
            self.closed = False

        def faulted(self):
            return self.closed

        def list_tools(self):
            return [{"name": self.generation}]

        def close(self):
            self.closed = True
            return True

    def connect(config):
        connection = _Connection(config["generation"])
        if connection.generation == "old":
            started.set()
            assert release.wait(5)
        return connection

    manager = MCPManager()
    monkeypatch.setattr(manager, "_connect", connect)

    def old_call():
        try:
            manager.list_tools("volcengine-datapro", {"generation": "old"})
        except Exception as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    thread = threading.Thread(target=old_call)
    thread.start()
    assert started.wait(5)
    manager.disconnect("volcengine-datapro")
    release.set()
    thread.join(5)

    assert len(errors) == 1
    assert isinstance(errors[0], MCPError)
    assert manager.list_tools("volcengine-datapro", {"generation": "new"}) == [
        {"name": "new"}
    ]
    manager.shutdown()


@pytest.mark.stubbed_backend
def test_streamable_http_404_reinitializes_the_session_once(monkeypatch):
    requests = []
    expired = False

    class _Opener:
        def open(self, request, timeout=None):
            nonlocal expired
            payload = json.loads(request.data)
            requests.append(payload["method"])
            if payload["method"] == "initialize":
                session = "session-new" if expired else "session-old"
                return _rpc_response(
                    {"jsonrpc": "2.0", "id": payload["id"], "result": {}},
                    session=session,
                )
            if payload["method"] == "notifications/initialized":
                return _HTTPResponse(202)
            if payload["method"] == "tools/list" and not expired:
                expired = True
                raise urllib.error.HTTPError(request.full_url, 404, "expired", {}, None)
            return _rpc_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"tools": [{"name": "dataPro_search"}]},
                }
            )

    _permit_fake_http(monkeypatch, _Opener())
    manager = MCPManager()
    try:
        assert manager.list_tools(
            "datapro",
            {
                "transport": "streamable_http",
                "url": "https://dataset.example/mcp",
            },
        ) == [{"name": "dataPro_search"}]
    finally:
        manager.shutdown()
    assert requests.count("initialize") == 2
    assert requests[-1] == "tools/list"


@pytest.mark.stubbed_backend
def test_streamable_http_refuses_redirect_without_leaking_headers(monkeypatch):
    canary = "plan-key-must-not-escape"

    class _Opener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                canary,
                {"Location": "https://other.example/mcp?key=" + canary},
                None,
            )

    _permit_fake_http(monkeypatch, _Opener())
    manager = MCPManager()
    with pytest.raises(MCPError) as raised:
        manager.list_tools(
            "datapro",
            {
                "transport": "streamable_http",
                "url": "https://dataset.example/mcp",
                "headers": {"X-Test-Plan-Key": canary},
            },
        )
    message = str(raised.value)
    assert "redirect was refused" in message
    assert canary not in message
    assert canary not in repr(raised.value)


@pytest.mark.stubbed_backend
def test_datapro_web_route_calls_search_saves_result_and_never_projects_key(
    tmp_path, monkeypatch, caplog
):
    from openai4s import datapro
    from openai4s.config import Config, LLMConfig
    from openai4s.server import gateway as gateway_mod

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            return None

    canary = "agent-plan-route-canary-never-return"
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    runner.store.upsert_connector(
        connector_id=datapro.CONNECTOR_ID,
        name="Volcengine DataPro",
        command=datapro.managed_connector_command(),
        enabled=True,
    )
    calls = []

    class _Manager:
        def disconnect(self, connector_id, cache_scope=None):
            calls.append(("disconnect", connector_id))

        def call_tool(self, connector_id, config, tool, args):
            outbound = config["headers_provider"]()
            calls.append((connector_id, tool, args, outbound))
            echoed_key = outbound["X-Agent-Plan-Key"]
            code = 4011 if args.get("query") == "force 4011" else 0
            return {
                "is_error": False,
                "text": "echo " + echoed_key,
                "raw": {
                    "content": [{"type": "text", "text": "echo " + echoed_key}],
                    "structuredContent": {
                        "code": code,
                        "records": [{"title": "evidence", "echo": echoed_key}],
                        echoed_key: "reflected-key",
                    },
                },
            }

    monkeypatch.setattr(mcp_client, "manager", lambda: _Manager())
    try:
        handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
        handler = object.__new__(handler_cls)
        replies = []
        handler._query = lambda: {}
        handler._json = lambda obj, code=200: replies.append((code, obj))

        handler._body = lambda: {"agent_plan_key": canary}
        handler._api("POST", "/datapro/config")
        config_status, config_body = replies.pop()
        assert config_status == 200
        assert config_body["key_configured"] is True
        assert canary not in json.dumps(config_body)
        assert "secret://" not in json.dumps(config_body)
        assert "X-Agent-Plan-Key" not in json.dumps(config_body)

        with pytest.raises(
            gateway_mod.GatewayError,
            match="requires a real dataPro_search call",
        ):
            handler._body = lambda: {}
            handler._api("POST", f"/connectors/{datapro.CONNECTOR_ID}/probe")

        runner.store.set_connector_enabled(datapro.CONNECTOR_ID, False)
        with pytest.raises(gateway_mod.GatewayError, match="connector is disabled"):
            handler._body = lambda: {
                "tool": datapro.TOOL_NAME,
                "args": {"query": "must not run"},
            }
            handler._api("POST", f"/connectors/{datapro.CONNECTOR_ID}/call")
        assert [call for call in calls if call[0] == datapro.CONNECTOR_ID] == []
        runner.store.set_connector_enabled(datapro.CONNECTOR_ID, True)

        handler._body = lambda: {"query": "find professional evidence"}
        handler._api("POST", "/datapro/search")
        status, body = replies.pop()
        assert status == 200
        assert body["structuredContent"]["code"] == 0
        assert body["available"] is True
        assert body["message"] == datapro.AVAILABLE_MESSAGE
        assert body["index"]["complete"] is True
        assert body["index"]["source_leaf_count"] == body["index"]["indexed_leaf_count"]
        assert body["index"]["source_digest"] == body["index"]["indexed_digest"]
        assert canary not in json.dumps(body)
        assert "[REDACTED]" in json.dumps(body)
        assert body["artifact"]["id"]

        artifact = runner.store.get_artifact(body["artifact"]["id"])
        assert artifact is not None
        saved = Path(artifact["path"]).read_text(encoding="utf-8")
        assert canary not in saved
        assert "[REDACTED]" in saved
        assert "find professional evidence" in saved
        indexed = runner.store.search_datapro_index("evidence")
        assert indexed["total"] == 1
        assert indexed["items"][0]["artifact_id"] == body["artifact"]["id"]

        # The managed generic call is not an indexing bypass.  It uses the
        # same strict success gate and returns the same completeness receipt,
        # even though it does not create a Web Artifact.
        handler._body = lambda: {
            "tool": datapro.TOOL_NAME,
            "args": {"query": "generic professional evidence"},
        }
        handler._api("POST", f"/connectors/{datapro.CONNECTOR_ID}/call")
        generic_status, generic = replies.pop()
        assert generic_status == 200
        assert generic["raw"]["structuredContent"]["code"] == 0
        assert generic["index"]["complete"] is True
        assert (
            runner.store.search_datapro_index("generic professional evidence")["total"]
            >= 1
        )

        # Product success is index + saved Artifact as one visible outcome.
        # A failed upload must compensate the already-created index batch,
        # otherwise a 502 leaves a searchable ghost result behind.
        upload = runner.artifacts.upload

        def fail_upload(*args, **kwargs):
            raise RuntimeError("injected DataPro artifact upload failure")

        monkeypatch.setattr(runner.artifacts, "upload", fail_upload)
        handler._body = lambda: {"query": "artifact-failure-ghost-sentinel"}
        handler._api("POST", "/datapro/search")
        failed_status, failed_body = replies.pop()
        assert failed_status == 502
        assert failed_body.get("error")
        assert (
            runner.store.search_datapro_index("artifact-failure-ghost-sentinel")[
                "total"
            ]
            == 0
        )
        monkeypatch.setattr(runner.artifacts, "upload", upload)

        tool_calls = [call for call in calls if call[0] == datapro.CONNECTOR_ID]
        assert len(tool_calls) == 3, "each available result needs one real tool call"
        _, tool, args, outbound = tool_calls[0]
        assert tool == datapro.TOOL_NAME
        assert args == {"query": "find professional evidence"}
        assert outbound["X-Agent-Plan-Key"] == canary
        assert outbound["X-Hqd-Extra-Info"] == "openai4s"
        assert canary not in caplog.text

        # A credential too short for lossless exact-reflection redaction is
        # rejected before storage or any outbound MCP call.
        calls_before = len(calls)
        handler._body = lambda: {"agent_plan_key": "r"}
        with pytest.raises(gateway_mod.GatewayError, match="at least") as denied:
            handler._api("POST", "/datapro/config")
        assert denied.value.code == 400
        assert len(calls) == calls_before
    finally:
        runner.close()


def test_managed_product_config_routes_project_only_local_credential_state(tmp_path):
    """Non-stubbed response-capture sources for both shared-key products."""

    from openai4s import datapro
    from openai4s.config import Config, LLMConfig
    from openai4s.server import gateway as gateway_mod

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            return None

    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    runner.store.upsert_connector(
        connector_id=datapro.CONNECTOR_ID,
        name="Volcengine DataPro",
        command=datapro.managed_connector_command(),
        enabled=True,
    )
    try:
        handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
        handler = object.__new__(handler_cls)
        replies = []
        handler._query = lambda: {}
        handler._json = lambda obj, code=200: replies.append((code, obj))

        handler._api("GET", "/datapro/config")
        assert replies.pop() == (
            200,
            {
                "key_configured": False,
                "ark_key_reused": False,
                "connector_id": datapro.CONNECTOR_ID,
                "connector_enabled": True,
                "skill_name": datapro.SKILL_NAME,
                "skill_enabled": True,
            },
        )

        handler._api("GET", "/doubao-search/config")
        assert replies.pop() == (
            200,
            {
                "key_configured": False,
                "ark_key_reused": False,
                "provider": "doubao-search",
                "primary": True,
            },
        )

        handler._body = lambda: {"agent_plan_key": "local-config-test-key"}
        handler._api("POST", "/datapro/config")
        status, body = replies.pop()
        assert status == 200
        assert body == {
            "ok": True,
            "key_configured": True,
            "ark_key_reused": False,
            "connector_id": datapro.CONNECTOR_ID,
            "connector_enabled": True,
            "skill_name": datapro.SKILL_NAME,
            "skill_enabled": True,
        }

        # The second product observes the same brokered credential without a
        # network call, and its own save response remains metadata-only.
        handler._api("GET", "/doubao-search/config")
        assert replies.pop() == (
            200,
            {
                "key_configured": True,
                "ark_key_reused": False,
                "provider": "doubao-search",
                "primary": True,
            },
        )
        handler._body = lambda: {"agent_plan_key": "second-local-config-test-key"}
        handler._api("POST", "/doubao-search/config")
        assert replies.pop() == (
            200,
            {
                "ok": True,
                "key_configured": True,
                "ark_key_reused": False,
                "provider": "doubao-search",
                "primary": True,
            },
        )
    finally:
        runner.close()

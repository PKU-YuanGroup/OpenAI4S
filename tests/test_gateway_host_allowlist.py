"""The DNS-rebinding Host allowlist (GHSA-fm3g-2c7x-8qj8), over a real socket.

`tests/test_gateway.py::test_dns_rebinding_host_header_is_rejected` already
covers the guard, but it does so on a synthetic handler: `object.__new__` plus a
monkeypatched `_json`/`_api`. That proves `_route` *decided* 403 — it cannot
prove a 403 is what a client receives, because nothing in it ever writes a
status line. This project has been bitten by exactly that gap before (a
`GatewayError` asserted from a direct method call still reached HTTP as 200), and
the guard being audited here is the one control standing between a rebound
browser tab and `/api/v1/compute/jobs`. So this module binds the real
`make_handler` output into a real `ThreadingHTTPServer` and speaks raw HTTP/1.1
at it, asserting the status line on the wire.

It also pins three properties the synthetic test cannot express at all:

* the guard runs **before** the token gate — a forged Host with no credential
  must be refused as a Host, not as a 401, or the ordering claim in `_route` is
  decorative;
* it covers surfaces outside `/api/` — the SPA shell, `/static/`, and the
  token-exempt `/health` — because a rebound page is same-origin and can read
  every one of those response bodies;
* the `/api/v1/ws` upgrade is refused with a status line rather than switching
  protocols, which is the difference between "rejected" and "attacker now holds
  a socket that accepts commands".

Remove the `_host_header_allowed()` call from `_route` and the forged-Host cases
here answer 200 (and 101 for the WebSocket) instead of 403.
"""

from __future__ import annotations

import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth


class _Hub:
    """The narrow slice of the WS hub `make_handler` touches at construction."""

    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


def _free_port() -> int:
    # The allowlist compares against `cfg.port`, so the daemon has to actually
    # be listening on the port the config names — an ephemeral bind (port 0)
    # would make every legitimate request fail the port check and the test
    # would pass for the wrong reason.
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@pytest.fixture()
def daemon(tmp_path: Path):
    """A real gateway on loopback: real Handler, real socket, real status lines."""

    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
        host="127.0.0.1",
        port=port,
    )
    cfg.ensure_dirs()
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    token = local_auth.load_or_mint(cfg.data_dir)
    try:
        yield port, token
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        try:
            runner.close()
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass


def _speak(port: int, request: bytes) -> tuple[int, bytes]:
    """Send a hand-built request and return (status_code, raw_response).

    Hand-built rather than `http.client` because the attack is a *forged* Host
    and every stdlib client sets Host itself from the address it dialled; the
    absent-Host case cannot be expressed through one at all.
    """

    conn = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        conn.sendall(request)
        chunks = []
        # Read to EOF, not to the header terminator. Stopping at the first
        # blank line passed when this module ran alone and failed when it ran
        # after tests/test_gateway.py: the body is a separate TCP segment, and
        # whether it had arrived by the first recv depended on machine load.
        # Every request below either sends `Connection: close` or hits a
        # refusal path that sets `close_connection`, so EOF always comes.
        while True:
            block = conn.recv(65536)
            if not block:
                break
            chunks.append(block)
    finally:
        conn.close()
    raw = b"".join(chunks)
    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status_line.split(" ")
    assert len(parts) >= 2, f"no status line in {raw[:120]!r}"
    return int(parts[1]), raw


def _get(port: int, path: str, host: str | None, token: str | None = None) -> int:
    lines = [f"GET {path} HTTP/1.1"]
    if host is not None:
        lines.append(f"Host: {host}")
    if token is not None:
        lines.append(f"{local_auth.TOKEN_HEADER}: {token}")
    lines.append("Connection: close")
    return _speak(port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))[0]


def test_a_forged_host_never_reaches_the_command_sink(daemon):
    """The exact bypass: evil.test resolves to 127.0.0.1, so the browser sends
    Origin == Host == evil.test and the CSRF guard is satisfied, while the POST
    lands on the loopback daemon's unauthenticated job runner.

    The credential is presented on purpose. The browser in a rebinding attack
    *has* the user's cookie, so a 403 produced by the token gate would prove
    nothing about the Host check."""

    port, token = daemon
    body = b'{"command":"touch /tmp/openai4s-rebind-poc"}'
    request = (
        b"POST /api/v1/compute/jobs HTTP/1.1\r\n"
        + f"Host: evil.test:{port}\r\n".encode()
        + f"Origin: http://evil.test:{port}\r\n".encode()
        + f"{local_auth.TOKEN_HEADER}: {token}\r\n".encode()
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"Connection: close\r\n\r\n"
        + body
    )
    status, raw = _speak(port, request)
    assert status == 403, raw[:200]
    # Refused as a Host, not as anything downstream: if this ever becomes the
    # cross-origin message, the Origin guard is doing the work and the rebind
    # case (Origin == Host) is silently unprotected again.
    assert b"host not allowed" in raw


def test_the_host_check_runs_before_the_token_gate(daemon):
    """Ordering, stated as an observable. `_route` claims the allowlist is
    checked before Origin and before the credential; a forged Host with no
    credential at all must therefore answer 403 "host not allowed" rather than
    401. If the token gate ran first this would be a 401 and the allowlist would
    only ever be consulted for already-authenticated callers."""

    port, _token = daemon
    status, raw = _speak(
        port,
        b"GET /api/v1/frames HTTP/1.1\r\n"
        + f"Host: evil.test:{port}\r\n".encode()
        + b"Connection: close\r\n\r\n",
    )
    assert status == 403, raw[:200]
    assert b"host not allowed" in raw


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/frames",  # API read: a rebound page is same-origin, it can read this
        "/",  # SPA shell
        "/static/app.js",  # the frontend itself
        "/health",  # token-exempt, so the allowlist is its only gate
    ],
)
def test_a_forged_host_is_refused_on_every_surface(daemon, path):
    """The allowlist is not an `/api/` guard. Origin-less GETs skip the CSRF
    check entirely, and a rebound origin can read any body it fetches — so a
    guard that only covered mutations would leave the whole read surface open."""

    port, token = daemon
    assert _get(port, path, f"evil.test:{port}", token) == 403


def test_a_forged_host_cannot_upgrade_the_websocket(daemon):
    """The WS upgrade is a GET, is exempt from CORS entirely, and the socket it
    yields accepts state-changing commands. It must be refused with a status
    line, not switched to protocols."""

    port, token = daemon
    status, raw = _speak(
        port,
        b"GET /api/v1/ws HTTP/1.1\r\n"
        + f"Host: evil.test:{port}\r\n".encode()
        + f"Origin: http://evil.test:{port}\r\n".encode()
        + b"Upgrade: websocket\r\n"
        + b"Connection: Upgrade\r\n"
        + b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        + b"Sec-WebSocket-Version: 13\r\n"
        + f"{local_auth.TOKEN_HEADER}: {token}\r\n".encode()
        + b"\r\n",
    )
    assert status == 403, raw[:200]
    assert b"host not allowed" in raw


@pytest.mark.parametrize("hostname", ["127.0.0.1", "localhost", "[::1]", "LocalHost"])
def test_the_real_loopback_hosts_still_work(daemon, hostname):
    """A guard that rejects everything is not a fix. Every Host a browser
    actually sends to this daemon — including the IPv6 literal form and mixed
    case — must still be served."""

    port, token = daemon
    assert _get(port, "/api/v1/frames", f"{hostname}:{port}", token) == 200


def test_an_absent_host_still_works(daemon):
    """curl/CLI clients: the rebinding vector is a browser, and a browser always
    sends a Host, so an absent one is a non-browser local client. HTTP/1.0
    because HTTP/1.1 makes Host mandatory — this is the only way to express it."""

    port, token = daemon
    status, raw = _speak(
        port,
        b"GET /api/v1/frames HTTP/1.0\r\n"
        + f"{local_auth.TOKEN_HEADER}: {token}\r\n".encode()
        + b"Connection: close\r\n\r\n",
    )
    assert status == 200, raw[:200]


def test_the_right_hostname_on_the_wrong_port_is_refused(daemon):
    """`evil.test` is not the only forgeable value. A Host naming a loopback
    address but a port this daemon does not serve did not come from a browser
    that reached this daemon honestly."""

    port, token = daemon
    wrong = port + 1 if port + 1 < 65536 else port - 1
    assert _get(port, "/api/v1/frames", f"127.0.0.1:{wrong}", token) == 403

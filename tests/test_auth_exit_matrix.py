"""The credential gate's exit matrix, over a real socket.

P0-1's exit criteria name an unauthenticated REST/WS matrix and a cookie that
survives a restart. The implementation is present and correct; three legs of the
proof were not, and the docs recorded the row as closed anyway:

1. **No test ever drove an unauthenticated WebSocket upgrade.** Every
   `/api/v1/ws` test in the tree presents a credential, and every one of them
   runs on a synthetic handler with `_handle_ws` replaced by a list append --
   so none of them could observe the difference between "refused" and "the
   client now holds a socket that accepts `cancel_execution` and streams
   approval prompts". That is the one outcome the gate exists to prevent.
2. **The unauthenticated REST "matrix" was one route.** `/api/v1/frames`, plus
   the two exempt paths. A route added outside the gate would have been caught
   by nothing.
3. **"Cookie across restart" asserted token-file stability, not a cookie.**
   `make_handler` twice on one data dir, comparing `read_token` -- which is a
   test of `local_auth`, not of whether a cookie a browser is still holding is
   accepted by the handler that replaced the one that issued it.

Everything here speaks raw HTTP/1.1 at a real `ThreadingHTTPServer`, for the
reason `tests/test_gateway_host_allowlist.py` states: a decision asserted from a
direct `_route` call has never written a status line, and this repository has
already shipped a `GatewayError` that reached HTTP as 200.
"""

from __future__ import annotations

import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import contract
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from tests._ports import bound_gateway_server


class _Hub:
    """Enough of the WS hub that a *successful* upgrade runs its real path.

    A stub missing `add` lets the 101 reach the wire and then raises inside
    `_handle_ws`, which is the shape of a control that passes for the wrong
    reason: it would keep passing if the upgrade were failing one line later.
    """

    def __init__(self) -> None:
        self.connections: list = []

    def add(self, conn):
        self.connections.append(conn)

    def remove(self, conn):
        if conn in self.connections:
            self.connections.remove(conn)

    def subscribe(self, conn, root_frame_id):
        return None

    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


def _free_port() -> int:
    # The Host allowlist compares against `cfg.port`, so the daemon has to be
    # listening on the port its config names; an ephemeral bind would make every
    # request fail the Host check and the whole module would pass for the wrong
    # reason.
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _config(data_dir: Path, port: int) -> Config:
    cfg = Config(
        data_dir=data_dir,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
        host="127.0.0.1",
        port=port,
    )
    cfg.ensure_dirs()
    return cfg


class _Daemon:
    """One running gateway, and the ability to replace it in place.

    Replacing rather than tearing down is the point of the restart case: the
    cookie under test was issued by the handler that is being thrown away.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._httpd: ThreadingHTTPServer | None
        self._httpd, self.port = bound_gateway_server()
        self.cfg = _config(data_dir, self.port)
        self._runners: list = []
        self._thread: threading.Thread | None = None
        self.start()

    def start(self) -> None:
        if self._httpd is None:
            # A restart must come back on the SAME port -- the cookie under
            # test was issued against it -- so this rebind cannot go through
            # port 0. The close()->bind() gap here is ours alone and
            # milliseconds wide, unlike the fixture-wide probe-then-rebind
            # window bound_gateway_server closes for the first bind.
            self._httpd = ThreadingHTTPServer(
                ("127.0.0.1", self.port), BaseHTTPRequestHandler
            )
            self._httpd.daemon_threads = True
        runner = gateway_mod.SessionRunner(self.cfg, _Hub())
        self._runners.append(runner)
        handler_cls = gateway_mod.make_handler(self.cfg, _Hub(), runner)
        self._httpd.RequestHandlerClass = handler_cls
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def restart(self) -> None:
        self.stop_server()
        self.start()

    def stop_server(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def close(self) -> None:
        self.stop_server()
        for runner in self._runners:
            try:
                runner.close()
            except Exception:  # noqa: BLE001 - teardown must not mask a failure
                pass

    @property
    def token(self) -> str:
        return local_auth.load_or_mint(self.data_dir)


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _Daemon(tmp_path)
    try:
        yield node
    finally:
        node.close()


def _speak(port: int, request: bytes, *, read_all: bool = True) -> tuple[int, bytes]:
    """Send a hand-built request; return (status_code, raw bytes).

    Hand-built because a WebSocket upgrade cannot be expressed through
    `http.client` -- and because the *absence* of a header is part of what is
    being asserted, which no stdlib client will let you say.
    """
    conn = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        conn.sendall(request)
        chunks: list[bytes] = []
        while True:
            block = conn.recv(65536)
            if not block:
                break
            chunks.append(block)
            if not read_all and b"\r\n\r\n" in b"".join(chunks):
                # A successful upgrade never closes: reading to EOF on a live
                # WebSocket would block until the socket timeout, and the status
                # line is the whole assertion.
                break
    except socket.timeout:  # noqa: UP041 - the alias is the stdlib name here
        pass
    finally:
        conn.close()
    raw = b"".join(chunks)
    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status_line.split(" ")
    assert len(parts) >= 2, f"no status line in {raw[:160]!r}"
    return int(parts[1]), raw


def _ws_upgrade(port: int, *, token: str | None, cookie: str | None = None) -> tuple:
    lines = [
        "GET /api/v1/ws HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
    ]
    if token is not None:
        lines.append(f"{local_auth.TOKEN_HEADER}: {token}")
    if cookie is not None:
        lines.append(f"Cookie: {cookie}")
    return _speak(
        port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"), read_all=False
    )


def _get(port: int, path: str, *, token: str | None = None, cookie: str | None = None):
    lines = [f"GET {path} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    if token is not None:
        lines.append(f"{local_auth.TOKEN_HEADER}: {token}")
    if cookie is not None:
        lines.append(f"Cookie: {cookie}")
    lines.append("Connection: close")
    return _speak(port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))


# -- the WebSocket leg --------------------------------------------------------


def test_an_unauthenticated_websocket_upgrade_never_switches_protocols(daemon):
    """The outcome the gate exists to prevent, asserted as the wire event.

    `/api/v1/ws` is a GET, is exempt from CORS entirely, and the socket it
    yields accepts state-changing commands and streams a session's output plus
    its pending approval prompts. "Refused" and "101 Switching Protocols" are
    the two possible answers, and no test in this tree had ever asked for the
    second one without first presenting a credential.
    """
    status, raw = _ws_upgrade(daemon.port, token=None)

    assert status == 401, raw[:200]
    assert status != 101
    assert b"Sec-WebSocket-Accept" not in raw


def test_a_wrong_credential_does_not_open_the_socket_either(daemon):
    """A token-shaped string that is not the token. Distinguishes the gate from
    a `if header is present` check, which a caller can satisfy for free."""
    status, raw = _ws_upgrade(daemon.port, token="0" * len(daemon.token))

    assert status == 401, raw[:200]
    assert b"Sec-WebSocket-Accept" not in raw


def test_the_right_credential_does_open_the_socket(daemon):
    """The control, without which the two refusals above are satisfied by a
    daemon that cannot upgrade a WebSocket at all -- and the falsification
    (removing the gate) would then prove nothing."""
    status, raw = _ws_upgrade(daemon.port, token=daemon.token)

    assert status == 101, raw[:200]
    assert b"Sec-WebSocket-Accept" in raw


# -- the REST matrix ----------------------------------------------------------


def _concrete(route: str) -> str:
    """One walkable path per routable pattern.

    The gate is keyed on the *path*, before any routing happens, so the
    placeholder never has to name a real object -- a 401 is the assertion and a
    404 would mean the request got past the gate.
    """
    path = re.sub(r"\(\[\^/\]\+\)", "matrix-probe", route)
    path = re.sub(r"\(\.\+\)", "matrix-probe", path)
    path = re.sub(r"\([^)]*\)", "matrix-probe", path)
    return contract.API_ROOT + path


def test_every_routable_path_refuses_an_anonymous_caller(daemon):
    """The matrix, swept from the same route list the coverage gate uses.

    One route was being checked. `contract.http_routes()` is what
    `capture_response_contract.py` reconciles against, so a route added to the
    gateway and not to the exempt set arrives here on its own -- which is the
    only version of this test that keeps working after the day it is written.
    """
    routes = contract.http_routes()
    assert len(routes) > 100, f"the route list collapsed to {len(routes)}"

    exempt = {p for p in local_auth_exempt_paths()}
    refused: list[str] = []
    allowed: list[tuple[str, int]] = []
    for route in sorted(routes):
        path = _concrete(route)
        if path in exempt:
            continue
        status, _raw = _get(daemon.port, path)
        (refused if status == 401 else allowed).append(
            path if status == 401 else (path, status)
        )

    assert not allowed, f"reachable without a credential: {allowed[:10]}"
    assert len(refused) >= len(routes) - len(exempt)


def local_auth_exempt_paths() -> set[str]:
    """Read from the gate, not restated.

    A copy of this set in the test would keep passing while somebody widened
    the real one -- which is the failure mode the sweep is here to close.
    """
    return set(gateway_mod._UNAUTHENTICATED_PATHS)


def test_the_exempt_paths_answer_without_a_credential(daemon):
    """The other half: a gate that refuses everything is not a gate, it is an
    outage. `/health` is a liveness probe and `/auth/status` is how a client
    learns it needs a token at all -- a 401 there is unrecoverable by
    construction."""
    for path in sorted(local_auth_exempt_paths()):
        status, raw = _get(daemon.port, path)
        assert status == 200, (path, raw[:160])
        assert daemon.token.encode() not in raw, f"{path} echoed the token"


def test_the_exempt_set_stays_small_and_named(daemon):
    """Two paths, both readable without leaking anything. This is not style: the
    sweep above skips whatever is in this set, so widening it silently widens
    what the matrix stops checking."""
    assert local_auth_exempt_paths() == {"/health", contract.API_ROOT + "/auth/status"}


# -- the cookie, across a restart ---------------------------------------------


def _issue_cookie(daemon: _Daemon) -> str:
    """The real bootstrap: `GET /?token=…` answers 303 and sets `os_token`."""
    status, raw = _speak(
        daemon.port,
        (
            f"GET /?token={daemon.token} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{daemon.port}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii"),
    )
    assert status == 303, raw[:200]
    match = re.search(rb"Set-Cookie:\s*(os_token=[^;\r\n]+)", raw)
    assert match, raw[:300]
    return match.group(1).decode("ascii")


def test_a_cookie_issued_before_a_restart_is_accepted_after_it(daemon):
    """What the criterion actually asks, driven end to end.

    The previous evidence called `make_handler` twice and compared
    `local_auth.read_token` -- a test of the token file, which cannot fail for
    any reason a *browser* would notice. The browser's side of this is a cookie
    it is still holding, presented to a handler that did not issue it. A token
    minted per boot passes the old assertion for the first handler and logs
    every open tab out.
    """
    cookie = _issue_cookie(daemon)
    assert _get(daemon.port, "/api/v1/frames", cookie=cookie)[0] == 200

    daemon.restart()

    status, raw = _get(daemon.port, "/api/v1/frames", cookie=cookie)
    assert status == 200, raw[:200]


def test_a_cookie_issued_before_a_restart_still_opens_the_websocket(daemon):
    """The live channel, which is what an open tab actually reconnects on. A
    session whose REST calls survive a restart and whose socket does not is a UI
    that looks connected and receives nothing."""
    cookie = _issue_cookie(daemon)
    daemon.restart()

    status, raw = _ws_upgrade(daemon.port, token=None, cookie=cookie)

    assert status == 101, raw[:200]


def test_a_cookie_from_a_different_data_dir_is_not_accepted(tmp_path):
    """The negative arm. Without it, "the cookie still works" is satisfied by a
    handler that stopped checking."""
    theirs = _Daemon(tmp_path / "theirs")
    mine = _Daemon(tmp_path / "mine")
    try:
        foreign = _issue_cookie(theirs)
        assert _get(mine.port, "/api/v1/frames", cookie=foreign)[0] == 401
    finally:
        theirs.close()
        mine.close()


# -- the retired loopback opt-out -----------------------------------------------


def _body(raw: bytes) -> dict:
    import json

    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


@pytest.mark.parametrize("value", ["0", "false", "no"])
def test_require_token_zero_is_ignored_on_loopback(
    tmp_path, monkeypatch, capsys, value
):
    """`OPENAI4S_REQUIRE_TOKEN=0` was granted exactly one minor release (D1).

    It turned off the only credential check in front of `kernel/execute`,
    `compute/jobs` and `host.bash` on a loopback bind, and v0.2.0 was the
    release it was granted for. From 0.3.0 the variable is ignored: a daemon
    started with it still mints the persistent token, still refuses an
    anonymous caller, and `/auth/status` still reports the gate it enforces.

    Driven over a real socket rather than by reading the token file, because a
    token file that exists beside a handler that does not check it is the
    shape the opt-out had -- only the wire can tell the two apart.
    """
    monkeypatch.setenv("OPENAI4S_REQUIRE_TOKEN", value)
    node = _Daemon(tmp_path / "loopback")
    try:
        assert node.cfg.host == "127.0.0.1"
        assert local_auth.read_token(
            node.data_dir
        ), f"OPENAI4S_REQUIRE_TOKEN={value} on loopback minted no token"

        status, raw = _get(node.port, contract.API_ROOT + "/auth/status")
        assert status == 200, raw[:200]
        body = _body(raw)
        assert body["auth_mode"] == "token", body
        assert body["authenticated"] is False, body
        assert body["token_header"] == local_auth.TOKEN_HEADER, body

        assert _get(node.port, contract.API_ROOT + "/frames")[0] == 401
        ws_status, ws_raw = _ws_upgrade(node.port, token=None)
        assert ws_status == 401, ws_raw[:200]

        err = capsys.readouterr().err
        assert "access token required" in err
        assert "OPENAI4S_REQUIRE_TOKEN=0" not in err, err
    finally:
        node.close()


def test_require_token_zero_is_ignored_off_loopback(tmp_path, monkeypatch):
    """A non-loopback bind is reachable by anything that can route to it. It
    never honoured the opt-out, and it must not start to now that the variable
    means nothing anywhere."""
    monkeypatch.setenv("OPENAI4S_REQUIRE_TOKEN", "0")
    cfg = _config(tmp_path / "public", _free_port())
    cfg.host = "0.0.0.0"
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    try:
        gateway_mod.make_handler(cfg, _Hub(), runner)
        assert local_auth.read_token(
            cfg.data_dir
        ), "a non-loopback bind honoured OPENAI4S_REQUIRE_TOKEN=0 and minted no token"
    finally:
        runner.close()


def test_no_browser_gate_still_configures_the_retired_token_switch():
    """A gate that pins `OPENAI4S_REQUIRE_TOKEN` in its daemon's environment
    reads as though the credential gate were still a setting. It is not, so a
    pinned value documents a contract 0.3.0 removed. Comments may still name
    the variable to explain its removal; code may not set it."""
    offenders = []
    for gate in sorted(Path(__file__).resolve().parent.glob("*.mjs")):
        for number, line in enumerate(gate.read_text("utf-8").splitlines(), 1):
            code = line.strip()
            if code.startswith(("//", "*", "/*")):
                continue
            if "OPENAI4S_REQUIRE_TOKEN" in code:
                offenders.append(f"{gate.name}:{number}: {code}")
    assert offenders == []


def test_no_python_test_arms_the_retired_token_switch():
    """The Python half of the same dead contract. A test that sets
    `OPENAI4S_REQUIRE_TOKEN` to an arming value reads as though its gate were
    off without it; the gate is on regardless. The tests that set the retired
    opt-out values to prove they are ignored are the point, and stay."""
    import re

    arming = re.compile(
        r"""(?:setenv\(|environ\[)\s*["']OPENAI4S_REQUIRE_TOKEN["']\s*[\],]\s*=?\s*"""
        r"""["'](?:1|true|yes|on)["']""",
        re.IGNORECASE,
    )
    offenders = []
    for test_file in sorted(Path(__file__).resolve().parent.glob("*.py")):
        text = test_file.read_text("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if arming.search(line):
                offenders.append(f"{test_file.name}:{number}: {line.strip()}")
    assert offenders == []

"""Team-mode login routes and the request guard, over a real socket (M1-4/5).

Raw HTTP/1.1 at a real ThreadingHTTPServer, for the reason
test_auth_exit_matrix states: a decision asserted from a direct `_route` call
has never written a status line. All passwords are fake test values; PBKDF2
iterations are shrunk so the login loop stays fast.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.storage import team as team_mod
from openai4s.store import get_store


class _Hub:
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
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class _TeamDaemon:
    """One running gateway with OPENAI4S_TEAM_MODE=1 and a seeded user.

    A REAL WSHub, deliberately: the WS isolation tests subscribe and receive
    broadcasts, and a stub hub would prove nothing about the replay buffer or
    the per-connection filtering.
    """

    def __init__(self, data_dir: Path, *, team_mode: bool = True) -> None:
        self.data_dir = data_dir
        self.port = _free_port()
        self.cfg = Config(
            data_dir=data_dir,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=3,
            host="127.0.0.1",
            port=self.port,
        )
        if team_mode:
            self.cfg.team_mode = True
        self.cfg.ensure_dirs()
        self.store = get_store(self.cfg.db_path)
        self.hub = gateway_mod.WSHub()
        self.runner = gateway_mod.SessionRunner(self.cfg, self.hub)
        handler_cls = gateway_mod.make_handler(self.cfg, self.hub, self.runner)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler_cls)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def seed_user(self, username: str, password: str, role: str = "member") -> dict:
        return self.store.team.create_user(
            username=username, password=password, role=role
        )

    @property
    def token(self) -> str:
        return local_auth.load_or_mint(self.data_dir)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        try:
            self.runner.close()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture(autouse=True)
def _fast_pbkdf2(monkeypatch):
    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 1200)


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _TeamDaemon(tmp_path)
    try:
        yield node
    finally:
        node.close()


def _speak(port: int, request: bytes, *, read_all: bool = True) -> tuple[int, bytes]:
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
                break
    except socket.timeout:  # noqa: UP041
        pass
    finally:
        conn.close()
    raw = b"".join(chunks)
    status_line = raw.split(b"\r\n", 1)[0].decode("latin-1")
    parts = status_line.split(" ")
    assert len(parts) >= 2, f"no status line in {raw[:160]!r}"
    return int(parts[1]), raw


def _get(
    port: int,
    path: str,
    *,
    token: str | None = None,
    cookie: str | None = None,
    accept: str | None = None,
):
    lines = [f"GET {path} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    if token is not None:
        lines.append(f"{local_auth.TOKEN_HEADER}: {token}")
    if cookie is not None:
        lines.append(f"Cookie: {cookie}")
    if accept is not None:
        lines.append(f"Accept: {accept}")
    lines.append("Connection: close")
    return _speak(port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))


def _post(port: int, path: str, body: dict, *, cookie: str | None = None):
    payload = json.dumps(body).encode("utf-8")
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
    ]
    if cookie is not None:
        lines.append(f"Cookie: {cookie}")
    lines.append("Connection: close")
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    return _speak(port, head + payload)


def _set_cookie(raw: bytes) -> str:
    """The os_user cookie pair from a Set-Cookie header."""
    for line in raw.split(b"\r\n"):
        if line.lower().startswith(b"set-cookie:") and b"os_user=" in line:
            value = line.split(b":", 1)[1].strip().decode("latin-1")
            return value.split(";", 1)[0]
    raise AssertionError(f"no os_user Set-Cookie in {raw[:400]!r}")


def _body_json(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def _login(daemon, username: str, password: str) -> str:
    status, raw = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": username, "password": password},
    )
    assert status == 200, raw[:300]
    return _set_cookie(raw)


def _ws_upgrade(port: int, *, cookie: str | None = None) -> tuple:
    lines = [
        "GET /api/v1/ws HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version: 13",
    ]
    if cookie is not None:
        lines.append(f"Cookie: {cookie}")
    return _speak(
        port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"), read_all=False
    )


# -- the guard ---------------------------------------------------------------


def test_unauthenticated_api_answers_401(daemon):
    status, raw = _get(daemon.port, "/api/v1/frames")
    assert status == 401, raw[:300]
    assert _body_json(raw).get("code") == "login_required"


def test_unauthenticated_browser_get_redirects_to_login(daemon):
    status, raw = _get(daemon.port, "/", accept="text/html")
    assert status == 303, raw[:300]
    assert b"location: /login" in raw.lower()


def test_login_page_and_assets_are_reachable_anonymously(daemon):
    assert _get(daemon.port, "/login")[0] == 200
    assert _get(daemon.port, "/static/login.js")[0] == 200
    assert _get(daemon.port, "/health")[0] == 200


def test_access_token_no_longer_buys_a_browser_cookie(daemon):
    """In team mode the `?token=` bootstrap is gone: a shared machine token
    must not become a logged-in browser."""
    status, raw = _get(daemon.port, f"/?token={daemon.token}", accept="text/html")
    assert status == 303, raw[:300]
    assert b"location: /login" in raw.lower()
    assert b"os_token=" not in raw


# -- login / logout / me ------------------------------------------------------


def test_login_round_trip_and_me(daemon):
    daemon.seed_user("alice", "fake-pw-alice", role="admin")
    cookie = _login(daemon, "alice", "fake-pw-alice")

    status, raw = _get(daemon.port, "/api/v1/frames", cookie=cookie)
    assert status == 200, raw[:300]

    status, raw = _get(daemon.port, "/api/v1/auth/me", cookie=cookie)
    assert status == 200
    me = _body_json(raw)
    assert me["team_mode"] is True
    assert me["user"]["username"] == "alice"
    assert me["user"]["role"] == "admin"


def test_login_sets_httponly_lax_cookie(daemon):
    daemon.seed_user("alice", "fake-pw-alice")
    status, raw = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "alice", "password": "fake-pw-alice"},
    )
    assert status == 200
    header = next(
        line
        for line in raw.split(b"\r\n")
        if line.lower().startswith(b"set-cookie:") and b"os_user=" in line
    ).lower()
    assert b"httponly" in header
    assert b"samesite=lax" in header
    assert b"max-age=" in header


def test_wrong_password_is_401_and_audited(daemon):
    daemon.seed_user("alice", "fake-pw-alice")
    status, raw = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "alice", "password": "wrong"},
    )
    assert status == 401
    actions = [r["action"] for r in daemon.store.team.list_audit()]
    assert "login_failed" in actions


def test_logout_revokes_the_cookie(daemon):
    daemon.seed_user("alice", "fake-pw-alice")
    cookie = _login(daemon, "alice", "fake-pw-alice")
    status, raw = _post(daemon.port, "/api/v1/auth/logout", {}, cookie=cookie)
    assert status == 200
    # the clearing Set-Cookie has Max-Age=0
    assert b"max-age=0" in raw.lower()
    status, _ = _get(daemon.port, "/api/v1/frames", cookie=cookie)
    assert status == 401


def test_disabled_user_cookie_stops_working(daemon):
    user = daemon.seed_user("alice", "fake-pw-alice")
    cookie = _login(daemon, "alice", "fake-pw-alice")
    daemon.store.team.set_disabled(user["id"], True)
    status, _ = _get(daemon.port, "/api/v1/frames", cookie=cookie)
    assert status == 401


def test_login_rate_limit_answers_429(daemon):
    daemon.seed_user("mallory", "fake-pw-real")
    for _ in range(5):
        status, _ = _post(
            daemon.port,
            "/api/v1/auth/login",
            {"username": "mallory", "password": "guess"},
        )
        assert status == 401
    status, raw = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "mallory", "password": "guess"},
    )
    assert status == 429, raw[:300]
    # the right password is also refused while the bucket is empty
    status, _ = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "mallory", "password": "fake-pw-real"},
    )
    assert status == 429
    actions = [r["action"] for r in daemon.store.team.list_audit()]
    assert "login_rate_limited" in actions


# -- the CLI service path -----------------------------------------------------


def test_loopback_access_token_header_still_works(daemon):
    status, raw = _get(daemon.port, "/api/v1/frames", token=daemon.token)
    assert status == 200, raw[:300]


def test_me_reports_the_service_identity(daemon):
    status, raw = _get(daemon.port, "/api/v1/auth/me", token=daemon.token)
    assert status == 200
    me = _body_json(raw)
    assert me["user"]["kind"] == "service"


# -- websocket ---------------------------------------------------------------


def test_ws_upgrade_requires_a_login(daemon):
    status, raw = _ws_upgrade(daemon.port)
    assert status == 401, raw[:200]
    assert b"Sec-WebSocket-Accept" not in raw


def test_ws_upgrade_succeeds_with_a_login_cookie(daemon):
    daemon.seed_user("alice", "fake-pw-alice")
    cookie = _login(daemon, "alice", "fake-pw-alice")
    status, raw = _ws_upgrade(daemon.port, cookie=cookie)
    assert status == 101, raw[:200]


# -- team mode off (INV-1) ----------------------------------------------------


def test_team_mode_off_keeps_the_disabled_shapes(tmp_path):
    node = _TeamDaemon(tmp_path, team_mode=False)
    try:
        status, raw = _get(node.port, "/api/v1/auth/me", token=node.token)
        assert status == 200
        assert _body_json(raw) == {"team_mode": False, "user": None}
        status, raw = _post(node.port, "/api/v1/auth/login", {})
        # unauthenticated POST hits the legacy token gate first (401); with
        # the token it reaches the route's stable disabled shape (403)
        assert status == 401
        status, raw = _speak(
            node.port,
            (
                f"POST /api/v1/auth/login HTTP/1.1\r\nHost: 127.0.0.1:{node.port}\r\n"
                f"{local_auth.TOKEN_HEADER}: {node.token}\r\n"
                "Content-Type: application/json\r\nContent-Length: 2\r\n"
                "Connection: close\r\n\r\n{}"
            ).encode("ascii"),
        )
        assert status == 403
        assert _body_json(raw).get("code") == "team_off"
    finally:
        node.close()

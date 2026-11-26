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
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.storage import team as team_mod
from openai4s.store import get_store
from tests._ports import bound_gateway_server


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
    # Not used for the daemon's own bind any more (that goes through
    # tests._ports.bound_gateway_server, which keeps the socket it probes).
    # Still imported by test_compute_session_routes and
    # test_cluster_session_production_wiring for OPENAI4S_WORKER_LISTEN,
    # where the number goes into config and is never rebound.
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

    def __init__(
        self,
        data_dir: Path,
        *,
        team_mode: bool = True,
        data_roots: list[Path] | None = None,
        roadmap_features: RoadmapFeatureFlags | None = None,
        trusted_proxy_origins: tuple[str, ...] = (),
    ) -> None:
        self.data_dir = data_dir
        self._httpd, self.port = bound_gateway_server()
        self.cfg = Config(
            data_dir=data_dir,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=3,
            host="127.0.0.1",
            port=self.port,
            roadmap_features=roadmap_features or RoadmapFeatureFlags(),
            trusted_proxy_origins=trusted_proxy_origins,
        )
        if team_mode:
            self.cfg.team_mode = True
        if data_roots is not None:
            self.cfg.data_roots = list(data_roots)
        self.cfg.ensure_dirs()
        self.store = get_store(self.cfg.db_path)
        self.hub = gateway_mod.WSHub()
        self.runner = gateway_mod.SessionRunner(self.cfg, self.hub)
        handler_cls = gateway_mod.make_handler(self.cfg, self.hub, self.runner)
        self._httpd.RequestHandlerClass = handler_cls
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


def _post(
    port: int,
    path: str,
    body: dict,
    *,
    cookie: str | None = None,
    origin: str | None = None,
):
    payload = json.dumps(body).encode("utf-8")
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
    ]
    if cookie is not None:
        lines.append(f"Cookie: {cookie}")
    if origin is not None:
        lines.append(f"Origin: {origin}")
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


def _ws_upgrade(
    port: int, *, cookie: str | None = None, origin: str | None = None
) -> tuple:
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
    if origin is not None:
        lines.append(f"Origin: {origin}")
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


def test_the_login_redirect_carries_the_deep_link_back(daemon):
    """TEAM-9: a colleague's session link opened signed-out landed on the
    home page after the login, because the 303 dropped where it was going."""
    status, raw = _get(daemon.port, "/projects/p1/frames/f-abc?x=1", accept="text/html")
    assert status == 303, raw[:300]
    assert (
        b"location: /login?next=%2fprojects%2fp1%2fframes%2ff-abc%3fx%3d1"
        in raw.lower()
    )
    # A target that is not a page of this origin is not carried.
    status, raw = _get(daemon.port, "/login/x", accept="text/html")
    assert status == 303 and b"location: /login\r\n" in raw.lower()
    # an API caller still gets a 401, not a redirect
    assert _get(daemon.port, "/projects/p1")[0] == 401


@pytest.mark.parametrize(
    "target",
    [
        "/projects/p1/frames/f-abc?x=1",
        "/replay#f-1",
        "/projects/p1/frames/%E4%B8%AD",
    ],
)
def test_safe_next_path_keeps_same_origin_pages(target):
    from openai4s.server.team_auth import safe_next_path

    assert safe_next_path(target) == target


@pytest.mark.parametrize(
    "target",
    [
        None,
        "",
        "/",
        "/?token=abc",
        "//evil.example/x",
        "/\\evil.example",
        "/x\\y",
        "/\t/evil.example",
        "/x\r\nSet-Cookie: a=b",
        "/x y",
        "/projects/中",
        "https://evil.example/",
        "javascript:alert(1)",
        "evil.example",
        "/login",
        "/login?next=/x",
        "/api/v1/frames",
        "/" + "a" * 2048,
    ],
)
def test_safe_next_path_refuses_anything_that_could_leave_the_origin(target):
    from openai4s.server.team_auth import login_location, safe_next_path

    assert safe_next_path(target) is None
    assert login_location(target) == "/login"


def test_login_page_and_assets_are_reachable_anonymously(daemon):
    assert _get(daemon.port, "/login")[0] == 200
    assert _get(daemon.port, "/static/login.js")[0] == 200
    assert _get(daemon.port, "/health")[0] == 200
    # TEAM-1: the page wears the workbench's theme, fonts and language.
    assert _get(daemon.port, "/static/style.css")[0] == 200
    assert _get(daemon.port, "/static/theme-bootstrap.js")[0] == 200


_WEBUI = Path(__file__).resolve().parents[1] / "openai4s" / "server" / "webui"


def _login_copy() -> dict:
    src = (_WEBUI / "login.js").read_text(encoding="utf-8")
    start = src.index("var COPY = ") + len("var COPY = ")
    return json.loads(src[start : src.index("};", start) + 1])


def test_the_login_page_speaks_both_interface_languages():
    """TEAM-1: the page was a bare English form with a hard-coded
    `lang="en"`, a blue button of its own, and the server's raw sentence as
    its error. It now loads the workbench's theme bootstrap and stylesheet,
    and every string it shows comes from a zh + en table."""
    import re

    html = (_WEBUI / "login.html").read_text(encoding="utf-8")
    assert '<script src="/static/theme-bootstrap.js"></script>' in html
    assert '<link rel="stylesheet" href="/static/style.css">' in html
    copy = _login_copy()
    assert set(copy) == {"en", "zh"}
    assert set(copy["en"]) == set(copy["zh"])
    keys = set(re.findall(r'data-i18n="([^"]+)"', html))
    assert keys and keys <= set(copy["en"])
    # every refusal the login route can give has its own sentence
    js = (_WEBUI / "login.js").read_text(encoding="utf-8")
    for code in ("account_disabled", "rate_limited"):
        assert f'"{code}"' in js
    for key in ("err.invalid", "err.disabled", "err.rateLimited", "err.network"):
        assert copy["zh"][key] and copy["zh"][key] != copy["en"][key]
    assert "body.error" not in js  # never the server's English sentence


def test_the_login_page_says_who_to_ask_and_can_show_the_password():
    """TEAM-2: no self-service sign-up or reset exists, and the page said
    nothing about it; an initial password is a long generated string typed
    into a field that could not be revealed."""
    html = (_WEBUI / "login.html").read_text(encoding="utf-8")
    js = (_WEBUI / "login.js").read_text(encoding="utf-8")
    copy = _login_copy()
    assert 'data-i18n="help"' in html
    assert "administrator" in copy["en"]["help"] and "管理员" in copy["zh"]["help"]
    assert 'id="login-pw-toggle"' in html and 'aria-controls="p"' in html
    assert 'pw.type = pw.type === "password" ? "text" : "password"' in js
    for key in ("show", "hide", "showLabel", "hideLabel"):
        assert copy["zh"][key] and copy["en"][key]


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
    # Direct-loopback HTTP remains supported when no TLS proxy origin is
    # configured; a Secure cookie would not round-trip on that deployment.
    assert b"secure" not in header


def test_exact_trusted_proxy_origin_allows_login_and_websocket(tmp_path):
    node = _TeamDaemon(
        tmp_path,
        trusted_proxy_origins=("https://lab.example",),
    )
    try:
        node.seed_user("alice", "fake-pw-alice")
        status, raw = _post(
            node.port,
            "/api/v1/auth/login",
            {"username": "alice", "password": "fake-pw-alice"},
            origin="https://lab.example",
        )
        assert status == 200, raw[:300]
        login_cookie_header = next(
            line
            for line in raw.split(b"\r\n")
            if line.lower().startswith(b"set-cookie:") and b"os_user=" in line
        ).lower()
        assert b"secure" in login_cookie_header
        cookie = _set_cookie(raw)

        status, raw = _ws_upgrade(
            node.port,
            cookie=cookie,
            origin="https://lab.example",
        )
        assert status == 101, raw[:200]

        status, raw = _post(
            node.port,
            "/api/v1/auth/logout",
            {},
            cookie=cookie,
            origin="https://evil.example",
        )
        assert status == 403, raw[:300]
        assert _body_json(raw).get("error") == "cross-origin request refused"

        status, raw = _post(
            node.port,
            "/api/v1/auth/logout",
            {},
            cookie=cookie,
            origin="https://lab.example",
        )
        assert status == 200, raw[:300]
        clear_cookie_header = next(
            line
            for line in raw.split(b"\r\n")
            if line.lower().startswith(b"set-cookie:") and b"os_user=" in line
        ).lower()
        assert b"max-age=0" in clear_cookie_header
        assert b"secure" in clear_cookie_header

        status, raw = _ws_upgrade(
            node.port,
            cookie=cookie,
            origin="https://evil.example",
        )
        assert status == 403, raw[:200]
        assert b"Sec-WebSocket-Accept" not in raw
    finally:
        node.close()


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


def test_a_disabled_account_is_told_so_only_with_the_right_password(daemon):
    """TEAM-3: the right password of a disabled account was answered exactly
    like a wrong one, so its owner kept retrying or asked for a reset (which
    does not lift a disable). Say it -- but only to the right password: with
    a wrong one a disabled account must stay indistinguishable from an
    unknown name, or the login form becomes an account-state oracle."""
    user = daemon.seed_user("carol", "fake-pw-carol")
    daemon.store.team.set_disabled(user["id"], True)

    status, raw = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "carol", "password": "fake-pw-carol"},
    )
    assert status == 403, raw[:300]
    body = _body_json(raw)
    assert body["code"] == "account_disabled"
    assert b"os_user=" not in raw  # no session for a disabled account

    wrong = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "carol", "password": "wrong"},
    )
    unknown = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "nobody-here", "password": "wrong"},
    )
    assert wrong[0] == unknown[0] == 401
    strip = lambda b: {k: v for k, v in b.items() if k != "request_id"}  # noqa: E731
    assert strip(_body_json(wrong[1])) == strip(_body_json(unknown[1]))
    actions = [r["action"] for r in daemon.store.team.list_audit()]
    assert "login_disabled" in actions and "login" not in actions

    # enabled again, the same password signs in
    daemon.store.team.set_disabled(user["id"], False)
    assert _login(daemon, "carol", "fake-pw-carol")


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


def test_login_matches_the_username_in_any_case(daemon):
    """TEAM-8: `bob` exists, so `BOB` (a phone keyboard's capital) is bob."""
    daemon.seed_user("bob", "fake-pw-bob")
    for typed in ("BOB", " Bob", "\uff42\uff4f\uff42"):
        status, raw = _post(
            daemon.port,
            "/api/v1/auth/login",
            {"username": typed, "password": "fake-pw-bob"},
        )
        assert status == 200, (typed, raw[:300])
        assert _body_json(raw)["user"]["username"] == "bob"
    status, _ = _post(
        daemon.port,
        "/api/v1/auth/login",
        {"username": "BOB", "password": "wrong"},
    )
    assert status == 401


def test_login_rate_limit_is_per_account_not_per_spelling(daemon):
    """Once any spelling signs in, every spelling must share one bucket --
    otherwise each case/width variant of a name is five more guesses."""
    daemon.seed_user("mallory", "fake-pw-real")
    for _ in range(5):
        status, _ = _post(
            daemon.port,
            "/api/v1/auth/login",
            {"username": "mallory", "password": "guess"},
        )
        assert status == 401
    for typed in ("MALLORY", "\uff4d\uff41\uff4c\uff4c\uff4f\uff52\uff59"):
        status, raw = _post(
            daemon.port,
            "/api/v1/auth/login",
            {"username": typed, "password": "guess"},
        )
        assert status == 429, (typed, raw[:300])


# -- the CLI service path -----------------------------------------------------


def test_loopback_access_token_header_still_works(daemon):
    status, raw = _get(daemon.port, "/api/v1/frames", token=daemon.token)
    assert status == 200, raw[:300]


@pytest.mark.parametrize(
    "credential_header",
    (local_auth.TOKEN_HEADER, "Authorization"),
)
def test_proxy_mode_never_mints_service_identity_from_machine_token(
    tmp_path, credential_header
):
    """A reverse proxy and the local CLI are both loopback TCP peers.

    Once that topology is declared, neither accepted spelling of the machine
    token may turn the proxy's public client into the admin-equivalent service
    principal. The attacker can omit Origin/X-Forwarded-* entirely, so this
    regression deliberately does too.
    """

    node = _TeamDaemon(
        tmp_path,
        trusted_proxy_origins=("https://lab.example",),
    )
    try:
        value = node.token
        if credential_header == "Authorization":
            value = f"Bearer {value}"
        status, raw = _speak(
            node.port,
            (
                f"GET /api/v1/auth/me HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{node.port}\r\n"
                f"{credential_header}: {value}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"),
        )
        assert status == 401, raw[:300]
        assert _body_json(raw).get("code") == "login_required"
        status, raw = _speak(
            node.port,
            (
                "GET /api/v1/auth/status HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{node.port}\r\n"
                f"{credential_header}: {value}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"),
        )
        assert status == 200, raw[:300]
        assert _body_json(raw)["authenticated"] is False

        # Proxy mode remains usable through a real user identity; only the
        # ambiguous machine-principal path is disabled.
        node.seed_user("admin", "fake-pw-admin", role="admin")
        cookie = _login(node, "admin", "fake-pw-admin")
        status, raw = _get(node.port, "/api/v1/auth/me", cookie=cookie)
        assert status == 200, raw[:300]
        me = _body_json(raw)
        assert me["user"]["username"] == "admin"
        assert me["user"]["kind"] == "user"
        status, raw = _get(node.port, "/api/v1/auth/status", cookie=cookie)
        assert status == 200, raw[:300]
        assert _body_json(raw)["authenticated"] is True
    finally:
        node.close()


def test_me_reports_the_service_identity(daemon):
    status, raw = _get(daemon.port, "/api/v1/auth/me", token=daemon.token)
    assert status == 200
    me = _body_json(raw)
    assert me["user"]["kind"] == "service"
    status, raw = _get(daemon.port, "/api/v1/auth/status", token=daemon.token)
    assert status == 200
    assert _body_json(raw)["authenticated"] is True


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


def _token_post(port: int, token: str, path: str, body: dict):
    payload = json.dumps(body).encode("utf-8")
    head = (
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"{local_auth.TOKEN_HEADER}: {token}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    return _speak(port, head + payload)


_CHANGE = "/api/v1/auth/me/password"


@pytest.mark.parametrize("role", ["admin", "member"])
def test_a_signed_in_user_changes_their_own_password(daemon, role):
    """TEAM-22: a member could only keep the CLI-generated password or ask an
    admin at the host. The change needs the current password, revokes every
    other session, and keeps this browser signed in on a fresh cookie."""
    daemon.seed_user("bob", "fake-pw-bob-1", role=role)
    here = _login(daemon, "bob", "fake-pw-bob-1")
    elsewhere = _login(daemon, "bob", "fake-pw-bob-1")

    status, raw = _post(
        daemon.port,
        _CHANGE,
        {"current_password": "fake-pw-bob-1", "new_password": "fake-pw-bob-2"},
        cookie=here,
    )
    assert status == 200, raw[:300]
    fresh = _set_cookie(raw)
    assert _get(daemon.port, "/api/v1/auth/me", cookie=fresh)[0] == 200
    assert _get(daemon.port, "/api/v1/auth/me", cookie=here)[0] == 401
    assert _get(daemon.port, "/api/v1/auth/me", cookie=elsewhere)[0] == 401
    assert daemon.store.team.verify_password("bob", "fake-pw-bob-1") is None
    assert daemon.store.team.verify_password("bob", "fake-pw-bob-2") is not None
    assert "password_change" in [r["action"] for r in daemon.store.team.list_audit()]


def test_a_password_change_needs_the_current_password_and_the_policy(daemon):
    daemon.seed_user("bob", "fake-pw-bob-1")
    cookie = _login(daemon, "bob", "fake-pw-bob-1")
    status, raw = _post(
        daemon.port,
        _CHANGE,
        {"current_password": "not-it", "new_password": "fake-pw-bob-2"},
        cookie=cookie,
    )
    assert status == 403, raw[:300]
    assert _body_json(raw)["code"] == "wrong_password"
    assert b"os_user=" not in raw
    status, raw = _post(
        daemon.port,
        _CHANGE,
        {"current_password": "fake-pw-bob-1", "new_password": "short"},
        cookie=cookie,
    )
    assert status == 400, raw[:300]
    assert _body_json(raw)["code"] == "weak_password"
    # nothing changed, and the cookie still works
    assert daemon.store.team.verify_password("bob", "fake-pw-bob-1") is not None
    assert _get(daemon.port, "/api/v1/auth/me", cookie=cookie)[0] == 200
    assert "password_change_failed" in [
        r["action"] for r in daemon.store.team.list_audit()
    ]


def test_guessing_the_current_password_is_charged_to_the_login_bucket(daemon):
    """A borrowed cookie must not become an unthrottled password oracle."""
    daemon.seed_user("bob", "fake-pw-bob-1")
    cookie = _login(daemon, "bob", "fake-pw-bob-1")  # one of the five
    for _ in range(4):
        status, _ = _post(
            daemon.port,
            _CHANGE,
            {"current_password": "guess", "new_password": "fake-pw-bob-2"},
            cookie=cookie,
        )
        assert status == 403
    status, raw = _post(
        daemon.port,
        _CHANGE,
        {"current_password": "fake-pw-bob-1", "new_password": "fake-pw-bob-2"},
        cookie=cookie,
    )
    assert status == 429, raw[:300]
    assert daemon.store.team.verify_password("bob", "fake-pw-bob-1") is not None


def test_password_change_is_closed_to_guests_strangers_and_the_cli_identity(daemon):
    daemon.seed_user("dave", "fake-pw-dave-1", role="guest")
    guest = _login(daemon, "dave", "fake-pw-dave-1")
    body = {"current_password": "fake-pw-dave-1", "new_password": "fake-pw-dave-2"}
    status, raw = _post(daemon.port, _CHANGE, body, cookie=guest)
    assert status == 403, raw[:300]
    assert _body_json(raw)["code"] == "guest_readonly"
    status, raw = _post(daemon.port, _CHANGE, body)
    assert status == 401, raw[:300]
    status, raw = _token_post(daemon.port, daemon.token, _CHANGE, body)
    assert status == 400, raw[:300]
    assert _body_json(raw)["code"] == "no_password"
    assert daemon.store.team.verify_password("dave", "fake-pw-dave-1") is not None


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


def test_the_password_route_answers_team_off_in_single_user_mode(tmp_path):
    node = _TeamDaemon(tmp_path, team_mode=False)
    try:
        status, raw = _token_post(node.port, node.token, _CHANGE, {})
        assert status == 403, raw[:300]
        assert _body_json(raw).get("code") == "team_off"
        # TEAM-28's list, likewise
        status, raw = _get(node.port, "/api/v1/auth/me/replays", token=node.token)
        assert status == 403, raw[:300]
        assert _body_json(raw).get("code") == "team_off"
    finally:
        node.close()

"""Web Diagnostics: passive GET, explicit POST, redacted bundle, no side channel."""

from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s import doctor
from openai4s.diagnostics import BUNDLE_FILENAME, BUNDLE_TEMP_PREFIX
from openai4s.server import diagnostics_routes, team_policy
from openai4s.storage import team as team_mod
from tests.test_team_auth_routes import (
    _get,
    _login,
    _post,
    _speak,
    _TeamDaemon,
)

SECRET = "sk-live-" + "aa11bb22cc33dd44ee55ff66"
PATH_CANARY = "/Users/research/private/grant-2026.csv"
PROMPT_CANARY = "unpublished-hypothesis-omega-prime-sequence"
RESEARCH_CANARY = "embargoed-cohort-gamma-raw-counts"
CANARIES = (SECRET, PATH_CANARY, PROMPT_CANARY, RESEARCH_CANARY)

_DIAG_PATHS = (
    "/api/v1/diagnostics/status",
    "/api/v1/diagnostics/checks",
    "/api/v1/diagnostics/bundle",
)


@pytest.fixture(autouse=True)
def _fast_pbkdf2(monkeypatch):
    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 1200)


@pytest.fixture(autouse=True)
def _reset_gate():
    diagnostics_routes.reset_bundle_gate_for_tests()
    yield
    diagnostics_routes.reset_bundle_gate_for_tests()


def _headers(raw: bytes) -> dict[str, str]:
    head = raw.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    out: dict[str, str] = {}
    for line in head.split("\r\n")[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip().lower()] = value.strip()
    return out


def _json_body(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def _payload(raw: bytes) -> bytes:
    return raw.split(b"\r\n\r\n", 1)[-1]


def _diag_temps() -> set[str]:
    root = Path(tempfile.gettempdir())
    return {p.name for p in root.glob(BUNDLE_TEMP_PREFIX + "*") if p.is_file()}


def _tracked_files(data_dir: Path) -> dict[str, tuple[int, int]]:
    names = (
        "openai4s.db",
        "openai4s.db-wal",
        "openai4s.db-shm",
        "openai4s.db-journal",
    )
    out: dict[str, tuple[int, int]] = {}
    for name in names:
        path = data_dir / name
        if path.exists():
            st = path.stat()
            out[name] = (st.st_size, st.st_mtime_ns)
    for path in data_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(data_dir))
        if rel.endswith(".zip") or "config" in path.name.lower():
            st = path.stat()
            out[rel] = (st.st_size, st.st_mtime_ns)
    return out


def _admin_daemon(tmp_path: Path) -> tuple[_TeamDaemon, str, str]:
    node = _TeamDaemon(tmp_path)
    node.seed_user("root", "fake-pw-r", role="admin")
    node.seed_user("bob", "fake-pw-b")
    admin = _login(node, "root", "fake-pw-r")
    member = _login(node, "bob", "fake-pw-b")
    return node, admin, member


def _get_extra(
    port: int, path: str, *, cookie: str | None = None, extra: list[str] | None = None
):
    lines = [f"GET {path} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    if cookie is not None:
        lines.append(f"Cookie: {cookie}")
    lines.extend(extra or [])
    lines.append("Connection: close")
    return _speak(port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))


def _post_extra(
    port: int,
    path: str,
    body: dict,
    *,
    cookie: str | None = None,
    extra: list[str] | None = None,
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
    lines.extend(extra or [])
    lines.append("Connection: close")
    return _speak(port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + payload)


# ---------------------------------------------------------------------------
# inventory / policy
# ---------------------------------------------------------------------------


def test_routes_are_in_the_machine_readable_inventory():
    declared = {(spec.method, spec.pattern) for spec in diagnostics_routes.ROUTES}
    assert declared == {
        ("GET", r"/diagnostics/status"),
        ("POST", r"/diagnostics/checks"),
        ("POST", r"/diagnostics/bundle"),
    }


def test_diagnostics_are_daemon_operations_on_every_verb():
    for path in (
        "/diagnostics/status",
        "/diagnostics/checks",
        "/diagnostics/bundle",
    ):
        assert team_policy.is_daemon_operation("GET", path)
        assert team_policy.is_daemon_operation("POST", path)
        assert team_policy.is_admin_only_surface("GET", path)
        assert team_policy.is_admin_only_surface("POST", path)


def test_passive_status_does_not_open_the_store_or_run_side_effect_checks(
    tmp_path, monkeypatch
):
    from openai4s.config import Config, LLMConfig

    monkeypatch.setattr(
        "openai4s.store.get_store",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("store opened")),
    )
    for name in doctor.SIDE_EFFECT_CHECKS:
        monkeypatch.setattr(
            doctor,
            f"_{name}" if name != "data" else "_data_dir",
            lambda _cfg, n=name: (_ for _ in ()).throw(AssertionError(n)),
        )
    monkeypatch.setattr(
        doctor,
        "report",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("report")),
    )
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    payload = doctor.passive_status(cfg)
    assert "security" in payload
    assert "checks" not in payload
    assert "kernel_sandbox" in payload["security"]


# ---------------------------------------------------------------------------
# authz over a real socket
# ---------------------------------------------------------------------------


def test_unauthenticated_requests_are_401(tmp_path):
    node = _TeamDaemon(tmp_path)
    try:
        node.seed_user("root", "fake-pw-r", role="admin")
        for path, method in (
            ("/api/v1/diagnostics/status", "GET"),
            ("/api/v1/diagnostics/checks", "POST"),
            ("/api/v1/diagnostics/bundle", "POST"),
        ):
            if method == "GET":
                status, raw = _get(node.port, path)
            else:
                status, raw = _post(node.port, path, {})
            assert status == 401, raw[:240]
            body = _json_body(raw)
            assert body.get("code") in {"login_required", "unauthorized"}
            assert "security" not in body
            assert "checks" not in body
            assert "content-disposition" not in _headers(raw)
    finally:
        node.close()


def test_member_always_gets_the_same_403(tmp_path):
    """403, not 404: existence of the surface is not the secret.

    The secret is the diagnostic content. Members must not learn it from
    status, size, or filename — including whether a bundle is in flight.
    """
    node, _admin, member = _admin_daemon(tmp_path)
    try:
        seen: list[tuple[int, str, int, str]] = []
        for path, method in (
            ("/api/v1/diagnostics/status", "GET"),
            ("/api/v1/diagnostics/checks", "POST"),
            ("/api/v1/diagnostics/bundle", "POST"),
        ):
            if method == "GET":
                status, raw = _get(node.port, path, cookie=member)
            else:
                status, raw = _post(node.port, path, {}, cookie=member)
            headers = _headers(raw)
            body = _json_body(raw)
            assert status == 403, raw[:240]
            assert body.get("code") == "admin_only"
            assert "security" not in body
            assert "checks" not in body
            assert "content-disposition" not in headers
            seen.append(
                (
                    status,
                    body.get("code") or "",
                    len(_payload(raw)),
                    headers.get("content-disposition", ""),
                )
            )
        assert len(set(seen)) == 1
    finally:
        node.close()


def test_member_still_gets_403_while_an_admin_bundle_is_in_flight(
    tmp_path, monkeypatch
):
    node, admin, member = _admin_daemon(tmp_path)
    hold = threading.Event()
    started = threading.Event()

    def _held(cfg, path, **_kw):
        started.set()
        hold.wait(5)
        Path(path).write_bytes(b"PK\x03\x04empty")
        return {"path": str(path), "included": [], "excluded": []}

    monkeypatch.setattr("openai4s.server.diagnostics_routes.write_bundle_file", _held)
    try:

        def _admin_download():
            _post(node.port, "/api/v1/diagnostics/bundle", {}, cookie=admin)

        worker = threading.Thread(target=_admin_download, daemon=True)
        worker.start()
        assert started.wait(5)
        status, raw = _post(node.port, "/api/v1/diagnostics/bundle", {}, cookie=member)
        assert status == 403, raw[:240]
        assert _json_body(raw).get("code") == "admin_only"
        assert "content-disposition" not in _headers(raw)
        hold.set()
        worker.join(10)
    finally:
        hold.set()
        node.close()


def test_admin_status_is_200_no_store_and_carries_request_id(tmp_path):
    node, admin, _member = _admin_daemon(tmp_path)
    try:
        status, raw = _get_extra(
            node.port,
            "/api/v1/diagnostics/status",
            cookie=admin,
            extra=["X-Request-Id: diag-req-42"],
        )
        assert status == 200, raw[:300]
        headers = _headers(raw)
        body = _json_body(raw)
        assert headers.get("cache-control") == "no-store"
        assert headers.get("x-request-id") == "diag-req-42"
        assert body["request_id"] == "diag-req-42"
        assert "security" in body
        assert "environment" in body
        assert "checks" not in body
        assert "content-disposition" not in headers
    finally:
        node.close()


def test_single_user_still_requires_the_access_token(tmp_path):
    from openai4s.server import local_auth

    node = _TeamDaemon(tmp_path, team_mode=False)
    try:
        status, _raw = _get(node.port, "/api/v1/diagnostics/status")
        assert status == 401
        status, raw = _get(
            node.port,
            "/api/v1/diagnostics/status",
            token=local_auth.load_or_mint(node.data_dir),
        )
        assert status == 200, raw[:240]
        assert "security" in _json_body(raw)
    finally:
        node.close()


# ---------------------------------------------------------------------------
# passive GET: measurable zero relative to an idle daemon
# ---------------------------------------------------------------------------


def test_status_adds_no_network_subprocess_or_business_writes(tmp_path, monkeypatch):
    node, admin, _member = _admin_daemon(tmp_path)
    pops: list[object] = []
    connects: list[object] = []
    orig_connect = socket.socket.connect
    orig_popen = subprocess.Popen.__init__

    def _connect(sock, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else address
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if host in ("127.0.0.1", "::1", "localhost") and port == node.port:
            return orig_connect(sock, address, *a, **k)
        connects.append(address)
        raise OSError("diagnostics status must not open a new connection")

    def _popen(self, *a, **k):
        pops.append(a[0] if a else k)
        raise OSError("diagnostics status must not spawn")

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(subprocess.Popen, "__init__", _popen)
    monkeypatch.setattr(
        doctor,
        "report",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("report on GET")),
    )
    try:
        files_before = _tracked_files(node.cfg.data_dir)
        temps_before = _diag_temps()
        status, raw = _get(node.port, "/api/v1/diagnostics/status", cookie=admin)
        assert status == 200, raw[:300]
        assert pops == []
        assert connects == []
        assert _tracked_files(node.cfg.data_dir) == files_before
        assert _diag_temps() == temps_before
    finally:
        monkeypatch.setattr(socket.socket, "connect", orig_connect)
        monkeypatch.setattr(subprocess.Popen, "__init__", orig_popen)
        node.close()


def test_access_audit_does_not_carry_sensitive_fields(tmp_path, monkeypatch):
    from openai4s.observability import log_event

    captured: list[dict] = []
    orig = log_event

    def _log(event, /, **fields):
        rec = orig(event, **fields)
        captured.append(rec)
        return rec

    monkeypatch.setattr("openai4s.observability.log_event", _log)
    monkeypatch.setattr("openai4s.server.gateway.log_event", _log)
    node, admin, _member = _admin_daemon(tmp_path)
    try:
        status, raw = _get_extra(
            node.port,
            "/api/v1/diagnostics/status?token=" + SECRET,
            cookie=admin,
        )
        assert status == 200, raw[:240]
        blob = json.dumps(captured)
        for canary in CANARIES:
            assert canary not in blob
        assert SECRET not in blob
        for rec in captured:
            if rec.get("event") == "http_request":
                path = str(rec.get("path") or "")
                assert "token=" not in path
                assert SECRET not in path
    finally:
        node.close()


@pytest.mark.stubbed_backend
def test_explicit_checks_call_doctor_report(tmp_path, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        doctor,
        "report",
        lambda _cfg: called.append("report") or {"status": "ok", "checks": []},
    )
    monkeypatch.setattr(
        "openai4s.server.diagnostics_routes.doctor.report",
        lambda _cfg: called.append("report") or {"status": "ok", "checks": []},
    )
    node, admin, _member = _admin_daemon(tmp_path)
    try:
        status, raw = _post(node.port, "/api/v1/diagnostics/checks", {}, cookie=admin)
        assert status == 200, raw[:240]
        body = _json_body(raw)
        assert body["status"] == "ok"
        assert "request_id" in body
        assert called == ["report"]
        assert _headers(raw).get("cache-control") == "no-store"
    finally:
        node.close()


# ---------------------------------------------------------------------------
# bundle: canaries, limits, cleanup
# ---------------------------------------------------------------------------


def _inject_canaries(cfg, monkeypatch):
    monkeypatch.setenv("HOST_PRIVATE_SLOT_ORANGE", SECRET)
    logs = Path(cfg.data_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "app.out").write_text(
        json.dumps({"HOST_PRIVATE_SLOT_ORANGE": SECRET, "prompt": PROMPT_CANARY})
        + "\n"
        + f"{PATH_CANARY} {RESEARCH_CANARY}\n",
        encoding="utf-8",
    )
    workspace = Path(cfg.data_dir) / "workspaces" / "p1"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.md").write_text(PROMPT_CANARY + "\n" + RESEARCH_CANARY)
    (Path(cfg.data_dir) / "messages.json").write_text(PROMPT_CANARY)
    (Path(cfg.data_dir) / "results.parquet").write_bytes(RESEARCH_CANARY.encode())


def test_bundle_download_has_zero_canary_hits(tmp_path, monkeypatch):
    node, admin, _member = _admin_daemon(tmp_path)
    _inject_canaries(node.cfg, monkeypatch)
    try:
        status, raw = _post_extra(
            node.port,
            "/api/v1/diagnostics/bundle",
            {},
            cookie=admin,
            extra=["X-Request-Id: diag-bundle-7"],
        )
        assert status == 200, raw[:300]
        headers = _headers(raw)
        assert headers.get("cache-control") == "no-store"
        assert headers.get("x-request-id") == "diag-bundle-7"
        assert headers.get("content-disposition") == (
            f'attachment; filename="{BUNDLE_FILENAME}"'
        )
        blob = _payload(raw)
        for canary in CANARIES:
            assert canary.encode() not in blob, canary
        assert BUNDLE_FILENAME.encode()  # filename is the fixed public name
        with zipfile.ZipFile(__import__("io").BytesIO(blob)) as archive:
            names = archive.namelist()
            joined = " ".join(names)
            for canary in CANARIES:
                assert canary not in joined
            assert "openai4s.db" not in names
            assert not any("messages" in n for n in names)
            assert not any("notebook" in n.lower() for n in names)
            assert not any("workspaces" in n for n in names)
    finally:
        node.close()


def test_client_cannot_choose_the_output_path(tmp_path):
    node, admin, _member = _admin_daemon(tmp_path)
    planted = tmp_path / "chosen.zip"
    try:
        status, raw = _post(
            node.port,
            "/api/v1/diagnostics/bundle",
            {"output": str(planted), "path": "/etc/passwd"},
            cookie=admin,
        )
        assert status == 200, raw[:240]
        assert not planted.exists()
        assert _headers(raw).get("content-disposition") == (
            f'attachment; filename="{BUNDLE_FILENAME}"'
        )
    finally:
        node.close()


@pytest.mark.stubbed_backend
def test_single_flight_returns_429(tmp_path, monkeypatch):
    node, admin, _member = _admin_daemon(tmp_path)
    hold = threading.Event()
    started = threading.Event()

    def _held(cfg, path, **_kw):
        started.set()
        hold.wait(5)
        Path(path).write_bytes(b"PK\x03\x04ok")
        return {"path": str(path), "included": [], "excluded": []}

    monkeypatch.setattr("openai4s.server.diagnostics_routes.write_bundle_file", _held)
    try:
        replies: list[int] = []
        limited: list[bytes] = []

        def _one():
            status, raw = _post(
                node.port, "/api/v1/diagnostics/bundle", {}, cookie=admin
            )
            replies.append(status)
            if status == 429:
                limited.append(raw)

        first = threading.Thread(target=_one, daemon=True)
        first.start()
        assert started.wait(5)
        second = threading.Thread(target=_one, daemon=True)
        second.start()
        second.join(10)
        hold.set()
        first.join(10)
        assert sorted(replies) == [200, 429]
        assert limited
        headers = _headers(limited[0])
        assert headers.get("cache-control") == "no-store"
        assert "content-disposition" not in headers
    finally:
        hold.set()
        node.close()


def test_per_principal_cooldown_returns_429(tmp_path):
    node, admin, _member = _admin_daemon(tmp_path)
    try:
        status, raw = _post(node.port, "/api/v1/diagnostics/bundle", {}, cookie=admin)
        assert status == 200, raw[:240]
        status, raw = _post(node.port, "/api/v1/diagnostics/bundle", {}, cookie=admin)
        assert status == 429, raw[:240]
        headers = _headers(raw)
        assert _json_body(raw).get("code") == "rate_limited"
        assert headers.get("cache-control") == "no-store"
        assert "content-disposition" not in headers
    finally:
        node.close()


@pytest.mark.stubbed_backend
def test_bundle_over_32_mib_is_413(tmp_path, monkeypatch):
    node, admin, _member = _admin_daemon(tmp_path)
    monkeypatch.setattr("openai4s.server.diagnostics_routes.BUNDLE_MAX_BYTES", 64)

    def _huge(cfg, path, **kw):
        from openai4s.diagnostics import BundleTooLarge

        raise BundleTooLarge("too big")

    monkeypatch.setattr("openai4s.server.diagnostics_routes.write_bundle_file", _huge)
    try:
        status, raw = _post(node.port, "/api/v1/diagnostics/bundle", {}, cookie=admin)
        assert status == 413, raw[:240]
        headers = _headers(raw)
        assert _json_body(raw).get("code") == "payload_too_large"
        assert headers.get("cache-control") == "no-store"
        assert "content-disposition" not in headers
    finally:
        node.close()


@pytest.mark.stubbed_backend
def test_temp_files_return_to_baseline_on_complete_disconnect_and_error(
    tmp_path, monkeypatch
):
    node, admin, _member = _admin_daemon(tmp_path)
    baseline = _diag_temps()

    def _fat(_cfg, path, **_kw):
        Path(path).write_bytes(b"PK\x03\x04" + b"x" * (512 * 1024))
        return {"path": str(path), "included": ["report.json"], "excluded": []}

    monkeypatch.setattr("openai4s.server.diagnostics_routes.write_bundle_file", _fat)
    try:
        status, raw = _post(node.port, "/api/v1/diagnostics/bundle", {}, cookie=admin)
        assert status == 200, raw[:240]
        assert _diag_temps() == baseline

        diagnostics_routes.reset_bundle_gate_for_tests()
        payload = b"{}"
        req = (
            "\r\n".join(
                [
                    "POST /api/v1/diagnostics/bundle HTTP/1.1",
                    f"Host: 127.0.0.1:{node.port}",
                    "Content-Type: application/json",
                    f"Content-Length: {len(payload)}",
                    f"Cookie: {admin}",
                    "Connection: close",
                ]
            )
            + "\r\n\r\n"
        ).encode("ascii") + payload
        conn = socket.create_connection(("127.0.0.1", node.port), timeout=10)
        try:
            conn.sendall(req)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            conn.close()
        finally:
            try:
                conn.close()
            except OSError:
                pass
        deadline = time.time() + 5
        while time.time() < deadline and _diag_temps() != baseline:
            time.sleep(0.05)
        assert _diag_temps() == baseline

        diagnostics_routes.reset_bundle_gate_for_tests()

        def _boom(*_a, **_k):
            raise RuntimeError("bundle exploded")

        monkeypatch.setattr(
            "openai4s.server.diagnostics_routes.write_bundle_file", _boom
        )
        status, _raw = _post(node.port, "/api/v1/diagnostics/bundle", {}, cookie=admin)
        assert status == 500
        assert _diag_temps() == baseline
    finally:
        node.close()


def test_handle_refuses_a_member_without_the_gateway_guard():
    handler = SimpleNamespace(
        _team_identity=SimpleNamespace(is_admin=False, user_id="bob"),
        sent=None,
        _correlation_id="r1",
    )

    def _json(body, status=200):
        handler.sent = (status, body)

    handler._json = _json
    owned = diagnostics_routes.handle(
        handler, "GET", "/diagnostics/status", cfg=SimpleNamespace()
    )
    assert owned is True
    assert handler.sent[0] == 403
    assert handler.sent[1]["code"] == "admin_only"

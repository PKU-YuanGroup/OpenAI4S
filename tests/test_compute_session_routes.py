"""Where a session runs, over real HTTP against a real daemon (M3b-6).

Driven through the socket rather than by calling `handle()`, because the
status code is half of what these routes promise and a direct call cannot
observe it — a `GatewayError` raised from a method can still reach a
browser as 200.

The claims worth checking are the boring-sounding ones. A daemon with no
worker listener must answer "this session runs locally" rather than
erroring, because that is the default for every install. A request for a
profile nobody configured must be refused rather than guessed at, because
guessing is how a job lands on a queue its owner never chose. And another
user's session must answer 404, not 403 — which sessions exist is itself
information about what a colleague is working on (INV-13).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _free_port,
    _get,
    _login,
    _post,
    _speak,
    _TeamDaemon,
)


def _body(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _TeamDaemon(tmp_path)
    node.seed_user("root", "fake-pw-r", role="admin")
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    try:
        yield node
    finally:
        node.close()


def _session_of(daemon, username: str) -> str:
    """A session this user owns, recorded through the ownership repository
    the gateway itself consults — the subject of these tests is the compute
    routes, not a second copy of session creation."""
    user = daemon.store.team.get_user_by_username(username)
    session_id = f"frame_{username}_1"
    daemon.store.team.set_session_owner(session_id, user["id"])
    return session_id


def test_a_daemon_with_no_listener_says_the_session_runs_locally(daemon):
    """Not an error: local is what every install does, and an error here
    would make the default configuration look broken."""
    cookie = _login(daemon, "alice", "fake-pw-a")
    session_id = _session_of(daemon, "alice")

    status, raw = _get(
        daemon.port, f"/api/v1/sessions/{session_id}/compute", cookie=cookie
    )
    assert status == 200
    payload = _body(raw)
    assert payload["location"] == "local"
    assert payload["workload"] is None


def test_asking_for_a_cluster_session_without_a_listener_is_refused_with_a_reason(
    daemon,
):
    cookie = _login(daemon, "alice", "fake-pw-a")
    session_id = _session_of(daemon, "alice")

    status, raw = _post(
        daemon.port,
        f"/api/v1/sessions/{session_id}/compute",
        {"profile": "gpu-interactive"},
        cookie=cookie,
    )
    assert status == 409
    payload = _body(raw)
    assert payload["code"] == "not_configured"
    assert "OPENAI4S_WORKER_LISTEN" in payload["error"]


def test_somebody_elses_session_is_not_found_rather_than_forbidden(daemon):
    """INV-13: which sessions exist is protected information."""
    bob = _login(daemon, "bob", "fake-pw-b")
    session_id = _session_of(daemon, "alice")

    status, raw = _get(
        daemon.port, f"/api/v1/sessions/{session_id}/compute", cookie=bob
    )
    assert status == 404
    assert _body(raw)["error"] == "session not found"

    status, raw = _post(
        daemon.port,
        f"/api/v1/sessions/{session_id}/compute",
        {"profile": "gpu-interactive"},
        cookie=bob,
    )
    assert status == 404

    status, raw = _post(
        daemon.port, f"/api/v1/sessions/{session_id}/compute/release", {}, cookie=bob
    )
    assert status == 404


def test_releasing_a_session_that_never_had_a_resource(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    session_id = _session_of(daemon, "alice")
    status, raw = _post(
        daemon.port,
        f"/api/v1/sessions/{session_id}/compute/release",
        {},
        cookie=cookie,
    )
    assert status == 409
    assert _body(raw)["code"] == "not_configured"


def _put(port: int, path: str, body: dict, cookie: str, method: str = "POST"):
    payload = json.dumps(body).encode("utf-8")
    head = (
        "\r\n".join(
            [
                f"{method} {path} HTTP/1.1",
                f"Host: 127.0.0.1:{port}",
                "Content-Type: application/json",
                f"Content-Length: {len(payload)}",
                f"Cookie: {cookie}",
                "Connection: close",
            ]
        )
        + "\r\n\r\n"
    ).encode("ascii")
    return _speak(port, head + payload)


@pytest.fixture()
def listening_daemon(tmp_path, monkeypatch):
    """A daemon that actually has a worker listener, so the requests that a
    default install refuses at the door get as far as the code under test."""
    monkeypatch.setenv("OPENAI4S_WORKER_LISTEN", f"127.0.0.1:{_free_port()}")
    node = _TeamDaemon(tmp_path)
    node.seed_user("alice", "fake-pw-a")
    try:
        yield node
    finally:
        node.close()


def test_checkpoint_recovery_answers_501_not_400(listening_daemon):
    """The request is well-formed and the strategy is one this product
    names; it is this version that cannot honour it. A 400 would tell the
    user they made a mistake."""
    daemon = listening_daemon
    assert (
        daemon.runner.compute_sessions is not None
    ), "the listener did not come up, so this would be testing the 409 path"
    cookie = _login(daemon, "alice", "fake-pw-a")
    session_id = _session_of(daemon, "alice")
    status, raw = _put(
        daemon.port,
        f"/api/v1/sessions/{session_id}/compute",
        {"profile": "gpu-interactive", "recovery": "CHECKPOINT"},
        cookie,
    )
    assert status == 501, raw[:300]
    body = _body(raw)
    assert body["code"] == "recovery_unsupported"
    assert body["supported"] == ["WORKSPACE_ONLY"]
    assert "not true" in body["error"]


def test_an_unconfigured_profile_is_refused_rather_than_guessed(listening_daemon):
    daemon = listening_daemon
    cookie = _login(daemon, "alice", "fake-pw-a")
    session_id = _session_of(daemon, "alice")
    status, raw = _put(
        daemon.port,
        f"/api/v1/sessions/{session_id}/compute",
        {"profile": "no-such-profile"},
        cookie,
    )
    assert status == 400
    assert _body(raw)["code"] == "unknown_profile"

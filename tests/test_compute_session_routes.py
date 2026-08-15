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

"""Session ownership and the cross-user authorization matrix (M1-6, INV-13).

Real handlers over a real socket, two seeded users plus an admin: A must not
see, read, or operate B's sessions through any enumeration or addressed route,
and an unowned (pre-team) session is admin-only. All passwords are fake.
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
    _TeamDaemon,
)


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _TeamDaemon(tmp_path)
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    node.seed_user("root", "fake-pw-r", role="admin")
    node.store.create_project(name="p", description="", context="")
    try:
        yield node
    finally:
        node.close()


def _body(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def _project_id(daemon) -> str:
    return str(daemon.store.list_projects()[0]["project_id"])


def _create_session(daemon, cookie: str, *, name: str | None = None) -> str:
    """A session, and by default a *named* one.

    The listing hides "abandoned empty" sessions -- no messages, no cells, no
    name -- so a bare frame is invisible to everybody including its owner.
    The enumeration tests below then compared two empty sets and passed with
    the ownership filter removed entirely; a name is the cheapest thing that
    makes the row real.
    """
    status, raw = _post(
        daemon.port,
        "/api/v1/frames",
        {"project_id": _project_id(daemon)},
        cookie=cookie,
    )
    assert status == 200, raw[:300]
    fid = _body(raw).get("frame_id") or _body(raw).get("id")
    assert fid
    # The listing route hides "abandoned empty" sessions -- no messages, no
    # cells, no name -- and an invisible row makes every assertion below
    # vacuous. A *message* rather than a name, because `frames.name` is null
    # in every other offline test and the frozen response shapes record it as
    # null-only; naming sessions here would widen that contract for a reason
    # that has nothing to do with the contract.
    daemon.store.add_message(
        root_frame_id=str(fid), role="user", content=name or "seeded activity"
    )
    return str(fid)


def test_creation_records_the_owner(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    fid = _create_session(daemon, cookie)
    owner = daemon.store.team.session_owner(fid)
    assert owner is not None
    assert owner["user_id"] == daemon.store.team.get_user_by_username("alice")["id"]
    assert owner["visibility"] == "project"


def test_enumeration_is_ownership_filtered(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _create_session(daemon, a)
    fid_b = _create_session(daemon, b)

    status, raw = _get(daemon.port, "/api/v1/frames?project_id=all", cookie=a)
    assert status == 200
    ids = {f.get("frame_id") or f.get("id") for f in _body(raw)["frames"]}
    # The positive control first: without it `fid_b not in ids` is satisfied
    # by an empty listing, which is what this assertion used to allow.
    assert fid_a in ids, ids
    assert fid_b not in ids

    status, raw = _get(daemon.port, "/api/v1/frames?project_id=all", cookie=b)
    ids = {f.get("frame_id") or f.get("id") for f in _body(raw)["frames"]}
    assert fid_b in ids, ids
    assert fid_a not in ids


def test_admin_sees_everything(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    fid_a = _create_session(daemon, a)
    root_cookie = _login(daemon, "root", "fake-pw-r")
    status, raw = _get(daemon.port, f"/api/v1/frames/{fid_a}", cookie=root_cookie)
    assert status == 200
    assert (_body(raw).get("frame_id") or _body(raw).get("id")) == fid_a


def test_cross_user_read_is_404_not_403(daemon):
    """B asking for A's session learns nothing — not even that it exists."""
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _create_session(daemon, a)

    for path in (
        f"/api/v1/frames/{fid_a}",
        f"/api/v1/frames/{fid_a}/messages",
        f"/api/v1/frames/{fid_a}/artifacts",
        f"/api/v1/frames/{fid_a}/execution-log",
        f"/api/v1/frames/{fid_a}/action-timeline",
    ):
        status, raw = _get(daemon.port, path, cookie=b)
        assert status == 404, (path, raw[:200])


def test_cross_user_operations_are_refused(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _create_session(daemon, a)

    # message, cancel, delete: every one 404s for B
    status, _ = _post(
        daemon.port,
        f"/api/v1/frames/{fid_a}/message",
        {"content": "hi", "wait": False},
        cookie=b,
    )
    assert status == 404
    status, _ = _post(daemon.port, f"/api/v1/frames/{fid_a}/cancel", {}, cookie=b)
    assert status == 404

    # and the owner gets past the scope guard (control for the matrix above;
    # 400 "nothing to cancel" is the route's own answer, not the guard's 404)
    status, _ = _post(daemon.port, f"/api/v1/frames/{fid_a}/cancel", {}, cookie=a)
    assert status != 404


def test_unowned_session_is_admin_only(daemon):
    """A root frame with no ownership row (pre-team history, demo seed, CLI
    run) is visible to admins and to nobody else — fail closed."""
    fid = daemon.store.new_frame(kind="turn", status="ready")
    b = _login(daemon, "bob", "fake-pw-b")
    status, _ = _get(daemon.port, f"/api/v1/frames/{fid}", cookie=b)
    assert status == 404
    root_cookie = _login(daemon, "root", "fake-pw-r")
    status, _ = _get(daemon.port, f"/api/v1/frames/{fid}", cookie=root_cookie)
    assert status == 200


def test_search_does_not_leak_other_users_sessions(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _create_session(daemon, a)
    daemon.store.update_frame(fid_a, name="alice-secret-experiment")

    status, raw = _get(daemon.port, "/api/v1/search?q=alice-secret", cookie=b)
    assert status == 200
    assert _body(raw)["sessions"] == []

    status, raw = _get(daemon.port, "/api/v1/search?q=alice-secret", cookie=a)
    ids = {s["id"] for s in _body(raw)["sessions"]}
    assert fid_a in ids


def test_project_timeline_is_ownership_filtered(daemon):
    """Two layers, and both matter: a non-participant is refused the project
    outright, and a participant still sees only sessions they may see."""
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    fid_a = _create_session(daemon, a)
    path = f"/api/v1/projects/{_project_id(daemon)}/action-timeline"

    # bob participates in nothing here: the project itself is not his to read
    assert _get(daemon.port, path, cookie=b)[0] == 404

    # once he has a session of his own in the project he may read it — and
    # alice's session is still filtered out of the cross-session view
    _create_session(daemon, b)
    status, raw = _get(daemon.port, path, cookie=b)
    assert status == 200, raw[:200]
    payload = _body(raw)
    roots = {
        g.get("session", {}).get("root_frame_id") for g in payload.get("groups", [])
    }
    assert fid_a not in roots


def test_deleting_a_session_removes_its_ownership_row(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    fid_a = _create_session(daemon, a)
    assert daemon.store.team.session_owner(fid_a) is not None
    status, raw = _speak_delete(daemon, fid_a, a)
    assert status == 200, raw[:300]
    assert daemon.store.team.session_owner(fid_a) is None


def _speak_delete(daemon, fid: str, cookie: str):
    from tests.test_team_auth_routes import _speak

    lines = [
        f"DELETE /api/v1/frames/{fid} HTTP/1.1",
        f"Host: 127.0.0.1:{daemon.port}",
        f"Cookie: {cookie}",
        "Connection: close",
    ]
    return _speak(daemon.port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))


def test_service_identity_sees_everything(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    fid_a = _create_session(daemon, a)
    status, _ = _get(daemon.port, f"/api/v1/frames/{fid_a}", token=daemon.token)
    assert status == 200


def test_an_undecidable_creator_is_refused_not_admitted(daemon, monkeypatch):
    """`team_policy`'s contract is "undecidable means refused", and its caller
    was the exception.

    `_may_create_session_in` reconstructed an identity from a bare user id and
    read three different absences as the same permissive answer: the loopback
    service (correct), a user id that is not an account, and a database that
    would not answer. Only the first is a decision.
    """
    from openai4s.server import team_auth

    runner = daemon.runner
    project_id = _project_id(daemon)
    alice = daemon.store.team.get_user_by_username("alice")["id"]

    # The service identity is admin-equivalent by decision D2, and is
    # recognised by its own constant rather than by failing to be found.
    assert runner._may_create_session_in(project_id, team_auth.SERVICE_IDENTITY.user_id)
    # A real member still gets the ordinary participation answer.
    assert runner._may_create_session_in(project_id, alice) in (True, False)

    # An id that is not an account is not "no restrictions".
    assert not runner._may_create_session_in(project_id, "user_does_not_exist")

    # Nor is a lookup that raised.
    from openai4s.storage.team import TeamRepository

    def _boom(self, user_id):
        raise RuntimeError("team table unreadable")

    monkeypatch.setattr(TeamRepository, "get_user", _boom)
    assert not runner._may_create_session_in(project_id, alice)

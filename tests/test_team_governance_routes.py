"""M2 acceptance matrix over a real socket: governance routes, D4 visibility,
guest invites and replay, quotas, metering. All passwords/tokens fake.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.agent.ledger import RuntimeActionLedger
from openai4s.agent.runtime import ChatModel
from openai4s.config import LLMConfig
from openai4s.storage.governance import QuotaExceeded
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _get,
    _login,
    _post,
    _speak,
    _TeamDaemon,
)


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _TeamDaemon(tmp_path)
    node.seed_user("root", "fake-pw-r", role="admin")
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    node.seed_user("carol", "fake-pw-c")
    node.store.create_project(name="proj-one", description="", context="")
    try:
        yield node
    finally:
        node.close()


def _body(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def _pid(daemon) -> str:
    return str(daemon.store.list_projects()[0]["project_id"])


def _uid(daemon, username: str) -> str:
    return daemon.store.team.get_user_by_username(username)["id"]


def _create_session(daemon, cookie: str) -> str:
    status, raw = _post(
        daemon.port, "/api/v1/frames", {"project_id": _pid(daemon)}, cookie=cookie
    )
    assert status == 200, raw[:300]
    return str(_body(raw).get("frame_id") or _body(raw).get("id"))


def _delete(daemon, path: str, cookie: str, body: dict | None = None):
    payload = json.dumps(body or {}).encode()
    lines = [
        f"DELETE {path} HTTP/1.1",
        f"Host: 127.0.0.1:{daemon.port}",
        f"Cookie: {cookie}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    return _speak(
        daemon.port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + payload
    )


def _put(daemon, path: str, cookie: str, body: dict):
    payload = json.dumps(body).encode()
    lines = [
        f"PUT {path} HTTP/1.1",
        f"Host: 127.0.0.1:{daemon.port}",
        f"Cookie: {cookie}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    return _speak(
        daemon.port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + payload
    )


def _patch(daemon, path: str, cookie: str, body: dict):
    payload = json.dumps(body).encode()
    lines = [
        f"PATCH {path} HTTP/1.1",
        f"Host: 127.0.0.1:{daemon.port}",
        f"Cookie: {cookie}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    return _speak(
        daemon.port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + payload
    )


# -- admin-only surface -------------------------------------------------------


def test_team_routes_are_admin_only(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    for path in ("/api/v1/team/users", "/api/v1/team/usage", "/api/v1/team/audit"):
        status, raw = _get(daemon.port, path, cookie=a)
        assert status == 403, (path, raw[:200])
        assert _body(raw).get("code") == "admin_only"
    r = _login(daemon, "root", "fake-pw-r")
    for path in ("/api/v1/team/users", "/api/v1/team/usage", "/api/v1/team/audit"):
        assert _get(daemon.port, path, cookie=r)[0] == 200


def test_admin_user_management_routes(daemon):
    r = _login(daemon, "root", "fake-pw-r")
    status, raw = _post(
        daemon.port,
        "/api/v1/team/users",
        {"username": "dave", "password": "fake-pw-d"},
        cookie=r,
    )
    assert status == 201
    dave_id = _body(raw)["user"]["id"]
    status, raw = _post(
        daemon.port, f"/api/v1/team/users/{dave_id}/disable", {}, cookie=r
    )
    assert status == 200
    assert daemon.store.team.get_user(dave_id)["disabled"] is True
    status, raw = _post(
        daemon.port, f"/api/v1/team/users/{dave_id}/reset-password", {}, cookie=r
    )
    assert status == 200
    generated = _body(raw)["password"]
    assert daemon.store.team.verify_password("dave", generated) is None  # disabled
    actions = [x["action"] for x in daemon.store.team.list_audit()]
    assert {"user_add", "user_disable", "user_reset_password"} <= set(actions)


# -- D4 visibility matrix -----------------------------------------------------


def test_project_members_see_each_other_but_not_private(daemon):
    r = _login(daemon, "root", "fake-pw-r")
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    c = _login(daemon, "carol", "fake-pw-c")
    pid = _pid(daemon)
    for name in ("alice", "bob"):
        status, _ = _put(
            daemon,
            f"/api/v1/team/projects/{pid}/members/{_uid(daemon, name)}",
            r,
            {"role": "member"},
        )
        assert status == 200

    fid_open = _create_session(daemon, a)
    fid_priv = _create_session(daemon, a)
    status, raw = _post(
        daemon.port,
        f"/api/v1/frames/{fid_priv}/visibility",
        {"visibility": "private"},
        cookie=a,
    )
    assert status == 200, raw[:200]

    # bob (same project member): open yes, private no
    assert _get(daemon.port, f"/api/v1/frames/{fid_open}", cookie=b)[0] == 200
    assert _get(daemon.port, f"/api/v1/frames/{fid_priv}", cookie=b)[0] == 404
    # carol (not a member): neither
    assert _get(daemon.port, f"/api/v1/frames/{fid_open}", cookie=c)[0] == 404
    # owner and admin: both
    assert _get(daemon.port, f"/api/v1/frames/{fid_priv}", cookie=a)[0] == 200
    assert _get(daemon.port, f"/api/v1/frames/{fid_priv}", cookie=r)[0] == 200


@pytest.mark.stubbed_backend
def test_project_member_cannot_control_another_members_kernel(daemon):
    """Project visibility is read access, not shared namespace ownership."""

    alice = _login(daemon, "alice", "fake-pw-a")
    bob = _login(daemon, "bob", "fake-pw-b")
    pid = _pid(daemon)
    for name in ("alice", "bob"):
        daemon.store.governance.set_member(pid, _uid(daemon, name), "member")
    fid = _create_session(daemon, alice)
    daemon.cfg.notebook_repl = True

    called = []
    daemon.runner.restart_kernel = lambda *args, **kwargs: called.append("restart")
    daemon.runner.stop_kernel = lambda *args, **kwargs: called.append("stop")
    daemon.runner.start_kernel = lambda *args, **kwargs: called.append("start")
    daemon.runner.set_env = lambda *args, **kwargs: called.append("env")
    daemon.runner.interrupt_kernel = lambda *args, **kwargs: called.append("interrupt")
    daemon.runner.submit_repl = lambda *args, **kwargs: (
        called.append("execute") or SimpleNamespace(wait_result=lambda: {"ok": True})
    )

    # Positive control: Bob is a real project member and may read the session.
    assert _get(daemon.port, f"/api/v1/frames/{fid}", cookie=bob)[0] == 200
    for suffix, body in (
        ("execute", {"code": "print('must not run')", "wait": True}),
        ("restart", {}),
        ("stop", {}),
        ("start", {}),
        ("env", {"name": "struct"}),
        (
            "interrupt",
            {"execution_id": "owner-run", "owner": {"kind": "agent", "id": "x"}},
        ),
    ):
        status, raw = _post(
            daemon.port,
            f"/api/v1/frames/{fid}/kernel/{suffix}",
            body,
            cookie=bob,
        )
        assert status == 403, (suffix, raw[:200])
        assert _body(raw).get("code") == "owner_only"
    assert called == [], "a refused member must not reach a lifecycle method"


@pytest.mark.stubbed_backend
def test_project_member_cannot_destroy_or_rewrite_another_members_session(daemon):
    """Project read visibility grants no destructive lifecycle authority."""

    alice = _login(daemon, "alice", "fake-pw-a")
    bob = _login(daemon, "bob", "fake-pw-b")
    pid = _pid(daemon)
    for name in ("alice", "bob"):
        daemon.store.governance.set_member(pid, _uid(daemon, name), "member")
    fid = _create_session(daemon, alice)

    called = []
    daemon.runner.execute_recovery_action = lambda *args, **kwargs: (
        called.append("recovery") or {"ok": True}
    )
    daemon.runner.activate_session_branch = lambda *args, **kwargs: (
        called.append("activate") or {"ok": True}
    )
    daemon.runner.mutate_session_domain = lambda *args, **kwargs: (
        called.append(str(kwargs.get("operation"))) or {"ok": True}
    )
    daemon.runner.delete_session = lambda *args, **kwargs: called.append("delete")

    for suffix, body in (
        ("recovery/actions/restart_fresh", {"confirm": True}),
        ("branches/branch-victim/activate", {}),
        ("revert/apply", {"target_checkpoint_id": "checkpoint-victim"}),
        ("revert/undo", {"revert_checkpoint_id": "checkpoint-victim"}),
    ):
        status, raw = _post(
            daemon.port,
            f"/api/v1/frames/{fid}/{suffix}",
            body,
            cookie=bob,
        )
        assert status == 403, (suffix, raw[:200])
        assert _body(raw).get("code") == "owner_only"
    status, raw = _delete(daemon, f"/api/v1/frames/{fid}", bob)
    assert status == 403, raw[:200]
    assert _body(raw).get("code") == "owner_only"

    assert called == [], "a refused member must not reach a destructive sink"


@pytest.mark.stubbed_backend
def test_project_visible_session_is_read_only_to_other_members(daemon):
    """Every frame-scoped write defaults to owner/admin, including new routes."""

    alice = _login(daemon, "alice", "fake-pw-a")
    bob = _login(daemon, "bob", "fake-pw-b")
    pid = _pid(daemon)
    for name in ("alice", "bob"):
        daemon.store.governance.set_member(pid, _uid(daemon, name), "member")
    fid = _create_session(daemon, alice)

    # Read access and the sole POST-shaped read stay available.
    assert _get(daemon.port, f"/api/v1/frames/{fid}", cookie=bob)[0] == 200
    daemon.runner.session_domain.revert_preview = lambda *args, **kwargs: {
        "ok": True,
        "read_only": True,
    }
    status, raw = _post(
        daemon.port,
        f"/api/v1/frames/{fid}/revert/preview",
        {"target_checkpoint_id": "checkpoint-visible"},
        cookie=bob,
    )
    assert status == 200, raw[:200]

    post_routes = (
        ("message", {"request": "must not run"}),
        ("review-settings", {"auto_review": True}),
        ("review", {}),
        (
            "cancel",
            {
                "execution_id": "victim-run",
                "owner": {"kind": "agent", "id": "victim"},
            },
        ),
        ("decision", {"decision_id": "victim", "allow": True}),
        ("feedback", {"key": "victim", "rating": "down"}),
        ("plan/approve", {}),
        ("annotations", {"artifact_id": "victim", "body": "inject"}),
        ("artifacts/promote", {"cell_id": "victim"}),
        ("checkpoints", {"reason": "planted"}),
        ("branches/fork", {"from_checkpoint_id": "victim"}),
        ("shares", {}),
        ("delegations/victim/stop", {}),
        ("compute/tasks/victim/refresh", {}),
    )
    for suffix, body in post_routes:
        status, raw = _post(
            daemon.port,
            f"/api/v1/frames/{fid}/{suffix}",
            body,
            cookie=bob,
        )
        assert status == 403, (suffix, raw[:200])
        assert _body(raw).get("code") == "owner_only"

    for suffix, body in (
        ("", {"name": "planted"}),
        ("auto-mode", {"selection": "auto_fix"}),
    ):
        status, raw = _patch(
            daemon,
            f"/api/v1/frames/{fid}{('/' + suffix) if suffix else ''}",
            bob,
            body,
        )
        assert status == 403, (suffix, raw[:200])
        assert _body(raw).get("code") == "owner_only"

    status, raw = _post(
        daemon.port,
        "/api/v1/permissions",
        {
            "scope": "conversation",
            "scope_id": fid,
            "tool": "*",
            "decision": "allow",
        },
        cookie=bob,
    )
    assert status == 403, raw[:200]
    assert _body(raw).get("code") == "owner_only"

    status, raw = _post(
        daemon.port,
        "/api/v1/permissions",
        {
            "scope": "conversation",
            "scope_id": fid,
            "tool": "read_file",
            "decision": "ask",
        },
        cookie=alice,
    )
    assert status == 200, raw[:200]


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "username,password", [("alice", "fake-pw-a"), ("root", "fake-pw-r")]
)
def test_owner_and_admin_may_control_a_session_kernel(daemon, username, password):
    alice = _login(daemon, "alice", "fake-pw-a")
    caller = _login(daemon, username, password)
    fid = _create_session(daemon, alice)
    daemon.cfg.notebook_repl = True

    daemon.runner.restart_kernel = lambda *args, **kwargs: {"ok": True}
    daemon.runner.stop_kernel = lambda *args, **kwargs: {"ok": True}
    daemon.runner.start_kernel = lambda *args, **kwargs: {"ok": True}
    daemon.runner.set_env = lambda *args, **kwargs: {"ok": True}
    daemon.runner.interrupt_kernel = lambda *args, **kwargs: {"ok": True}
    daemon.runner.submit_repl = lambda *args, **kwargs: SimpleNamespace(
        wait_result=lambda: {"ok": True}
    )

    for suffix, body in (
        ("execute", {"code": "print('allowed')", "wait": True}),
        ("restart", {}),
        ("stop", {}),
        ("start", {}),
        ("env", {"name": "struct"}),
        (
            "interrupt",
            {"execution_id": "owner-run", "owner": {"kind": "agent", "id": "x"}},
        ),
    ):
        status, raw = _post(
            daemon.port,
            f"/api/v1/frames/{fid}/kernel/{suffix}",
            body,
            cookie=caller,
        )
        assert status == 200, (username, suffix, raw[:200])


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "username,password", [("alice", "fake-pw-a"), ("root", "fake-pw-r")]
)
def test_owner_and_admin_may_mutate_a_session_lifecycle(daemon, username, password):
    alice = _login(daemon, "alice", "fake-pw-a")
    caller = _login(daemon, username, password)
    fid = _create_session(daemon, alice)

    called = []
    daemon.runner.execute_recovery_action = lambda *args, **kwargs: (
        called.append("recovery") or {"ok": True}
    )
    daemon.runner.activate_session_branch = lambda *args, **kwargs: (
        called.append("activate") or {"ok": True}
    )
    daemon.runner.mutate_session_domain = lambda *args, **kwargs: (
        called.append(str(kwargs.get("operation"))) or {"ok": True}
    )
    daemon.runner.delete_session = lambda *args, **kwargs: called.append("delete")

    for suffix, body in (
        ("recovery/actions/restart_fresh", {"confirm": True}),
        ("branches/branch-owner/activate", {}),
        ("revert/apply", {"target_checkpoint_id": "checkpoint-owner"}),
        ("revert/undo", {"revert_checkpoint_id": "checkpoint-owner"}),
    ):
        status, raw = _post(
            daemon.port,
            f"/api/v1/frames/{fid}/{suffix}",
            body,
            cookie=caller,
        )
        assert status == 200, (username, suffix, raw[:200])
    status, raw = _delete(daemon, f"/api/v1/frames/{fid}", caller)
    assert status == 200, (username, raw[:200])
    assert called == [
        "recovery",
        "activate",
        "revert_session",
        "undo_revert",
        "delete",
    ]


@pytest.mark.stubbed_backend
def test_frame_scoped_kernel_install_is_admin_only_in_team_mode(daemon):
    alice = _login(daemon, "alice", "fake-pw-a")
    root = _login(daemon, "root", "fake-pw-r")
    fid = _create_session(daemon, alice)
    called = []

    def install(packages, **kwargs):
        called.append(packages)
        return {"ok": True, "installed": packages}

    daemon.runner.install_packages = install

    status, raw = _post(
        daemon.port,
        f"/api/v1/frames/{fid}/kernel/install",
        {"package": "numpy"},
        cookie=alice,
    )
    assert status == 403, raw[:200]
    assert _body(raw).get("code") == "admin_only"
    assert called == []

    status, raw = _post(
        daemon.port,
        f"/api/v1/frames/{fid}/kernel/install",
        {"package": "numpy"},
        cookie=root,
    )
    assert status == 200, raw[:200]
    assert called == [["numpy"]]


def test_admin_read_of_private_session_is_audited_per_view(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    r = _login(daemon, "root", "fake-pw-r")
    fid = _create_session(daemon, a)
    _post(
        daemon.port,
        f"/api/v1/frames/{fid}/visibility",
        {"visibility": "private"},
        cookie=a,
    )
    before = len(daemon.store.team.list_audit(action="admin_read_private"))
    assert _get(daemon.port, f"/api/v1/frames/{fid}", cookie=r)[0] == 200
    assert _get(daemon.port, f"/api/v1/frames/{fid}/messages", cookie=r)[0] == 200
    rows = daemon.store.team.list_audit(action="admin_read_private")
    assert len(rows) == before + 2  # every view, not the first
    assert rows[0]["target"] == fid
    # the owner's own reads are not "admin reads"
    assert _get(daemon.port, f"/api/v1/frames/{fid}", cookie=a)[0] == 200
    assert len(daemon.store.team.list_audit(action="admin_read_private")) == before + 2


def test_admin_ws_subscribe_to_private_session_is_audited(daemon):
    """A live subscription is a view too (D4)."""
    from tests.test_team_ws_isolation import _WSClient

    a = _login(daemon, "alice", "fake-pw-a")
    r = _login(daemon, "root", "fake-pw-r")
    fid = _create_session(daemon, a)
    _post(
        daemon.port,
        f"/api/v1/frames/{fid}/visibility",
        {"visibility": "private"},
        cookie=a,
    )
    before = len(daemon.store.team.list_audit(action="admin_read_private"))
    ws = _WSClient(daemon.port, r)
    try:
        ws.send({"type": "view_session", "root_frame_id": fid})
        reply = ws.recv_json()
        assert reply is not None and reply["type"] != "view_denied"
    finally:
        ws.close()
    assert len(daemon.store.team.list_audit(action="admin_read_private")) == before + 1


def test_visibility_toggle_is_owner_only_over_http(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    b = _login(daemon, "bob", "fake-pw-b")
    r = _login(daemon, "root", "fake-pw-r")
    pid = _pid(daemon)
    for name in ("alice", "bob"):
        daemon.store.governance.set_member(pid, _uid(daemon, name), "member")
    fid = _create_session(daemon, a)
    # Bob can see the project Session, but only its owner controls D4
    # visibility. Admin is intentionally not an owner substitute here either.
    assert _get(daemon.port, f"/api/v1/frames/{fid}", cookie=b)[0] == 200
    status, _ = _post(
        daemon.port,
        f"/api/v1/frames/{fid}/visibility",
        {"visibility": "private"},
        cookie=b,
    )
    assert status == 404
    status, _ = _post(
        daemon.port,
        f"/api/v1/frames/{fid}/visibility",
        {"visibility": "private"},
        cookie=r,
    )
    assert status == 404

    # Authorization precedes semantic and JSON parsing. Otherwise a guessed
    # Session answers 400 only when it exists, leaking the ownership row.
    status, _ = _post(
        daemon.port,
        f"/api/v1/frames/{fid}/visibility",
        {"visibility": "bogus"},
        cookie=b,
    )
    assert status == 404
    malformed = b"{"
    lines = [
        f"POST /api/v1/frames/{fid}/visibility HTTP/1.1",
        f"Host: 127.0.0.1:{daemon.port}",
        f"Cookie: {b}",
        "Content-Type: application/json",
        f"Content-Length: {len(malformed)}",
        "Connection: close",
    ]
    status, raw = _speak(
        daemon.port,
        ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + malformed,
    )
    assert status == 404, raw[:200]
    assert _body(raw).get("code") != "malformed_json"

    status, _ = _post(
        daemon.port,
        f"/api/v1/frames/{fid}/visibility",
        {"visibility": "bogus"},
        cookie=a,
    )
    assert status == 400


# -- guest invites + replay ---------------------------------------------------


def _invite_guest(daemon, root_cookie: str, username: str) -> str:
    """Create+redeem an invite; returns the guest's login cookie."""
    status, raw = _post(
        daemon.port,
        "/api/v1/team/invites",
        {"project_id": _pid(daemon)},
        cookie=root_cookie,
    )
    assert status == 201, raw[:300]
    token = _body(raw)["token"]
    status, raw = _post(
        daemon.port,
        "/api/v1/auth/redeem-invite",
        {"token": token, "username": username, "password": "fake-guest-pw"},
    )
    assert status == 201, raw[:300]
    for line in raw.split(b"\r\n"):
        if line.lower().startswith(b"set-cookie:") and b"os_user=" in line:
            return line.split(b":", 1)[1].strip().decode().split(";", 1)[0]
    raise AssertionError("no cookie on redeem")


def test_invite_redeem_creates_replay_only_guest(daemon):
    r = _login(daemon, "root", "fake-pw-r")
    a = _login(daemon, "alice", "fake-pw-a")
    fid = _create_session(daemon, a)
    guest_cookie = _invite_guest(daemon, r, "visitor")

    # replay of a project-visibility session: allowed
    status, raw = _get(
        daemon.port, f"/api/v1/sessions/{fid}/replay", cookie=guest_cookie
    )
    assert status == 200, raw[:300]
    view = _body(raw)
    assert view.get("schema_version")

    # everything else: refused (D3)
    for path in ("/api/v1/frames", "/api/v1/files", f"/api/v1/frames/{fid}"):
        status, raw = _get(daemon.port, path, cookie=guest_cookie)
        assert status == 403, (path, raw[:200])
        assert _body(raw).get("code") == "guest_readonly"
    status, _ = _post(
        daemon.port, "/api/v1/frames", {"project_id": _pid(daemon)}, cookie=guest_cookie
    )
    assert status == 403

    # a private session is not replayable by the guest
    _post(
        daemon.port,
        f"/api/v1/frames/{fid}/visibility",
        {"visibility": "private"},
        cookie=a,
    )
    status, _ = _get(daemon.port, f"/api/v1/sessions/{fid}/replay", cookie=guest_cookie)
    assert status == 404

    # /auth/me still works so the guest UI can know who it is
    assert _get(daemon.port, "/api/v1/auth/me", cookie=guest_cookie)[0] == 200


def test_invite_is_single_use_and_username_collision_does_not_burn_it(daemon):
    r = _login(daemon, "root", "fake-pw-r")
    status, raw = _post(
        daemon.port, "/api/v1/team/invites", {"project_id": _pid(daemon)}, cookie=r
    )
    token = _body(raw)["token"]
    # collision with an existing username: refused WITHOUT consuming the token
    status, _ = _post(
        daemon.port,
        "/api/v1/auth/redeem-invite",
        {"token": token, "username": "alice", "password": "x"},
    )
    assert status == 409
    status, _ = _post(
        daemon.port,
        "/api/v1/auth/redeem-invite",
        {"token": token, "username": "fresh", "password": "fake-pw"},
    )
    assert status == 201
    status, _ = _post(
        daemon.port,
        "/api/v1/auth/redeem-invite",
        {"token": token, "username": "fresh2", "password": "fake-pw"},
    )
    assert status == 403


def test_member_replay_and_isolation(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    c = _login(daemon, "carol", "fake-pw-c")
    fid = _create_session(daemon, a)
    assert _get(daemon.port, f"/api/v1/sessions/{fid}/replay", cookie=a)[0] == 200
    # not a member, not the owner: 404, not 403
    assert _get(daemon.port, f"/api/v1/sessions/{fid}/replay", cookie=c)[0] == 404
    assert _get(daemon.port, "/api/v1/sessions/f-none/replay", cookie=a)[0] == 404


# -- usage + quotas -----------------------------------------------------------


def test_session_quota_blocks_creation_and_usage_is_reported(daemon):
    r = _login(daemon, "root", "fake-pw-r")
    a = _login(daemon, "alice", "fake-pw-a")
    status, _ = _post(
        daemon.port,
        "/api/v1/team/quotas",
        {
            "scope": "user",
            "scope_id": _uid(daemon, "alice"),
            "kind": "sessions_created",
            "limit_amount": 1,
            "window": "day",
        },
        cookie=r,
    )
    assert status == 200
    _create_session(daemon, a)
    status, raw = _post(
        daemon.port, "/api/v1/frames", {"project_id": _pid(daemon)}, cookie=a
    )
    assert status == 429, raw[:300]
    assert _body(raw).get("code") == "QUOTA_EXCEEDED"

    status, raw = _get(daemon.port, "/api/v1/team/usage", cookie=r)
    assert status == 200
    rows = _body(raw)["usage"]
    mine = [
        x
        for x in rows
        if x["user_id"] == _uid(daemon, "alice") and x["kind"] == "sessions_created"
    ]
    assert mine and mine[0]["total"] == 1

    # admin can list and delete the quota
    status, raw = _get(daemon.port, "/api/v1/team/quotas", cookie=r)
    assert _body(raw)["quotas"][0]["kind"] == "sessions_created"
    status, _ = _delete(
        daemon,
        "/api/v1/team/quotas",
        r,
        {
            "scope": "user",
            "scope_id": _uid(daemon, "alice"),
            "kind": "sessions_created",
            "window": "day",
        },
    )
    assert status == 200
    _create_session(daemon, a)  # allowed again


# -- metering units -----------------------------------------------------------


def test_ledger_attributes_llm_usage_to_the_owner(daemon):
    a = _login(daemon, "alice", "fake-pw-a")
    fid = _create_session(daemon, a)
    ledger = RuntimeActionLedger(
        store=daemon.store, root_frame_id=fid, turn_id="turn-1"
    )
    ledger._record_team_usage({"input_tokens": 100, "output_tokens": 7})
    # A provider may finish after Stop has already quarantined its content.
    # Those counters still belong to the session owner and quota ledger.
    ledger.record_abandoned_usage({"input_tokens": 11, "output_tokens": 2})
    # The gateway forwards the reply's own usage mapping, so the alias-only
    # OpenAI shape has to meter too: `record_session_llm_usage` reads the
    # canonical names, and dropping the aliases would let Stop bypass the
    # quota ledger silently -- exactly what this path exists to prevent.
    ledger.record_abandoned_usage({"prompt_tokens": 5, "completion_tokens": 1})
    rows = daemon.store.governance.usage_summary(user_id=_uid(daemon, "alice"))
    by_kind = {r["kind"]: r["total"] for r in rows}
    assert by_kind["llm_input_tokens"] == 116
    assert by_kind["llm_output_tokens"] == 10

    # no ownership row -> no rows written (single-user inertness, INV-1)
    orphan = daemon.store.new_frame(kind="turn", status="ready")
    ledger2 = RuntimeActionLedger(
        store=daemon.store, root_frame_id=orphan, turn_id="turn-2"
    )
    before = len(daemon.store.governance.usage_summary())
    ledger2._record_team_usage({"input_tokens": 5, "output_tokens": 5})
    assert len(daemon.store.governance.usage_summary()) == before


def test_chat_model_quota_gate_runs_before_the_provider_call():
    calls = []

    def gate():
        raise QuotaExceeded(
            "llm quota exhausted", scope="user", kind="llm_input_tokens", window="day"
        )

    model = ChatModel(
        LLMConfig(provider="deepseek", api_key="test-key"),
        lambda *a, **k: calls.append(1) or {"content": "x"},
        quota_gate=gate,
    )
    with pytest.raises(QuotaExceeded):
        model.complete([{"role": "user", "content": "hi"}], lambda d: None)
    assert calls == []  # the provider was never reached

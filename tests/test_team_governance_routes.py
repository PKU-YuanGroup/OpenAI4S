"""M2 acceptance matrix over a real socket: governance routes, D4 visibility,
guest invites and replay, quotas, metering. All passwords/tokens fake.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    fid = _create_session(daemon, a)
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
    rows = daemon.store.governance.usage_summary(user_id=_uid(daemon, "alice"))
    by_kind = {r["kind"]: r["total"] for r in rows}
    assert by_kind["llm_input_tokens"] == 100
    assert by_kind["llm_output_tokens"] == 7

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

"""Governance storage (M2): membership, invites, usage ledger, quotas,
and the D4 visibility semantics they enable. All tokens/passwords fake.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.storage.governance import QuotaExceeded, invite_digest
from openai4s.store import get_store


@pytest.fixture()
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


# -- membership ---------------------------------------------------------------


def test_membership_upsert_list_remove(store):
    store.governance.set_member("proj_a", "user_1")
    store.governance.set_member("proj_a", "user_1", role="guest")  # upsert
    store.governance.set_member("proj_a", "user_2")
    assert store.governance.member_role("proj_a", "user_1") == "guest"
    assert [m["user_id"] for m in store.governance.list_members("proj_a")] == [
        "user_1",
        "user_2",
    ]
    assert store.governance.projects_of("user_1") == [
        {"project_id": "proj_a", "role": "guest"}
    ]
    assert store.governance.remove_member("proj_a", "user_1") is True
    assert store.governance.member_role("proj_a", "user_1") is None
    with pytest.raises(ValueError):
        store.governance.set_member("proj_a", "user_3", role="owner")


# -- D4 visibility ------------------------------------------------------------


def test_project_visibility_semantics(store):
    """D4: project-open to members, private to the owner, guests never widen,
    no project means private."""
    store.team.create_user(username="alice", password="fake-a")
    alice = store.team.get_user_by_username("alice")
    bob_user = store.team.create_user(username="bob", password="fake-b")
    bob = {"id": bob_user["id"], "role": "member", "kind": "user"}
    guest_user = store.team.create_user(username="g", password="fake-g", role="guest")
    guest = {"id": guest_user["id"], "role": "guest", "kind": "user"}

    store.team.set_session_owner("f-proj", alice["id"], project_id="proj_a")
    store.team.set_session_owner("f-priv", alice["id"], project_id="proj_a")
    store.team.set_session_visibility("f-priv", "private", user_id=alice["id"])
    store.team.set_session_owner("f-noproj", alice["id"])  # no project

    # not yet a member: nothing of alice's is visible to bob
    assert store.team.session_visible_to("f-proj", bob) is False

    store.governance.set_member("proj_a", bob["id"])
    store.governance.set_member("proj_a", guest["id"], role="guest")

    assert store.team.session_visible_to("f-proj", bob) is True
    assert store.team.session_visible_to("f-priv", bob) is False
    assert store.team.session_visible_to("f-noproj", bob) is False
    # a guest membership never widens general access
    assert store.team.session_visible_to("f-proj", guest) is False


def test_visibility_toggle_is_owner_only(store):
    store.team.set_session_owner("f-x", "user_owner", project_id="proj_a")
    assert (
        store.team.set_session_visibility("f-x", "private", user_id="user_other")
        is False
    )
    assert (
        store.team.set_session_visibility("f-x", "private", user_id="user_owner")
        is True
    )
    assert store.team.session_owner("f-x")["visibility"] == "private"
    with pytest.raises(ValueError):
        store.team.set_session_visibility("f-x", "secret", user_id="user_owner")


def test_enumeration_includes_project_visible_sessions(store):
    """The browse filter (M1-6) widened for D4: members see each other's
    project-visibility sessions, not the private ones."""
    fid_a = store.new_frame(kind="turn", project_id="proj_a")
    fid_b = store.new_frame(kind="turn", project_id="proj_a")
    store.team.set_session_owner(fid_a, "user_alice", project_id="proj_a")
    store.team.set_session_owner(fid_b, "user_alice", project_id="proj_a")
    store.team.set_session_visibility(fid_b, "private", user_id="user_alice")
    store.governance.set_member("proj_a", "user_bob")

    seen = {
        f["frame_id"]
        for f in store.browse_frames(project_id="all", visible_to_user_id="user_bob")
    }
    assert fid_a in seen
    assert fid_b not in seen


# -- invites ------------------------------------------------------------------


def test_invite_round_trip_single_use_and_expiry(store):
    token = store.governance.create_invite("proj_a", "admin_1")
    # only the digest is stored
    row = store._conn.execute("SELECT token_hash FROM invites").fetchone()[0]
    assert row == invite_digest(token) != token

    redeemed = store.governance.redeem_invite(token)
    assert redeemed == {"project_id": "proj_a", "created_by": "admin_1"}
    # single use
    assert store.governance.redeem_invite(token) is None

    expired = store.governance.create_invite("proj_a", "admin_1", ttl_s=0)
    assert store.governance.redeem_invite(expired) is None
    assert store.governance.redeem_invite("garbage") is None


def test_invite_listing_leaks_no_token(store):
    token = store.governance.create_invite("proj_a", "admin_1")
    rows = store.governance.list_invites()
    assert rows[0]["token_prefix"] == invite_digest(token)[:12]
    assert token not in str(rows)
    assert rows[0]["live"] is True
    assert store.governance.revoke_invite(rows[0]["token_prefix"]) is True
    assert store.governance.redeem_invite(token) is None


# -- usage ledger -------------------------------------------------------------


def test_usage_recording_and_aggregation(store):
    store.governance.record_usage(
        user_id="u1", kind="llm_input_tokens", amount=100, project_id="p1", ref="f-1"
    )
    store.governance.record_usage(
        user_id="u1", kind="llm_input_tokens", amount=50, project_id="p1", ref="f-2"
    )
    store.governance.record_usage(user_id="u2", kind="kernel_cpu_s", amount=2.5)
    rows = store.governance.usage_summary()
    by_key = {(r["user_id"], r["kind"]): r for r in rows}
    assert by_key[("u1", "llm_input_tokens")]["total"] == 150
    assert by_key[("u1", "llm_input_tokens")]["events"] == 2
    assert by_key[("u2", "kernel_cpu_s")]["total"] == 2.5

    only_u1 = store.governance.usage_summary(user_id="u1")
    assert {r["user_id"] for r in only_u1} == {"u1"}
    # a row with no user is dropped, not attributed to nobody
    store.governance.record_usage(user_id="", kind="llm_input_tokens", amount=5)
    assert store.governance.usage_summary() == rows


# -- quotas -------------------------------------------------------------------


def test_quota_verdicts(store):
    # no quota rows -> allowed
    store.governance.check_quota(user_id="u1", project_id="p1", kind="llm_input_tokens")

    store.governance.set_quota(
        scope="user",
        scope_id="u1",
        kind="llm_input_tokens",
        limit_amount=100,
        window="day",
    )
    store.governance.record_usage(
        user_id="u1", kind="llm_input_tokens", amount=99, project_id="p1"
    )
    store.governance.check_quota(user_id="u1", project_id="p1", kind="llm_input_tokens")
    store.governance.record_usage(
        user_id="u1", kind="llm_input_tokens", amount=1, project_id="p1"
    )
    with pytest.raises(QuotaExceeded) as e:
        store.governance.check_quota(
            user_id="u1", project_id="p1", kind="llm_input_tokens"
        )
    assert e.value.code == "QUOTA_EXCEEDED"

    # a project-scope quota binds every member's usage in that project
    store.governance.set_quota(
        scope="project",
        scope_id="p2",
        kind="sessions_created",
        limit_amount=0,
        window="week",
    )
    with pytest.raises(QuotaExceeded):
        store.governance.check_quota(
            user_id="u9", project_id="p2", kind="sessions_created"
        )
    # other projects unaffected
    store.governance.check_quota(user_id="u9", project_id="p3", kind="sessions_created")


def test_quota_set_validation_and_listing(store):
    kind = "llm_input_tokens"
    with pytest.raises(ValueError):
        store.governance.set_quota(
            scope="org", scope_id="x", kind=kind, limit_amount=1, window="day"
        )
    with pytest.raises(ValueError):
        store.governance.set_quota(
            scope="user", scope_id="x", kind=kind, limit_amount=1, window="hour"
        )
    # A quota on a kind no enforcement point consults is refused rather than
    # silently recorded: an admin who set it would believe the resource was
    # capped. kernel_cpu_s is metered but not yet enforced.
    with pytest.raises(ValueError):
        store.governance.set_quota(
            scope="user",
            scope_id="x",
            kind="kernel_cpu_s",
            limit_amount=1,
            window="day",
        )
    with pytest.raises(ValueError):
        store.governance.set_quota(
            scope="user", scope_id="x", kind="", limit_amount=1, window="day"
        )
    store.governance.set_quota(
        scope="user", scope_id="x", kind=kind, limit_amount=1, window="day"
    )
    store.governance.set_quota(
        scope="user", scope_id="x", kind=kind, limit_amount=9, window="day"
    )  # upsert
    rows = store.governance.list_quotas()
    assert rows == [
        {
            "scope": "user",
            "scope_id": "x",
            "kind": kind,
            "limit_amount": 9.0,
            "window": "day",
        }
    ]
    assert (
        store.governance.delete_quota(
            scope="user", scope_id="x", kind=kind, window="day"
        )
        is True
    )
    assert store.governance.list_quotas() == []


def test_host_query_cannot_read_governance_tables(store):
    for table in ("project_members", "invites", "usage_ledger", "quotas"):
        with pytest.raises(PermissionError):
            store.query(f"SELECT * FROM {table}")

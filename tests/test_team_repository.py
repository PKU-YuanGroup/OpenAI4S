"""Team-mode identity storage (storage/team.py): users, sessions, audit.

Passwords here are deliberately fake test values (never real credentials).
Most tests shrink PBKDF2_ITERATIONS so the suite stays fast; the one contract
test that pins the production cost leaves the constant alone without hashing.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.storage import team as team_mod
from openai4s.store import get_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    # 600k PBKDF2 rounds per hash is right for production and wrong for a
    # test loop; the row records its own iteration count, so shrinking the
    # constant here exercises the identical code path.
    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 1200)
    return get_store(Config(data_dir=tmp_path).db_path)


def test_production_iteration_cost_is_pinned():
    # M1-2 contract: pbkdf2_hmac('sha256', ..., 600_000). The row records the
    # count, so bumping this constant must stay a deliberate act. This test
    # takes no store fixture, so the constant is unpatched here.
    assert team_mod.PBKDF2_ITERATIONS == 600_000


def test_repository_shares_store_connection_and_lock(store):
    assert store.team._connection is store._conn
    assert store.team._lock is store._lock


def test_create_verify_and_reject(store):
    user = store.team.create_user(
        username="alice", password="test-password-not-real", role="admin"
    )
    assert user["id"].startswith("user_")
    assert user["role"] == "admin"

    ok = store.team.verify_password("alice", "test-password-not-real")
    assert ok is not None and ok["id"] == user["id"]
    assert store.team.verify_password("alice", "wrong-password") is None
    assert store.team.verify_password("nobody", "test-password-not-real") is None


def test_duplicate_username_and_bad_role_refused(store):
    store.team.create_user(username="alice", password="test-password-not-real")
    with pytest.raises(ValueError):
        store.team.create_user(username="alice", password="other-fake-password")
    with pytest.raises(ValueError):
        store.team.create_user(username="bob", password="x", role="superuser")
    with pytest.raises(ValueError):
        store.team.create_user(username="", password="x")
    with pytest.raises(ValueError):
        store.team.create_user(username="carol", password="")


def test_compatibility_equivalent_usernames_are_refused(store):
    """SQLite NOCASE is ASCII-only, while usernames name filesystem areas.

    Kelvin sign normalizes to ``K`` under NFKC, so allowing both would create
    two account rows for one portable/case-insensitive path identity.
    """
    store.team.create_user(username="K", password="test-password-not-real")
    with pytest.raises(ValueError, match="already exists"):
        store.team.create_user(username="K", password="other-fake-password")


def test_an_upgraded_database_with_portable_username_collisions_fails_closed(
    store,
):
    """Creation guards cannot repair conflicting rows from an older release."""
    path = store.db_path
    original = store.team.create_user(username="K", password="test-password-not-real")
    row = store._conn.execute(
        "SELECT password_hash, password_salt, iterations, created_at"
        " FROM users WHERE id=?",
        (original["id"],),
    ).fetchone()
    store._conn.execute(
        "INSERT INTO users(id,username,display_name,role,password_hash,"
        "password_salt,iterations,disabled,created_at) VALUES(?,?,?,?,?,?,?,0,?)",
        (
            "legacy_collision",
            "K",
            None,
            "member",
            row["password_hash"],
            row["password_salt"],
            row["iterations"],
            row["created_at"] + 1,
        ),
    )
    store._conn.commit()
    store.close()

    with pytest.raises(RuntimeError, match="same portable filesystem identity"):
        get_store(path)


def test_password_hash_never_stored_plaintext(store):
    store.team.create_user(username="alice", password="test-password-not-real")
    row = store._conn.execute(
        "SELECT password_hash, password_salt, iterations FROM users"
    ).fetchone()
    assert b"test-password-not-real" not in bytes(row["password_hash"])
    assert row["iterations"] == 1200  # the shrunk fixture value, recorded per row
    assert len(bytes(row["password_salt"])) == 16


def test_verify_uses_row_iterations_not_the_constant(store, monkeypatch):
    # A future cost bump must not invalidate existing hashes: verification
    # reads the per-row iteration count.
    store.team.create_user(username="alice", password="test-password-not-real")
    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 2400)
    assert store.team.verify_password("alice", "test-password-not-real") is not None


def test_disabled_user_cannot_login_and_sessions_die(store):
    user = store.team.create_user(username="alice", password="test-password-not-real")
    token = store.team.create_auth_session(user["id"])
    assert store.team.resolve_auth_session(token)["id"] == user["id"]

    store.team.set_disabled(user["id"], True)
    assert store.team.verify_password("alice", "test-password-not-real") is None
    assert store.team.resolve_auth_session(token) is None

    store.team.set_disabled(user["id"], False)
    assert store.team.verify_password("alice", "test-password-not-real") is not None
    # the old session stays revoked; re-enabling does not resurrect cookies
    assert store.team.resolve_auth_session(token) is None


def test_auth_session_round_trip_and_revoke(store):
    user = store.team.create_user(username="alice", password="test-password-not-real")
    token = store.team.create_auth_session(user["id"])
    assert store.team.resolve_auth_session(token)["username"] == "alice"
    assert store.team.resolve_auth_session("no-such-token") is None
    assert store.team.resolve_auth_session(None) is None

    assert store.team.revoke_auth_session(token) is True
    assert store.team.resolve_auth_session(token) is None
    assert store.team.revoke_auth_session(token) is False


def test_auth_session_expiry(store):
    user = store.team.create_user(username="alice", password="test-password-not-real")
    token = store.team.create_auth_session(user["id"], ttl_s=0)
    assert store.team.resolve_auth_session(token) is None
    # the expired row is reaped on resolve
    left = store._conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    assert left == 0


def test_raw_token_never_stored(store):
    user = store.team.create_user(username="alice", password="test-password-not-real")
    token = store.team.create_auth_session(user["id"])
    stored = store._conn.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
    assert stored != token
    assert stored == team_mod.token_digest(token)


def test_password_reset_revokes_sessions(store):
    user = store.team.create_user(username="alice", password="test-password-not-real")
    token = store.team.create_auth_session(user["id"])
    store.team.set_password(user["id"], "new-fake-password")
    assert store.team.resolve_auth_session(token) is None
    assert store.team.verify_password("alice", "test-password-not-real") is None
    assert store.team.verify_password("alice", "new-fake-password") is not None


def test_purge_expired_sessions(store):
    user = store.team.create_user(username="alice", password="test-password-not-real")
    store.team.create_auth_session(user["id"], ttl_s=0)
    keep = store.team.create_auth_session(user["id"])
    assert store.team.purge_expired_sessions() == 1
    assert store.team.resolve_auth_session(keep) is not None


def test_audit_log_records_and_filters(store):
    store.team.audit(actor="alice", action="login")
    store.team.audit(
        actor="admin",
        action="admin_read_private",
        user_id="user_x",
        target="f-abc",
        detail="viewed",
    )
    rows = store.team.list_audit()
    assert [r["action"] for r in rows] == ["admin_read_private", "login"]
    only = store.team.list_audit(action="login")
    assert len(only) == 1 and only[0]["actor"] == "alice"


def test_user_listing_and_count(store):
    assert store.team.count_users() == 0
    store.team.create_user(username="alice", password="fake-a", role="admin")
    store.team.create_user(username="bob", password="fake-b")
    assert store.team.count_users() == 2
    names = [u["username"] for u in store.team.list_users()]
    assert names == ["alice", "bob"]
    assert store.team.get_user_by_username("bob")["role"] == "member"


def test_host_query_cannot_read_identity_tables(store):
    # INV-9 hygiene: the agent-facing SQL surface refuses these tables.
    for table in ("users", "auth_sessions", "team_audit_log", "session_owners"):
        with pytest.raises(PermissionError):
            store.query(f"SELECT * FROM {table}")

"""Team-mode identity storage: users, login sessions, and the audit log.

This backs `OPENAI4S_TEAM_MODE` (docs/team-server-plan.md M1). Three tables,
all additive — no existing table changes shape, so single-user installs are
untouched (INV-1):

  users           account rows. The password is stored as
                  ``pbkdf2_hmac('sha256', password, salt, iterations)`` with a
                  per-user random salt and the iteration count *recorded on the
                  row*, so a future cost bump re-hashes lazily on next login
                  instead of invalidating every account.
  auth_sessions   browser login sessions. Only ``sha256(token)`` is stored;
                  the raw token exists in the cookie and nowhere else, so a
                  database read (or backup leak) cannot mint a valid cookie.
  team_audit_log  governance-sensitive actions (INV-12): login/logout, user
                  management, admin reads of private sessions. Each row carries
                  ``(actor, delegated_by, user, project, action, target)``.

All three are in ``store.QUERY_DENYLIST``: ``host.query`` must never read
password or token material (INV-9 hygiene at the storage layer).

Verification is constant-time (`hmac.compare_digest`), and unknown usernames
burn the same PBKDF2 work as wrong passwords so the two are not separable by
timing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from typing import Any, Callable

#: PBKDF2-HMAC-SHA256 iteration count for new hashes (plan M1-2). Recorded
#: per-row so this constant can move without a migration.
PBKDF2_ITERATIONS = 600_000

_ROLES = ("admin", "member", "guest")

#: Login-session lifetime (seconds). 14 days: long enough that a lab member is
#: not re-authenticating daily, short enough that a leaked cookie dies.
SESSION_TTL_S = 14 * 24 * 3600

TEAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL CHECK (role IN ('admin','member','guest')),
    password_hash BLOB NOT NULL,
    password_salt BLOB NOT NULL,
    iterations    INTEGER NOT NULL,
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    last_seen_at INTEGER
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS team_audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    actor        TEXT NOT NULL,
    delegated_by TEXT,
    user_id      TEXT,
    project_id   TEXT,
    action       TEXT NOT NULL,
    target       TEXT,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS ix_team_audit_ts ON team_audit_log(ts);
CREATE INDEX IF NOT EXISTS ix_team_audit_actor ON team_audit_log(actor);
"""


def create_team_schema(conn: sqlite3.Connection) -> None:
    """Idempotent DDL, called from the numbered Store migration."""
    conn.executescript(TEAM_SCHEMA)


def hash_password(password: str, salt: bytes, iterations: int | None = None) -> bytes:
    """None -> the current module constant, resolved at call time so the cost
    can be tuned (tests shrink it) without the default going stale."""
    rounds = PBKDF2_ITERATIONS if iterations is None else iterations
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)


def token_digest(token: str) -> str:
    """The stored form of a login token: hex sha256 of the raw value."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_row(row: Any) -> dict:
    return {
        "id": row[0],
        "username": row[1],
        "display_name": row[2],
        "role": row[3],
        "disabled": bool(row[4]),
        "created_at": row[5],
    }


_USER_COLS = "id, username, display_name, role, disabled, created_at"


class TeamRepository:
    """Accounts, login sessions, and the team audit log."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms

    # --- users -----------------------------------------------------------

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str = "member",
        display_name: str | None = None,
    ) -> dict:
        username = username.strip()
        if not username:
            raise ValueError("username must be non-empty")
        if role not in _ROLES:
            raise ValueError(f"role must be one of {_ROLES}, got {role!r}")
        if not password:
            raise ValueError("password must be non-empty")
        salt = secrets.token_bytes(16)
        digest = hash_password(password, salt)
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        now = self._clock_ms()
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO users(id, username, display_name, role,"
                    " password_hash, password_salt, iterations, disabled,"
                    " created_at) VALUES(?,?,?,?,?,?,?,0,?)",
                    (
                        user_id,
                        username,
                        display_name,
                        role,
                        digest,
                        salt,
                        PBKDF2_ITERATIONS,
                        now,
                    ),
                )
                self._connection.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"username {username!r} already exists") from exc
        return {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "role": role,
            "disabled": False,
            "created_at": now,
        }

    def get_user(self, user_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {_USER_COLS} FROM users WHERE id=?", (user_id,)
            ).fetchone()
        return _user_row(row) if row else None

    def get_user_by_username(self, username: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {_USER_COLS} FROM users WHERE username=?",
                (username.strip(),),
            ).fetchone()
        return _user_row(row) if row else None

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                f"SELECT {_USER_COLS} FROM users ORDER BY created_at, username"
            ).fetchall()
        return [_user_row(r) for r in rows]

    def count_users(self) -> int:
        with self._lock:
            return int(
                self._connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            )

    def set_disabled(self, user_id: str, disabled: bool) -> bool:
        """Disable also revokes every live login session — a disabled account
        must not keep riding an already-issued cookie."""
        with self._lock:
            cur = self._connection.execute(
                "UPDATE users SET disabled=? WHERE id=?",
                (1 if disabled else 0, user_id),
            )
            if disabled:
                self._connection.execute(
                    "DELETE FROM auth_sessions WHERE user_id=?", (user_id,)
                )
            self._connection.commit()
        return cur.rowcount > 0

    def set_password(self, user_id: str, password: str) -> bool:
        """Reset also revokes live sessions: the reset is the recovery move
        after a suspected compromise, so old cookies must die with it."""
        if not password:
            raise ValueError("password must be non-empty")
        salt = secrets.token_bytes(16)
        digest = hash_password(password, salt)
        with self._lock:
            cur = self._connection.execute(
                "UPDATE users SET password_hash=?, password_salt=?, iterations=?"
                " WHERE id=?",
                (digest, salt, PBKDF2_ITERATIONS, user_id),
            )
            self._connection.execute(
                "DELETE FROM auth_sessions WHERE user_id=?", (user_id,)
            )
            self._connection.commit()
        return cur.rowcount > 0

    def verify_password(self, username: str, password: str) -> dict | None:
        """The user row on success; None for wrong password, unknown user, or
        a disabled account — indistinguishably, and in constant-ish time."""
        with self._lock:
            row = self._connection.execute(
                "SELECT id, password_hash, password_salt, iterations, disabled"
                " FROM users WHERE username=?",
                (username.strip(),),
            ).fetchone()
        if row is None:
            # Burn the same PBKDF2 work an existing user costs, so "no such
            # user" and "wrong password" have the same timing profile.
            hash_password(password, b"timing-equalizer-salt")
            return None
        user_id, stored, salt, iterations, disabled = row
        candidate = hash_password(password, bytes(salt), int(iterations))
        if not hmac.compare_digest(candidate, bytes(stored)):
            return None
        if disabled:
            return None
        return self.get_user(str(user_id))

    # --- login sessions --------------------------------------------------

    def create_auth_session(self, user_id: str, *, ttl_s: int = SESSION_TTL_S) -> str:
        """Mint a login session; returns the raw token exactly once."""
        token = secrets.token_urlsafe(32)
        now = self._clock_ms()
        with self._lock:
            self._connection.execute(
                "INSERT INTO auth_sessions(token_hash, user_id, created_at,"
                " expires_at, last_seen_at) VALUES(?,?,?,?,?)",
                (token_digest(token), user_id, now, now + ttl_s * 1000, now),
            )
            self._connection.commit()
        return token

    def resolve_auth_session(self, token: str | None) -> dict | None:
        """The live user for a cookie token, or None (expired, revoked,
        unknown, or the account is disabled)."""
        if not token:
            return None
        digest = token_digest(token)
        now = self._clock_ms()
        with self._lock:
            row = self._connection.execute(
                "SELECT s.user_id, s.expires_at FROM auth_sessions s"
                " JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash=? AND u.disabled=0",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            user_id, expires_at = row
            if int(expires_at) <= now:
                self._connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash=?", (digest,)
                )
                self._connection.commit()
                return None
            self._connection.execute(
                "UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?",
                (now, digest),
            )
            self._connection.commit()
        return self.get_user(str(user_id))

    def revoke_auth_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            cur = self._connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash=?",
                (token_digest(token),),
            )
            self._connection.commit()
        return cur.rowcount > 0

    def purge_expired_sessions(self) -> int:
        with self._lock:
            cur = self._connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at<=?",
                (self._clock_ms(),),
            )
            self._connection.commit()
        return cur.rowcount

    # --- audit (INV-12) --------------------------------------------------

    def audit(
        self,
        *,
        actor: str,
        action: str,
        delegated_by: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        target: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO team_audit_log(ts, actor, delegated_by, user_id,"
                " project_id, action, target, detail) VALUES(?,?,?,?,?,?,?,?)",
                (
                    self._clock_ms(),
                    actor,
                    delegated_by,
                    user_id,
                    project_id,
                    action,
                    target,
                    detail,
                ),
            )
            self._connection.commit()

    def list_audit(self, *, limit: int = 200, action: str | None = None) -> list[dict]:
        sql = (
            "SELECT id, ts, actor, delegated_by, user_id, project_id, action,"
            " target, detail FROM team_audit_log"
        )
        params: tuple = ()
        if action:
            sql += " WHERE action=?"
            params = (action,)
        sql += " ORDER BY id DESC LIMIT ?"
        params += (max(1, min(int(limit), 1000)),)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [
            {
                "id": r[0],
                "ts": r[1],
                "actor": r[2],
                "delegated_by": r[3],
                "user_id": r[4],
                "project_id": r[5],
                "action": r[6],
                "target": r[7],
                "detail": r[8],
            }
            for r in rows
        ]


__all__ = [
    "PBKDF2_ITERATIONS",
    "SESSION_TTL_S",
    "TeamRepository",
    "create_team_schema",
    "hash_password",
    "token_digest",
]

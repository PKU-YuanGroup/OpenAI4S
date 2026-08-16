"""Team-mode governance storage: membership, invites, usage, quotas (M2).

Four tables, all additive (INV-1), all behind the same numbered-migration
discipline as storage/team.py:

  project_members  who belongs to which project, with a per-project role.
                   Admins are implicit members of everything and have no
                   rows here; a row's role is 'member' or 'guest'.
  invites          project-scoped, expiring guest invitations. Only
                   ``sha256(token)`` is stored — the raw invite token is
                   printed once by whoever created it (D4-adjacent hygiene:
                   a database read must not mint a working invite).
  usage_ledger     append-only metering rows (M2-5): LLM tokens and kernel
                   CPU seconds, attributed ``(user, project, kind, amount,
                   ref)``. Aggregation happens in SQL here, not in callers.
  quotas           per-user/per-project limits by kind and window (M2-6).
                   The *check* lives here beside the ledger it reads;
                   callers get a verdict, never raw rows.

All four are agent-invisible through ``host.query`` (QUERY_DENYLIST):
invites hold credential digests, and the rest is governance state.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Any, Callable

from openai4s.storage.migrations import apply_ddl_script

#: Invite lifetime default (seconds): one week.
INVITE_TTL_S = 7 * 24 * 3600

#: The metering kinds the ledger understands today. Free-form on disk —
#: new kinds must not need a migration — but named here so callers and
#: aggregators cannot drift on spelling.
KIND_LLM_INPUT_TOKENS = "llm_input_tokens"
KIND_LLM_OUTPUT_TOKENS = "llm_output_tokens"
KIND_KERNEL_CPU_S = "kernel_cpu_s"
KIND_SESSIONS_CREATED = "sessions_created"

#: The kinds a *quota* may name. Deliberately narrower than what the ledger
#: records: `kernel_cpu_s` is metered but has no enforcement point yet, and a
#: limit nothing consults is worse than no limit — an admin sets it, believes
#: the resource is capped, and it is not. Refusing the write is what makes the
#: gap visible. Widen this set in the same commit that adds the check.
ENFORCED_QUOTA_KINDS = frozenset(
    {KIND_LLM_INPUT_TOKENS, KIND_LLM_OUTPUT_TOKENS, KIND_SESSIONS_CREATED}
)

_QUOTA_WINDOWS_MS = {
    "day": 24 * 3600 * 1000,
    "week": 7 * 24 * 3600 * 1000,
    "month": 30 * 24 * 3600 * 1000,
}

GOVERNANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member','guest')),
    UNIQUE (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_project_members_user ON project_members(user_id);
CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at    INTEGER
);
CREATE TABLE IF NOT EXISTS usage_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    user_id    TEXT NOT NULL,
    project_id TEXT,
    kind       TEXT NOT NULL,
    amount     REAL NOT NULL,
    ref        TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_user_kind_ts ON usage_ledger(user_id, kind, ts);
CREATE INDEX IF NOT EXISTS ix_usage_project_ts ON usage_ledger(project_id, ts);
CREATE TABLE IF NOT EXISTS quotas (
    scope        TEXT NOT NULL CHECK (scope IN ('user','project')),
    scope_id     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    limit_amount REAL NOT NULL,
    window       TEXT NOT NULL CHECK (window IN ('day','week','month')),
    UNIQUE (scope, scope_id, kind, window)
);
"""


def create_governance_schema(conn: sqlite3.Connection) -> None:
    """Idempotent DDL, called from the numbered Store migration."""
    apply_ddl_script(conn, GOVERNANCE_SCHEMA)


def invite_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class QuotaExceeded(Exception):
    """A metered action was refused (M2-6). Carries the standard code."""

    code = "QUOTA_EXCEEDED"

    def __init__(self, message: str, *, scope: str, kind: str, window: str):
        super().__init__(message)
        self.scope = scope
        self.kind = kind
        self.window = window


class GovernanceRepository:
    """Membership, invites, metering, and quota verdicts."""

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

    # --- project membership (M2-1) ---------------------------------------

    def set_member(self, project_id: str, user_id: str, role: str = "member") -> None:
        if role not in ("member", "guest"):
            raise ValueError("role must be 'member' or 'guest'")
        with self._lock:
            self._connection.execute(
                "INSERT INTO project_members(project_id, user_id, role)"
                " VALUES(?,?,?)"
                " ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role",
                (project_id, user_id, role),
            )
            self._connection.commit()

    def remove_member(self, project_id: str, user_id: str) -> bool:
        with self._lock:
            cur = self._connection.execute(
                "DELETE FROM project_members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            )
            self._connection.commit()
        return cur.rowcount > 0

    def list_members(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT project_id, user_id, role FROM project_members"
                " WHERE project_id=? ORDER BY user_id",
                (project_id,),
            ).fetchall()
        return [dict(zip(("project_id", "user_id", "role"), r)) for r in rows]

    def member_role(self, project_id: str | None, user_id: str) -> str | None:
        if not project_id:
            return None
        with self._lock:
            row = self._connection.execute(
                "SELECT role FROM project_members WHERE project_id=? AND user_id=?",
                (project_id, user_id),
            ).fetchone()
        return str(row[0]) if row else None

    def projects_of(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT project_id, role FROM project_members WHERE user_id=?"
                " ORDER BY project_id",
                (user_id,),
            ).fetchall()
        return [{"project_id": r[0], "role": r[1]} for r in rows]

    def project_is_claimed(self, project_id: str | None) -> bool:
        """Has anybody made this project theirs yet?

        Claimed means it has a **membership** row -- deliberately not "has
        somebody's session in it". Every project a user creates gets a
        creator membership row, so the ones that belong to someone are
        exactly the ones this returns True for. The seeded `default` and
        example projects have no members and never will unless somebody
        adds one, so they stay open: they are the shared scratch space a
        fresh install hands everybody, and closing them behind the first
        person to use them would make team mode arrive broken.

        Using session ownership here instead would also defeat the purpose.
        Session ownership is half of the participation union that creating a
        session grants, so treating it as a claim would let the first
        unauthorized join lock the project for everyone after it.
        """
        if not project_id:
            return False
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM project_members WHERE project_id=? LIMIT 1",
                (project_id,),
            ).fetchone()
        return row is not None

    def is_project_participant(self, project_id: str | None, user_id: str) -> bool:
        """May this user address a project at all (M2-1 authz)?

        A participant is a member (``project_members`` row) OR the owner of
        at least one session in the project (``session_owners``). The second
        clause keeps a member's own project reachable before an admin has
        added a membership row — and keeps single-user 'default'/'proj_example'
        sessions reachable by whoever created them.
        """
        if not project_id or not user_id:
            return False
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM project_members WHERE project_id=? AND user_id=?"
                " UNION SELECT 1 FROM session_owners WHERE project_id=?"
                " AND user_id=? LIMIT 1",
                (project_id, user_id, project_id, user_id),
            ).fetchone()
        return row is not None

    def participant_project_ids(self, user_id: str) -> set[str]:
        """Every project id this user participates in (member or session
        owner) — the filter for a non-admin's project list."""
        if not user_id:
            return set()
        with self._lock:
            rows = self._connection.execute(
                "SELECT project_id FROM project_members WHERE user_id=?"
                " UNION SELECT project_id FROM session_owners WHERE user_id=?"
                " AND project_id IS NOT NULL",
                (user_id, user_id),
            ).fetchall()
        return {str(r[0]) for r in rows if r[0]}

    # --- invites (M2-4) ---------------------------------------------------

    def create_invite(
        self, project_id: str, created_by: str, *, ttl_s: int = INVITE_TTL_S
    ) -> str:
        """Mint an invite; the raw token is returned exactly once."""
        token = secrets.token_urlsafe(24)
        now = self._clock_ms()
        with self._lock:
            self._connection.execute(
                "INSERT INTO invites(token_hash, project_id, created_by,"
                " expires_at, used_at) VALUES(?,?,?,?,NULL)",
                (invite_digest(token), project_id, created_by, now + ttl_s * 1000),
            )
            self._connection.commit()
        return token

    def redeem_invite(self, token: str) -> dict | None:
        """Consume a live invite; None when unknown, expired, or used.

        Single UPDATE with the liveness predicate in its WHERE — two racing
        redemptions cannot both win.
        """
        digest = invite_digest(token or "")
        now = self._clock_ms()
        with self._lock:
            cur = self._connection.execute(
                "UPDATE invites SET used_at=? WHERE token_hash=? AND used_at IS"
                " NULL AND expires_at>?",
                (now, digest, now),
            )
            if cur.rowcount == 0:
                self._connection.commit()
                return None
            row = self._connection.execute(
                "SELECT project_id, created_by FROM invites WHERE token_hash=?",
                (digest,),
            ).fetchone()
            self._connection.commit()
        return {"project_id": row[0], "created_by": row[1]}

    def reinstate_invite(self, token: str) -> bool:
        """Undo a redemption whose follow-up work failed.

        Redemption marks `used_at` before the account exists, because the
        mark is what makes it single-use under concurrency. If account
        creation then fails (a username race), the invite was consumed for
        nothing — so it is handed back rather than left burnt. Only an
        unexpired invite is reinstated: an expired one stays consumed.
        """
        digest = invite_digest(token or "")
        with self._lock:
            cur = self._connection.execute(
                "UPDATE invites SET used_at=NULL WHERE token_hash=? AND"
                " used_at IS NOT NULL AND expires_at>?",
                (digest, self._clock_ms()),
            )
            self._connection.commit()
        return cur.rowcount > 0

    def list_invites(self, *, project_id: str | None = None) -> list[dict]:
        """Digest-prefixed metadata only — never anything a client could
        turn back into a working token."""
        sql = (
            "SELECT token_hash, project_id, created_by, expires_at, used_at"
            " FROM invites"
        )
        params: tuple = ()
        if project_id:
            sql += " WHERE project_id=?"
            params = (project_id,)
        sql += " ORDER BY expires_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        now = self._clock_ms()
        return [
            {
                "token_prefix": r[0][:12],
                "project_id": r[1],
                "created_by": r[2],
                "expires_at": r[3],
                "used_at": r[4],
                "live": r[4] is None and r[3] > now,
            }
            for r in rows
        ]

    def revoke_invite(self, token_prefix: str) -> bool:
        """Revocation by the listed prefix (the raw token is long gone).

        Prefix-matched with ``substr`` rather than ``LIKE``: ``LIKE`` would
        read ``%`` / ``_`` in the admin-supplied prefix as wildcards, so a
        ``DELETE /team/invites/%`` would revoke *every* live invite at once.
        ``substr(token_hash, 1, len)`` compares literal characters only.
        """
        prefix = str(token_prefix or "")
        if not prefix:
            return False
        with self._lock:
            cur = self._connection.execute(
                "UPDATE invites SET used_at=? WHERE"
                " substr(token_hash, 1, ?) = ? AND used_at IS NULL",
                (self._clock_ms(), len(prefix), prefix),
            )
            self._connection.commit()
        return cur.rowcount > 0

    # --- usage ledger (M2-5) ---------------------------------------------

    def record_usage(
        self,
        *,
        user_id: str,
        kind: str,
        amount: float,
        project_id: str | None = None,
        ref: str | None = None,
    ) -> None:
        if not user_id or amount is None:
            return
        with self._lock:
            self._connection.execute(
                "INSERT INTO usage_ledger(ts, user_id, project_id, kind,"
                " amount, ref) VALUES(?,?,?,?,?,?)",
                (self._clock_ms(), user_id, project_id, kind, float(amount), ref),
            )
            self._connection.commit()

    def usage_summary(
        self,
        *,
        since_ms: int | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict]:
        clauses, params = [], []
        if since_ms is not None:
            clauses.append("ts>=?")
            params.append(since_ms)
        if user_id:
            clauses.append("user_id=?")
            params.append(user_id)
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT user_id, project_id, kind, SUM(amount) AS total,"
                " COUNT(*) AS events FROM usage_ledger"
                + where
                + " GROUP BY user_id, project_id, kind"
                " ORDER BY user_id, project_id, kind",
                params,
            ).fetchall()
        return [
            {
                "user_id": r[0],
                "project_id": r[1],
                "kind": r[2],
                "total": r[3],
                "events": r[4],
            }
            for r in rows
        ]

    # --- quotas (M2-6) ----------------------------------------------------

    def set_quota(
        self,
        *,
        scope: str,
        scope_id: str,
        kind: str,
        limit_amount: float,
        window: str,
    ) -> None:
        if scope not in ("user", "project"):
            raise ValueError("scope must be 'user' or 'project'")
        if window not in _QUOTA_WINDOWS_MS:
            raise ValueError(f"window must be one of {sorted(_QUOTA_WINDOWS_MS)}")
        if kind not in ENFORCED_QUOTA_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(ENFORCED_QUOTA_KINDS)}; a quota "
                f"on {kind!r} would be recorded and never consulted"
            )
        with self._lock:
            self._connection.execute(
                "INSERT INTO quotas(scope, scope_id, kind, limit_amount, window)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(scope, scope_id, kind, window)"
                " DO UPDATE SET limit_amount=excluded.limit_amount",
                (scope, scope_id, kind, float(limit_amount), window),
            )
            self._connection.commit()

    def delete_quota(
        self, *, scope: str, scope_id: str, kind: str, window: str
    ) -> bool:
        with self._lock:
            cur = self._connection.execute(
                "DELETE FROM quotas WHERE scope=? AND scope_id=? AND kind=?"
                " AND window=?",
                (scope, scope_id, kind, window),
            )
            self._connection.commit()
        return cur.rowcount > 0

    def list_quotas(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT scope, scope_id, kind, limit_amount, window FROM quotas"
                " ORDER BY scope, scope_id, kind, window"
            ).fetchall()
        return [
            dict(zip(("scope", "scope_id", "kind", "limit_amount", "window"), r))
            for r in rows
        ]

    def check_quota(self, *, user_id: str, project_id: str | None, kind: str) -> None:
        """Raise :class:`QuotaExceeded` when a metered action must be refused.

        Consumption is measured from the ledger over a sliding window. A
        limit of 0 means "none allowed". No quota rows -> allowed.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT scope, scope_id, limit_amount, window FROM quotas"
                " WHERE kind=? AND ((scope='user' AND scope_id=?)"
                " OR (scope='project' AND scope_id=?))",
                (kind, user_id, project_id or ""),
            ).fetchall()
            if not rows:
                return
            now = self._clock_ms()
            for scope, scope_id, limit_amount, window in rows:
                since = now - _QUOTA_WINDOWS_MS[str(window)]
                if scope == "user":
                    where, param = "user_id=?", user_id
                else:
                    where, param = "project_id=?", scope_id
                used = self._connection.execute(
                    f"SELECT COALESCE(SUM(amount), 0) FROM usage_ledger"
                    f" WHERE kind=? AND ts>=? AND {where}",
                    (kind, since, param),
                ).fetchone()[0]
                if float(used) >= float(limit_amount):
                    raise QuotaExceeded(
                        f"{scope} {kind} quota exhausted"
                        f" ({used:g}/{limit_amount:g} per {window})",
                        scope=str(scope),
                        kind=str(kind),
                        window=str(window),
                    )


__all__ = [
    "ENFORCED_QUOTA_KINDS",
    "GovernanceRepository",
    "INVITE_TTL_S",
    "KIND_KERNEL_CPU_S",
    "KIND_LLM_INPUT_TOKENS",
    "KIND_LLM_OUTPUT_TOKENS",
    "KIND_SESSIONS_CREATED",
    "QuotaExceeded",
    "create_governance_schema",
    "invite_digest",
]


def record_session_llm_usage(store: Any, root_frame_id: str, usage: Any) -> None:
    """Charge one provider call's tokens to the session's owner (M2-5).

    The single metering hook for every LLM call the daemon makes on a
    session's behalf. It exists as a function rather than as code inside
    the turn ledger because the reviewer reaches the provider through its
    own port, and it was billing only the legacy per-frame counters -- so a
    member could run reviews forever without the ledger the quota check
    reads ever advancing. Two writers of the same fact drift; one function
    called from two places does not.

    With no ownership row it reads and never writes (INV-1). Metering must
    never break the call it meters, so every failure here is swallowed.
    """
    if not usage:
        return
    try:
        governance = getattr(store, "governance", None)
        team = getattr(store, "team", None)
        if governance is None or team is None:
            return
        scope = store.resolve_frame_scope(root_frame_id)
        root = scope["root_frame_id"]
        owner = team.session_owner(root)
        if owner is None:
            return
        project = owner["project_id"] or scope.get("project_id")
        for kind, key in (
            ("llm_input_tokens", "input_tokens"),
            ("llm_output_tokens", "output_tokens"),
        ):
            amount = usage.get(key) or 0
            if amount:
                governance.record_usage(
                    user_id=owner["user_id"],
                    kind=kind,
                    amount=float(amount),
                    project_id=project,
                    ref=root,
                )
    except Exception:  # noqa: BLE001 — metering must not break the call
        pass

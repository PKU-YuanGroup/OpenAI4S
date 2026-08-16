"""A session's hold on a cluster resource, and when it lapses (M3b-4).

Two tables, both from the plan's appendix A shape:

    leases(workload_id PK, created_at, last_active_at, idle_ttl_s, max_lifetime_s)
    session_workloads(session_id PK, workload_id UNIQUE)

The second one is the deviation the plan's DDL does not carry, and it is
here because a SESSION workload has to be findable *from* the chat session
in both directions: forward to answer "is my kernel ready yet", backward to
answer "whose timeline should say the kernel state was lost". Putting the
session id inside `spec_json` would have made both lookups a table scan
through JSON, and made the pairing unenforceable — a UNIQUE column says
one session holds at most one workload and one workload backs at most one
session, which is exactly the rule.

The subtle half is `last_active_at`, and it is subtle in one direction
only: **a worker being alive is not a user being present**. A cluster
session whose kernel process is healthy, whose socket is connected and
whose heartbeats arrive on time is still an idle session if nobody has run
anything in it — and it is holding GPUs. So nothing in the transport, the
watchdog or the reclaimer's own probe touches this column. Only a user
executing something, or explicitly asking to keep it, does. Getting this
backwards produces a lease that renews itself forever, which is the same
as having no lease at all while looking like diligence.

Timestamps are integer milliseconds, matching the rest of `storage/`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

LEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    workload_id    TEXT PRIMARY KEY,
    created_at     INTEGER NOT NULL,
    last_active_at INTEGER NOT NULL,
    idle_ttl_s     INTEGER NOT NULL,
    max_lifetime_s INTEGER NOT NULL,
    released_at    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_leases_live ON leases(released_at);

CREATE TABLE IF NOT EXISTS session_workloads (
    session_id  TEXT PRIMARY KEY,
    workload_id TEXT NOT NULL UNIQUE,
    created_at  INTEGER NOT NULL
);
"""


def create_lease_schema(conn: sqlite3.Connection) -> None:
    """Idempotent DDL, called from the numbered Store migration."""
    conn.executescript(LEASE_SCHEMA)


@dataclass(frozen=True)
class Lease:
    """One session's claim on a resource, and the two clocks that end it."""

    workload_id: str
    created_at: int
    last_active_at: int
    idle_ttl_s: int
    max_lifetime_s: int
    released_at: int | None = None

    def idle_deadline_ms(self) -> int:
        return self.last_active_at + self.idle_ttl_s * 1000

    def lifetime_deadline_ms(self) -> int:
        return self.created_at + self.max_lifetime_s * 1000

    def expiry(self, now_ms: int) -> str | None:
        """Which clock ran out, if either. Lifetime wins ties.

        The order matters for the reason code an operator reads afterwards:
        a session that hit its maximum lifetime *while* idle should be
        reported as having hit its maximum lifetime, because that is the
        limit the user cannot renew past and the one they need told about.
        """
        if self.released_at is not None:
            return None
        if now_ms >= self.lifetime_deadline_ms():
            return "max_lifetime"
        if now_ms >= self.idle_deadline_ms():
            return "idle"
        return None


class LeaseRepository:
    """Leases and session↔workload bindings over the Store's connection."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: Any,
        clock_ms: Callable[[], int],
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._clock_ms = clock_ms

    # --- bindings ---------------------------------------------------------

    def bind_session(self, session_id: str, workload_id: str) -> None:
        """Pair a chat session with the workload backing its kernel.

        `INSERT OR REPLACE` on the session id, so re-binding after a
        recovery (same workload, new epoch) is a no-op rather than a
        constraint violation — recovery does not create a new workload, and
        a binding that refused to be rewritten would make it look like it
        should.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO session_workloads"
                " (session_id, workload_id, created_at) VALUES (?,?,?)",
                (session_id, workload_id, self._clock_ms()),
            )
            self._conn.commit()

    def workload_for_session(self, session_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT workload_id FROM session_workloads WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return row[0] if row else None

    def session_for_workload(self, workload_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM session_workloads WHERE workload_id=?",
                (workload_id,),
            ).fetchone()
        return row[0] if row else None

    def unbind_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM session_workloads WHERE session_id=?", (session_id,)
            )
            self._conn.commit()

    # --- leases -----------------------------------------------------------

    def open_lease(
        self, workload_id: str, *, idle_ttl_s: int, max_lifetime_s: int
    ) -> Lease:
        """Start (or restart) a lease. Both clocks reset.

        A reopened lease is a new lease: recovery placed a new allocation,
        and charging the replacement for the wall time the lost one already
        burned would cap a recovered session at an arbitrary fraction of
        the limit an operator configured.
        """
        now = self._clock_ms()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO leases"
                " (workload_id, created_at, last_active_at, idle_ttl_s,"
                "  max_lifetime_s, released_at) VALUES (?,?,?,?,?,NULL)",
                (workload_id, now, now, int(idle_ttl_s), int(max_lifetime_s)),
            )
            self._conn.commit()
        return Lease(
            workload_id=workload_id,
            created_at=now,
            last_active_at=now,
            idle_ttl_s=int(idle_ttl_s),
            max_lifetime_s=int(max_lifetime_s),
        )

    def touch(self, workload_id: str) -> bool:
        """Record user activity. **Only** a user's activity may call this.

        Not the transport, not the watchdog, not the reclaimer's own probe:
        see the module docstring. Returns False if there is no live lease,
        so a caller cannot mistake "kept alive" for "there was nothing to
        keep".
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE leases SET last_active_at=?"
                " WHERE workload_id=? AND released_at IS NULL",
                (self._clock_ms(), workload_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def get(self, workload_id: str) -> Lease | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT workload_id, created_at, last_active_at, idle_ttl_s,"
                " max_lifetime_s, released_at FROM leases WHERE workload_id=?",
                (workload_id,),
            ).fetchone()
        return _row_to_lease(row) if row else None

    def live_leases(self) -> list[Lease]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT workload_id, created_at, last_active_at, idle_ttl_s,"
                " max_lifetime_s, released_at FROM leases"
                " WHERE released_at IS NULL"
            ).fetchall()
        return [_row_to_lease(row) for row in rows]

    def release(self, workload_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE leases SET released_at=?"
                " WHERE workload_id=? AND released_at IS NULL",
                (self._clock_ms(), workload_id),
            )
            self._conn.commit()


def _row_to_lease(row: Any) -> Lease:
    return Lease(
        workload_id=row[0],
        created_at=int(row[1]),
        last_active_at=int(row[2]),
        idle_ttl_s=int(row[3]),
        max_lifetime_s=int(row[4]),
        released_at=int(row[5]) if row[5] is not None else None,
    )


__all__ = [
    "LEASE_SCHEMA",
    "Lease",
    "LeaseRepository",
    "create_lease_schema",
]

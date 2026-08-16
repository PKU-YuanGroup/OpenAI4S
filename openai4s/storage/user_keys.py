"""Per-user LLM credentials, by reference (M4-1, decision D7's second half).

What is stored here is a *reference*, never a key. The secret itself goes
through the same `SecretBroker` every other credential in this product
uses, so a per-user key inherits the keychain/libsecret/env backends and
the fail-closed posture already argued for there — and a database file
copied off this machine carries no usable credential, only the names of
slots it cannot open.

One row per (user, provider). Per-provider rather than one key per user
because a lab that has its own Anthropic account and uses the group's
OpenAI key is the ordinary case, not an exotic one; a single-key design
forces such a user to choose between paying for everything themselves and
paying for nothing.

Absence is the fallback. A user with no row for the active provider runs
on the group key exactly as before, which is what makes this feature
additive rather than a migration (INV-1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from openai4s.storage.migrations import apply_ddl_script

USER_KEY_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_llm_keys (
    user_id    TEXT NOT NULL,
    provider   TEXT NOT NULL,
    secret_ref TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, provider)
);
"""


def create_user_key_schema(conn: sqlite3.Connection) -> None:
    """Idempotent DDL, called from the numbered Store migration."""
    apply_ddl_script(conn, USER_KEY_SCHEMA)


@dataclass(frozen=True)
class UserKeyRecord:
    """That a user has a key for a provider, and nothing about its value."""

    user_id: str
    provider: str
    secret_ref: str
    created_at: int
    updated_at: int

    def public(self) -> dict[str, Any]:
        """What may be shown. Never the ref: it names a keychain slot, and a
        slot name is the one piece of a credential's identity worth keeping
        off a screen and out of a log."""
        return {
            "provider": self.provider,
            "configured": True,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class UserKeyRepository:
    """Per-user credential references over the Store's connection."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: Any,
        clock_ms: Callable[[], int],
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._clock_ms = clock_ms

    def set_ref(self, user_id: str, provider: str, secret_ref: str) -> None:
        now = self._clock_ms()
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_llm_keys"
                " (user_id, provider, secret_ref, created_at, updated_at)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(user_id, provider) DO UPDATE SET"
                " secret_ref=excluded.secret_ref, updated_at=excluded.updated_at",
                (user_id, provider.strip().lower(), secret_ref, now, now),
            )
            self._conn.commit()

    def get(self, user_id: str, provider: str) -> UserKeyRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id, provider, secret_ref, created_at, updated_at"
                " FROM user_llm_keys WHERE user_id=? AND provider=?",
                (user_id, provider.strip().lower()),
            ).fetchone()
        if not row:
            return None
        return UserKeyRecord(
            user_id=row[0],
            provider=row[1],
            secret_ref=row[2],
            created_at=int(row[3]),
            updated_at=int(row[4]),
        )

    def list_for_user(self, user_id: str) -> list[UserKeyRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, provider, secret_ref, created_at, updated_at"
                " FROM user_llm_keys WHERE user_id=? ORDER BY provider",
                (user_id,),
            ).fetchall()
        return [
            UserKeyRecord(
                user_id=r[0],
                provider=r[1],
                secret_ref=r[2],
                created_at=int(r[3]),
                updated_at=int(r[4]),
            )
            for r in rows
        ]

    def delete(self, user_id: str, provider: str, *, secrets: Any = None) -> bool:
        """Clear one key: the broker's value first, then the row.

        The row is a *reference*; deleting only it left the provider key
        persisted in the keychain with nothing left in the product that
        named the slot -- "cleared" reported to the user, credential still
        on disk. The broker value goes first, so a crash between the two
        leaves a row pointing at an empty slot (which the turn refuses with
        `user_key_unreadable`, visibly) rather than a live key with no row
        (which nothing would ever find again).
        """
        record = self.get(user_id, provider)
        if record is not None and secrets is not None:
            _delete_secret(secrets, record.secret_ref)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM user_llm_keys WHERE user_id=? AND provider=?",
                (user_id, provider.strip().lower()),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_all_for_user(self, user_id: str, *, secrets: Any = None) -> int:
        """Every provider at once — what disabling or deleting a user owes.

        A credential that outlives the account it belongs to is a credential
        nobody is watching, and the row is the only thing that would ever
        have named its slot again -- so the broker values go too, before the
        rows that name them.
        """
        if secrets is not None:
            for record in self.list_for_user(user_id):
                _delete_secret(secrets, record.secret_ref)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM user_llm_keys WHERE user_id=?", (user_id,)
            )
            self._conn.commit()
            return cur.rowcount


def _delete_secret(secrets: Any, ref: str) -> None:
    """Best-effort broker deletion. A slot that is already gone, or a broker
    that cannot delete (read-only env backend), must not stop the row from
    being cleared -- the row is what makes the key *usable*, so removing it
    is the part that has to succeed."""
    try:
        secrets.delete(ref)
    except Exception:  # noqa: BLE001
        pass


#: The broker scope per-user keys live under. One scope, distinct names, so
#: a per-user credential can never collide with the group's own slot.
USER_KEY_SCOPE = "llm-user"


def secret_name(user_id: str, provider: str) -> str:
    """The broker slot for one user's key with one provider.

    Joined with `.` rather than `:` because a reference becomes a keychain
    account name and rides in logs, so the broker restricts it to
    `[A-Za-z0-9-_.]` — a colon here is refused at `put`, which surfaces as
    a 503 on a request that had nothing wrong with it.
    """
    return f"{user_id}.{provider.strip().lower()}"


__all__ = [
    "USER_KEY_SCHEMA",
    "USER_KEY_SCOPE",
    "UserKeyRecord",
    "UserKeyRepository",
    "create_user_key_schema",
    "secret_name",
]

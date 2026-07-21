"""Durable registry rows for web shares, on Store's single connection.

The row is the lifecycle authority (``publishing``/``ready``/``failed``/
``revoked``); the on-disk ``current.json`` pointer is the authority for *which*
immutable snapshot a reader serves.  This repository owns only the SQL; the
filesystem two-phase publish and SnapshotLease GC live in
``server/share_service.py``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable


class SharesRepository:
    """Own the ``shares`` table and its small state-machine transitions."""

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

    # ------------------------------------------------------------------ reads
    def get(self, share_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM shares WHERE share_id=?", (share_id,)
            ).fetchone()
        return self._row(row) if row else None

    def active_for_frame(self, root_frame_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM shares WHERE root_frame_id=? "
                "AND status IN ('publishing','ready') "
                "ORDER BY created_at DESC LIMIT 1",
                (root_frame_id,),
            ).fetchone()
        return self._row(row) if row else None

    def list_for_frame(self, root_frame_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM shares WHERE root_frame_id=? "
                "ORDER BY created_at DESC",
                (root_frame_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def list_all(self, *, include_revoked: bool = False) -> list[dict]:
        clause = "" if include_revoked else " WHERE status!='revoked'"
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM shares{clause} ORDER BY created_at DESC"
            ).fetchall()
        return [self._row(row) for row in rows]

    def list_active(self) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM shares WHERE status='ready' ORDER BY created_at"
            ).fetchall()
        return [self._row(row) for row in rows]

    # ------------------------------------------------------------------ writes
    def begin_publish(
        self,
        *,
        share_id: str,
        root_frame_id: str,
        title: str | None,
        pending_snapshot_id: str,
        expires_at: int | None = None,
    ) -> dict:
        """Insert (create) or move an existing row into ``publishing``.

        The partial unique index ``ux_shares_active_frame`` makes a second active
        share for the same frame fail with ``IntegrityError`` — callers translate
        that into a 409 that points at the existing share.
        """

        now = self._clock_ms()
        with self._lock:
            existing = self._connection.execute(
                "SELECT share_id FROM shares WHERE root_frame_id=? "
                "AND status IN ('publishing','ready')",
                (root_frame_id,),
            ).fetchone()
            if existing and existing["share_id"] != share_id:
                raise sqlite3.IntegrityError(
                    f"active share already exists: {existing['share_id']}"
                )
            self._connection.execute(
                "INSERT INTO shares(share_id,root_frame_id,title,status,"
                "pending_snapshot_id,created_at,updated_at,expires_at) "
                "VALUES(?,?,?,'publishing',?,?,?,?) "
                "ON CONFLICT(share_id) DO UPDATE SET "
                "status='publishing', pending_snapshot_id=excluded.pending_snapshot_id, "
                "title=excluded.title, updated_at=excluded.updated_at, "
                "expires_at=excluded.expires_at",
                (
                    share_id,
                    root_frame_id,
                    title,
                    pending_snapshot_id,
                    now,
                    now,
                    expires_at,
                ),
            )
            self._connection.commit()
        return self.get(share_id)  # type: ignore[return-value]

    def list_expired(self, now_ms: int) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM shares WHERE status IN ('publishing','ready') "
                "AND expires_at IS NOT NULL AND expires_at<=?",
                (now_ms,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def mark_ready(
        self,
        share_id: str,
        *,
        snapshot_id: str,
        bundle_sha256: str,
        bundle_size: int,
        projection_id: str,
        counts: dict | None,
    ) -> dict | None:
        now = self._clock_ms()
        self._execute(
            "UPDATE shares SET status='ready', snapshot_id=?, "
            "pending_snapshot_id=NULL, bundle_sha256=?, bundle_size=?, "
            "projection_id=?, counts_json=?, updated_at=? WHERE share_id=?",
            (
                snapshot_id,
                bundle_sha256,
                int(bundle_size),
                projection_id,
                json.dumps(counts or {}),
                now,
                share_id,
            ),
        )
        return self.get(share_id)

    def mark_failed(self, share_id: str) -> None:
        self._execute(
            "UPDATE shares SET status='failed', pending_snapshot_id=NULL, "
            "updated_at=? WHERE share_id=?",
            (self._clock_ms(), share_id),
        )

    def mark_revoked(self, share_id: str) -> None:
        now = self._clock_ms()
        self._execute(
            "UPDATE shares SET status='revoked', pending_snapshot_id=NULL, "
            "revoked_at=?, updated_at=? WHERE share_id=?",
            (now, now, share_id),
        )

    def delete(self, share_id: str) -> None:
        self._execute("DELETE FROM shares WHERE share_id=?", (share_id,))

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        data = dict(row)
        raw = data.get("counts_json")
        try:
            data["counts"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data["counts"] = {}
        return data


__all__ = ["SharesRepository"]

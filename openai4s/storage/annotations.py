"""Image-annotation persistence on a Store-owned SQLite connection."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Callable


class AnnotationRepository:
    """CRUD and status transitions for figure-review annotations.

    The repository shares ``Store``'s connection and re-entrant lock.  In
    particular, ordinal allocation and insertion stay in one critical section
    so concurrent pins cannot receive the same number.
    """

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

    def add(
        self,
        *,
        root_frame_id: str,
        artifact_id: str,
        artifact_name: str | None,
        rel_x: float,
        rel_y: float,
        body: str,
        version_id: str | None = None,
        checksum: str | None = None,
    ) -> dict:
        """Pin a comment to a normalized point on an image artifact.

        ``version_id``/``checksum`` bind the pin to the exact bytes it was taken
        against. Optional because a caller may have no version to name (an
        artifact with no recorded version yet), and because rows written before
        the binding existed must keep loading; the send path treats a missing
        binding as the legacy artifact-latest resolution rather than refusing.
        """
        annotation_id = f"an-{uuid.uuid4().hex[:12]}"
        now = self._clock_ms()
        rel_x = max(0.0, min(1.0, float(rel_x)))
        rel_y = max(0.0, min(1.0, float(rel_y)))
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(number),0) AS n FROM annotations "
                "WHERE root_frame_id=? AND artifact_id=?",
                (root_frame_id, artifact_id),
            ).fetchone()
            number = int(row["n"]) + 1
            self._connection.execute(
                "INSERT INTO annotations(annotation_id,root_frame_id,artifact_id,"
                "artifact_name,rel_x,rel_y,number,body,status,created_at,"
                "updated_at,version_id,checksum) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    annotation_id,
                    root_frame_id,
                    artifact_id,
                    artifact_name,
                    rel_x,
                    rel_y,
                    number,
                    body,
                    "open",
                    now,
                    now,
                    version_id or None,
                    checksum or None,
                ),
            )
            self._connection.commit()
        return self.get(annotation_id)

    def get(self, annotation_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM annotations WHERE annotation_id=?",
                (annotation_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_for_frame(
        self,
        root_frame_id: str,
        *,
        artifact_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM annotations WHERE root_frame_id=?"
        params: list[Any] = [root_frame_id]
        if artifact_id:
            sql += " AND artifact_id=?"
            params.append(artifact_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY artifact_id, number"
        with self._lock:
            rows = self._connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def update(
        self,
        annotation_id: str,
        *,
        body: str | None = None,
        status: str | None = None,
    ) -> dict | None:
        sets: list[str] = []
        params: list[Any] = []
        if body is not None:
            sets.append("body=?")
            params.append(body)
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if not sets:
            return self.get(annotation_id)
        sets.append("updated_at=?")
        params.append(self._clock_ms())
        params.append(annotation_id)
        self._execute(
            f"UPDATE annotations SET {','.join(sets)} WHERE annotation_id=?",
            tuple(params),
        )
        return self.get(annotation_id)

    def mark_sent(self, annotation_ids: list[str]) -> None:
        ids = [
            annotation_id for annotation_id in (annotation_ids or []) if annotation_id
        ]
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self._execute(
            f"UPDATE annotations SET status='sent', updated_at={self._clock_ms()} "
            f"WHERE annotation_id IN ({placeholders}) AND status='open'",
            tuple(ids),
        )

    # ---- reservation: exactly-once admission of a pin into one message ----
    #
    # `mark_sent` alone gives at-most-once and not exactly-once. It filters
    # nothing and dedupes nothing, so an already-`sent` id re-entered a prompt,
    # a duplicated id entered twice, and two concurrent requests both carried
    # the same open pin. Moving it after the submit fixed *when* it runs and
    # not *what* it claims.
    #
    # A reservation is one UPDATE. SQLite applies it atomically, so of two
    # racing requests exactly one transitions a given row out of `open`, and
    # the loser sees it absent from its own reservation rather than silently
    # sharing it.

    def reserve(
        self, *, root_frame_id: str, annotation_ids: list[str], reservation_id: str
    ) -> list[dict]:
        """Claim the still-open pins named, in this frame. Returns what it got.

        Deduplicated by SQL, not by the loop below: `IN (...)` matches a row
        once however many times its id is listed, so a repeated id is carried
        once whether or not this filters it. What the loop is actually for is
        the exact-type check -- an id arrives from a JSON body, so it can be a
        number, a nested object, or a `str` subclass, and none of those should
        reach a query as a parameter.

        Scoped to the frame, so an id belonging to another session cannot be
        dragged into this one's prompt.
        """
        ids: list[str] = []
        seen: set[str] = set()
        for annotation_id in annotation_ids or []:
            if (
                type(annotation_id) is str
                and annotation_id
                and annotation_id not in seen
            ):
                seen.add(annotation_id)
                ids.append(annotation_id)
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with self._lock:
            self._connection.execute(
                f"UPDATE annotations SET status='reserved', reservation_id=?, "
                f"updated_at={self._clock_ms()} "
                f"WHERE root_frame_id=? AND status='open' "
                f"AND annotation_id IN ({placeholders})",
                (reservation_id, root_frame_id, *ids),
            )
            self._connection.commit()
            rows = self._connection.execute(
                "SELECT * FROM annotations WHERE reservation_id=? ORDER BY number",
                (reservation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def release(self, reservation_id: str) -> int:
        """Put this reservation's pins back to `open`. Only ever its own."""
        if not reservation_id:
            return 0
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE annotations SET status='open', reservation_id=NULL, "
                f"updated_at={self._clock_ms()} "
                f"WHERE reservation_id=? AND status='reserved'",
                (reservation_id,),
            )
            self._connection.commit()
            return int(cursor.rowcount or 0)

    def finalize_sent(self, reservation_id: str) -> int:
        """Consume this reservation. One statement, so all-or-none."""
        if not reservation_id:
            return 0
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE annotations SET status='sent', reservation_id=NULL, "
                f"updated_at={self._clock_ms()} "
                f"WHERE reservation_id=? AND status='reserved'",
                (reservation_id,),
            )
            self._connection.commit()
            return int(cursor.rowcount or 0)

    def delete(self, annotation_id: str) -> None:
        self._execute(
            "DELETE FROM annotations WHERE annotation_id=?",
            (annotation_id,),
        )

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = ["AnnotationRepository"]

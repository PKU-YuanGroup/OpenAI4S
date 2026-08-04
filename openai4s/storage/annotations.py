"""Image-annotation persistence on a Store-owned SQLite connection."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Callable


def settle_restored_annotation(row: "Any") -> dict[str, Any]:
    """One annotation coming back from a snapshot, with its holder settled.

    Every restore path shares this rule: package import, and -- since it was
    found missing there -- real checkpoint restore. A snapshot can capture a
    pin mid-flight, `reserved` with a live holder. The request that held it
    does not survive the gap, so the pair cannot be written back verbatim;
    the only state a user can act on is `open` with no holder.

    Lives here rather than in `server/` because `storage/` restores rows too
    and must not import upward -- and because two copies of this rule is two
    chances for a restore path to keep a holder nothing answers for.
    """
    settled = dict(row)
    if settled.get("status") == "reserved":
        settled["status"] = "open"
    settled["reservation_id"] = None
    return settled


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

    def is_reserved(self, annotation_id: str) -> bool:
        """Is this pin held by an in-flight request?

        The public PATCH/DELETE routes are outside the admission path, so
        without this a client could move a `reserved` row to `open` or delete
        it while the turn quoting it was still in flight -- breaking
        exactly-once from a direction the admission code never sees.
        """
        row = self.get(annotation_id)
        return bool(row and row.get("status") == "reserved")

    #: What a *public* caller may move a pin to, and from where.
    #:
    #: `reserved` is absent from every target on purpose: it is entered only by
    #: `reserve`, which sets the id in the same statement. A PATCH that could
    #: write `reserved` would produce a held row with no holder -- invisible in
    #: the composer and released by nothing.
    #: Enumerated from the statuses this product actually writes, not from the
    #: schema comment -- which said `open|sent|resolved` and was already out of
    #: date: `dismissed` is a real one, and omitting it turned a review action
    #: into a 400. A whitelist has to be built from the callers.
    _PUBLIC_STATUSES = frozenset({"open", "sent", "resolved", "dismissed"})

    def update(
        self,
        annotation_id: str,
        *,
        body: str | None = None,
        status: str | None = None,
        expect_status: str | None = None,
    ) -> dict | None:
        """Edit a pin, refusing to race a reservation.

        The check and the write are one statement. Asking
        `annotation_is_reserved()` and *then* updating is a TOCTOU window, and
        it is a real one rather than a theoretical one: `Store` holds a
        re-entrant lock per instance, and the daemon has more than one instance
        against one file. Measured on two connections, a reservation taken in
        that window produced `status='open'` with `reservation_id` still set --
        a pin the composer offers to the user while a turn is quoting it.

        So the expected current status is part of the predicate. A caller that
        loses the race changes nothing and is told so.
        """
        if status is not None and status not in self._PUBLIC_STATUSES:
            raise ValueError(f"not a public annotation status: {status!r}")
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

        # Never touch a held row, whatever the caller asked for, and require
        # the status it believed it was editing when it named one.
        predicate = "annotation_id=? AND reservation_id IS NULL AND status!='reserved'"
        if expect_status is not None:
            predicate += " AND status=?"
            params.append(expect_status)
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE annotations SET {','.join(sets)} WHERE {predicate}",
                tuple(params),
            )
            self._connection.commit()
            if not cursor.rowcount:
                return None
        return self.get(annotation_id)

    def delete_unreserved(self, annotation_id: str) -> bool:
        """Delete a pin only while nothing holds it. One statement, same reason."""
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM annotations WHERE annotation_id=? "
                "AND reservation_id IS NULL AND status!='reserved'",
                (annotation_id,),
            )
            self._connection.commit()
            return bool(cursor.rowcount)

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
                # Scoped by frame as well as by id. The UPDATE above was
                # already frame-scoped, but this read was not -- so the same
                # reservation id used in a second session read *both* frames'
                # rows back, and the caller quoted another session's pins.
                "SELECT * FROM annotations WHERE reservation_id=? "
                "AND root_frame_id=? ORDER BY number",
                (reservation_id, root_frame_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def release(self, reservation_id: str, *, root_frame_id: str | None = None) -> int:
        """Put this reservation's pins back to `open`. Only ever its own.

        Scoped by frame as well as by id. A reservation id travels in a
        response, so it is a value a caller holds -- and keyed on the id alone,
        a request in one session could free a claim held in another.
        """
        if not reservation_id:
            return 0
        scope = " AND root_frame_id=?" if root_frame_id else ""
        params = (reservation_id, root_frame_id) if root_frame_id else (reservation_id,)
        with self._lock:
            # Pins and ledger in one transaction, for the same reason
            # `finalize_sent` does it: a release that commits the rows and then
            # fails to stamp the ledger leaves `reserved` recorded against pins
            # that are already back in the composer, and reconciliation reports
            # an in-flight claim that no longer exists.
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                ledger = self._connection.execute(
                    "SELECT annotation_ids FROM annotation_admissions "
                    f"WHERE reservation_id=?{scope}",
                    params,
                ).fetchone()
                held = {
                    row["annotation_id"]
                    for row in self._connection.execute(
                        "SELECT annotation_id FROM annotations "
                        f"WHERE reservation_id=? AND status='reserved'{scope}",
                        params,
                    ).fetchall()
                }
                if ledger is not None:
                    # All of the set the ledger names, or none of it.
                    #
                    # `rowcount > 0` is not the same question. An admission
                    # naming two pins with only one still held moves one row,
                    # and stamping the whole thing `released` on that publishes
                    # an outcome for a pin this call did not touch -- one that
                    # a live turn may already have sent. Zero, partial and
                    # mismatched are all "somebody else got there first", and
                    # the honest answer to all three is to change nothing and
                    # leave the admission non-terminal.
                    try:
                        expected = set(json.loads(ledger["annotation_ids"] or "[]"))
                    except ValueError:
                        expected = set()
                    if not held or held != expected:
                        self._connection.rollback()
                        return 0
                cursor = self._connection.execute(
                    f"UPDATE annotations SET status='open', reservation_id=NULL, "
                    f"updated_at={self._clock_ms()} "
                    f"WHERE reservation_id=? AND status='reserved'{scope}",
                    params,
                )
                changed = int(cursor.rowcount or 0)
                if ledger is not None:
                    # Unreachable while the SELECT above and this UPDATE share
                    # a predicate inside one transaction, and kept because that
                    # is the assumption rather than a guarantee: the two are
                    # written out separately and a future edit to one is
                    # exactly how they come apart. A mutation removing it stays
                    # green, and no test below claims otherwise.
                    if changed != len(held):
                        self._connection.rollback()
                        return 0
                    # Non-terminal states only, so a `sent` that landed in
                    # between is not overwritten.
                    self._stamp_admission_locked(
                        "released", reservation_id, root_frame_id
                    )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            return changed

    def _stamp_admission_locked(
        self,
        state: str,
        reservation_id: str,
        root_frame_id: str | None,
        *,
        request_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        """The ledger half of a terminal transition. Caller holds the write
        transaction; this never commits.

        `sent` and `released` are written here and only here, alongside the row
        change they describe, which is what makes them evidence: a pin that is
        later resolved, dismissed or deleted does not un-send the message it
        was sent on. Reconciliation reads them as final and derives from the
        rows only for the non-terminal states, where the rows really are the
        authority.
        """
        scope = " AND root_frame_id=?" if root_frame_id else ""
        tail = (reservation_id, root_frame_id) if root_frame_id else (reservation_id,)
        # A compare-and-set on the non-terminal states, never an assignment.
        # `sent` and `released` are what the turn did, and nothing that happens
        # afterwards -- a recovery sweep, a restore, a late release -- gets to
        # rewrite it.
        #
        # Stated here, at the point it has to hold, though today it is a second
        # gate rather than the only one: both callers already refuse unless the
        # exact set the ledger names really moves, and neither can reach this
        # line with a terminal row. That makes it defence rather than
        # behaviour, and no test below claims otherwise -- a mutation removing
        # it stays green, which is the honest reading of "unreachable by
        # construction" and not evidence that it is unnecessary.
        self._connection.execute(
            "UPDATE annotation_admissions SET state=?, updated_at=?, "
            "request_id=COALESCE(?,request_id), job_id=COALESCE(?,job_id) "
            f"WHERE reservation_id=?{scope} AND state IN ('reserved','pending')",
            (state, self._clock_ms(), request_id, job_id, *tail),
        )

    def finalize_sent(
        self,
        reservation_id: str,
        *,
        expected_ids: list[str] | None = None,
        root_frame_id: str | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
    ) -> bool:
        """Consume this reservation, all of it or none of it.

        `expected_ids` is the exact set the prompt quoted. Finalising by
        reservation id alone would consume "whatever is still reserved", which
        is a different set the moment anything else has touched a row -- and it
        would report success having consumed fewer rows than the message
        carried. Checked and applied inside one lock so the count cannot change
        between the two.
        """
        if not reservation_id:
            return False
        scope = " AND root_frame_id=?" if root_frame_id else ""
        params = (reservation_id, root_frame_id) if root_frame_id else (reservation_id,)
        with self._lock:
            # One transaction, not one lock. `Store`'s lock is per instance and
            # the daemon has more than one instance on one file, so a SELECT
            # followed by an UPDATE is not atomic across connections: measured,
            # a row moved in between and this returned False having *already*
            # sent the other one. BEGIN IMMEDIATE takes the write lock before
            # the read, so the set cannot change under it, and a mismatch rolls
            # back to zero rows changed.
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                held = {
                    row["annotation_id"]
                    for row in self._connection.execute(
                        "SELECT annotation_id FROM annotations "
                        f"WHERE reservation_id=? AND status='reserved'{scope}",
                        params,
                    ).fetchall()
                }
                if not held or (expected_ids is not None and held != set(expected_ids)):
                    self._connection.rollback()
                    return False
            except BaseException:
                self._connection.rollback()
                raise
            try:
                cursor = self._connection.execute(
                    f"UPDATE annotations SET status='sent', reservation_id=NULL, "
                    f"updated_at={self._clock_ms()} "
                    f"WHERE reservation_id=? AND status='reserved'{scope}",
                    params,
                )
                changed = int(cursor.rowcount or 0)
                if changed != len(held):
                    self._connection.rollback()
                    return False
                # Same transaction as the rows it describes. Finalising and
                # then stamping the ledger separately is two outcomes: a fault
                # in the gap leaves the pins consumed and the ledger still
                # saying `reserved`, and the client that lost its 202 is told
                # its comments are in flight when the turn already carries
                # them. Correlation rides along so an accepted turn is
                # identifiable from the ledger alone.
                self._stamp_admission_locked(
                    "sent",
                    reservation_id,
                    root_frame_id,
                    request_id=request_id,
                    job_id=job_id,
                )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
            return True

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

"""Durable records for remote compute jobs.

A remote job outlives the process that submitted it. An ssh job keeps running
under ``nohup``; a byoc sandbox keeps billing. ``ComputeManager`` held its jobs
in a plain dict, so a daemon restart stranded every one of them: the work
carried on remotely with nothing left in the app that could find it, harvest it,
or cancel it — and the session's concurrency count reset to zero while the
provider was still busy.

Two tables, because they answer different questions:

* ``compute_jobs`` — where a job is now, and the handles needed to reach it.
* ``compute_job_events`` — append-only and monotonically sequenced, how it got
  there. A status alone cannot distinguish "we never submitted" from "we
  submitted and lost the response before we could record the handle", and those
  two demand opposite actions on restart: retry, or reconcile and do NOT retry.

The idempotency key is what makes that safe. It is recorded *before* the submit
is attempted, so a crash anywhere in the submit path leaves a row to find. On
restart, reconciliation looks the job up by key rather than assuming — which is
the difference between recovering a job and paying for it twice.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from typing import Any, Callable

# Everything a submit needs to reach the job again after a restart.
_FIELDS = (
    "job_id",
    "idempotency_key",
    "provider",
    "status",
    "alias",
    "workdir",
    "pid",
    # The remote *process group* the job actually landed in, read back from the
    # host rather than assumed. `$!` is a pid; in a non-interactive login shell
    # (dash/ash, and bash without job control) it is emphatically not the pgid,
    # so a cancel that signalled `-$!` found no group and reported success over
    # a job that was still running. See ComputeManager._submit_ssh.
    "pgid",
    "sandbox_id",
    # A PROVIDER HANDLE -- a remote pid or a sandbox id (`ComputeManager`:
    # `receipt = sandbox_id or pid`). Not a receipt in `jobs.py`'s sense, where
    # the word means the durable record that lets the next boot name a job this
    # daemon did not start. Two unrelated nouns, one column name; renaming it
    # costs a migration and closes nothing, so the collision is corrected here,
    # in the prose, which is where it actually misleads.
    "receipt",
    "outputs",
    "input_versions",
    # Which session/workspace owns this job. `_rehydrate` filters on it so a
    # restart does not hand one session's live jobs to another. NULL = CLI.
    "owner_key",
    "exit_code",
    "reason",
    # Why a job reached its terminal state, when the status alone would lose
    # something worth keeping — `failed` because outputs could not be verified
    # is a different fact from `failed` because the command exited non-zero.
    "termination_reason",
    # What the harvest actually produced: [{path, size, sha256}], plus one
    # digest over the whole record. Without these a job that declared outputs
    # and produced none of them still reported success.
    "artifact_manifest",
    "integrity_sha256",
    "created_at",
    "updated_at",
    "submitted_at",
    "terminal_at",
)

# Re-exported from the state machine so the SQL that rehydrates jobs and the
# runtime that counts them cannot drift apart again — they used to disagree
# about `staging`, which is how a crashed claim stayed reportable forever
# while holding no slot.
from openai4s.compute.states import (  # noqa: E402
    LIVE_STATES,
    IllegalTransition,
    check_transition,
)

#: How many jobs one page may carry, and the largest a caller may ask for.
#: Remote work accumulates over a long project; the panel is a summary.
MAX_PAGE = 200

#: The filters a page cursor is bound to. `live` narrows the row set to the
#: states that may still be consuming a remote resource; `all` is the record.
PAGE_STATES: tuple[str, ...] = ("all", "live")

#: Bumped when the cursor payload changes shape. A cursor from an older daemon
#: is a malformed cursor, not a position in the new listing.
_CURSOR_VERSION = 1


class ComputeCursorError(ValueError):
    """A page cursor that does not name a position in *this* listing.

    Raised for a payload that cannot be decoded, one minted for a different
    state filter, and one whose anchor job does not belong to the caller's
    owner scope. All three mean "start again from the first page": continuing
    from a position the server cannot vouch for would either skip rows or walk
    another owner's work, and both read as a complete list to the client.
    """


def encode_page_cursor(*, created_at: int, job_id: str, state: str) -> str:
    """The opaque position of one row in a ``(created_at, job_id)`` ordering.

    Opaque on purpose: a client that parses it becomes coupled to the sort key.
    It carries only the sort key and the filter, never ``owner_key`` — that is
    the session's workspace path, which is both a leak and useless as an
    authorization token (the server scopes every query by the authenticated
    owner and checks the anchor row belongs to it).
    """
    raw = json.dumps(
        {
            "v": _CURSOR_VERSION,
            "j": str(job_id),
            "t": int(created_at),
            "s": state,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_page_cursor(value: str, *, state: str) -> tuple[int, str]:
    """Return the keyset anchor, or raise :class:`ComputeCursorError`."""
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable is 400, not page one
        raise ComputeCursorError("cursor is not decodable") from exc
    if not isinstance(payload, dict):
        raise ComputeCursorError("cursor is not an object")
    if payload.get("v") != _CURSOR_VERSION:
        raise ComputeCursorError("cursor version is not supported")
    if payload.get("s") != state:
        raise ComputeCursorError("cursor filter mismatch")
    job_id = payload.get("j")
    created_at = payload.get("t")
    if not isinstance(job_id, str) or not job_id:
        raise ComputeCursorError("cursor names no job")
    if isinstance(created_at, bool) or not isinstance(created_at, int):
        raise ComputeCursorError("cursor names no creation time")
    return created_at, job_id


class ComputeJobRepository:
    """Persist compute job records and their event stream."""

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

    # --- jobs ------------------------------------------------------------
    def create(
        self,
        *,
        job_id: str,
        provider: str,
        status: str = "queued",
        idempotency_key: str | None = None,
        outputs: Any = None,
        input_versions: Any = None,
        owner_key: str | None = None,
    ) -> dict:
        """Record a job before it is submitted.

        Deliberately before: a row written only on success would be absent for
        exactly the failure that matters — the provider accepted the work and
        the response never arrived.
        """
        now = self._clock_ms()
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO compute_jobs(job_id,idempotency_key,provider,"
                    "status,outputs,input_versions,owner_key,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        idempotency_key,
                        provider,
                        status,
                        json.dumps(outputs) if outputs is not None else None,
                        (
                            json.dumps(input_versions)
                            if input_versions is not None
                            else None
                        ),
                        owner_key,
                        now,
                        now,
                    ),
                )
            except Exception:
                # A UNIQUE violation on the idempotency index (a concurrent
                # same-key submit) leaves the deferred transaction open on this
                # shared connection; roll it back so the caller's follow-up read
                # of the winning row is not stranded inside a poisoned txn. The
                # error propagates — the caller decides refuse-vs-degrade.
                self._connection.rollback()
                raise
            self._connection.commit()
        self.append_event(job_id, "created", {"provider": provider})
        return self.get(job_id) or {}

    def update(self, job_id: str, **fields: Any) -> dict | None:
        """Apply one status/field write, atomically with respect to the check.

        The read that validates the transition and the write that performs it
        are one critical section, guarded twice:

          * the store lock spans both, so two threads in this process cannot
            interleave read-check-write;
          * the UPDATE itself is conditional on the status that was read
            (``WHERE status IS ?``), so a writer that somehow got in anyway —
            another connection to the same file — loses instead of silently
            clobbering.

        Without this, a result thread and a cancel thread could both read
        ``running``, both pass ``check_transition``, and then overwrite each
        other's terminal state: ``cancelled`` landing on top of ``succeeded``
        (or the reverse) leaves the ledger claiming an outcome that never
        happened, and drives resource disposal off the losing one.
        """
        allowed = {k: v for k, v in fields.items() if k in _FIELDS and k != "job_id"}
        if not allowed:
            return self.get(job_id)
        for column in ("outputs", "input_versions", "artifact_manifest"):
            if column in allowed and not isinstance(allowed[column], (str, type(None))):
                allowed[column] = json.dumps(allowed[column])
        requested = str(allowed["status"]) if "status" in allowed else None
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM compute_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            current = row[0]
            if requested is not None:
                # The column used to accept any string, so a typo became a
                # state and a terminal job could be quietly re-opened by a
                # late probe.
                check_transition(job_id, current, requested)
            allowed["updated_at"] = self._clock_ms()
            assignments = ",".join(f"{k}=?" for k in allowed)
            if requested is None:
                cursor = self._connection.execute(
                    f"UPDATE compute_jobs SET {assignments} WHERE job_id=?",
                    (*allowed.values(), job_id),
                )
            else:
                cursor = self._connection.execute(
                    f"UPDATE compute_jobs SET {assignments} "
                    f"WHERE job_id=? AND status IS ?",
                    (*allowed.values(), job_id, current),
                )
            self._connection.commit()
            if requested is not None and cursor.rowcount == 0:
                observed = self._connection.execute(
                    "SELECT status FROM compute_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                raise IllegalTransition(
                    job_id,
                    (observed[0] if observed else None) or "<deleted>",
                    requested,
                )
            return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {','.join(_FIELDS)} FROM compute_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return self._decode(row)

    def by_idempotency_key(
        self, key: str, owner_key: str | None = None, *, scoped: bool = True
    ) -> dict | None:
        """The caller's own job for this key, not the installation's.

        This was `WHERE idempotency_key=?` with no owner predicate, while
        `live(scoped=True)` and `for_owner` both scope — and `for_owner`'s own
        docstring calls an optional scope flag "exactly the shape that leaks".
        The consequence was two-sided: the duplicate refusal handed session B
        session A's `job_id` and status, and B could not use a key A happened to
        pick, for work B's own `job_history` and `reconcile` cannot even see.

        `scoped=False` is the deliberate installation-wide read, used by the
        UNIQUE-violation recovery path where the winner may legitimately be
        another owner's row and the caller only needs to know a row exists.
        """
        if not key:
            return None
        sql = f"SELECT {','.join(_FIELDS)} FROM compute_jobs WHERE idempotency_key=?"
        params: tuple = (key,)
        if scoped:
            if owner_key is None:
                # NULL is the CLI / global context, and `= NULL` is never true.
                sql += " AND owner_key IS NULL"
            else:
                sql += " AND owner_key=?"
                params = (key, owner_key)
        with self._lock:
            row = self._connection.execute(sql, params).fetchone()
        return self._decode(row)

    def live(self, owner_key: str | None = None, scoped: bool = False) -> list[dict]:
        """Jobs that may still be consuming a remote resource.

        With ``scoped=True`` the result is restricted to one owner: rows whose
        ``owner_key`` equals ``owner_key`` (``NULL`` when it is None). This is how
        ``_rehydrate`` keeps a restart from handing one session's live jobs — and
        their harvested outputs — to another. ``scoped=False`` (the default) is
        the installation-wide view reconcile and status use.
        """
        placeholders = ",".join("?" for _ in LIVE_STATES)
        sql = (
            f"SELECT {','.join(_FIELDS)} FROM compute_jobs "
            f"WHERE status IN ({placeholders})"
        )
        params: list[Any] = list(LIVE_STATES)
        if scoped:
            if owner_key is None:
                sql += " AND owner_key IS NULL"
            else:
                sql += " AND owner_key=?"
                params.append(owner_key)
        sql += " ORDER BY created_at"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def for_owner(self, owner_key: str | None, limit: int = 200) -> list[dict]:
        """Every job one owner submitted, live and finished, newest first.

        `live()` answers "what might still be costing money", which is what
        rehydration and reconcile need. A person looking at their remote work
        needs the finished ones too -- a job that failed an hour ago is the one
        they came to look at.

        Scoping is unconditional here, unlike `live(scoped=...)`. There is no
        caller for an installation-wide *history* view, and an optional scope
        flag on a listing is exactly the shape that leaks: it defaults to off,
        and the one caller that forgets it enumerates every session's remote
        work -- submitted commands, hosts, harvest manifests. `job_history`
        had that bug and was fixed the same way.
        """
        sql = f"SELECT {','.join(_FIELDS)} FROM compute_jobs WHERE "
        params: list[Any] = []
        if owner_key is None:
            sql += "owner_key IS NULL"
        else:
            sql += "owner_key=?"
            params.append(owner_key)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def page_for_owner(
        self,
        owner_key: str | None,
        *,
        limit: int = MAX_PAGE,
        state: str = "all",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """One bounded page of one owner's jobs, plus that owner's live total.

        `for_owner` returns the newest N and nothing else, so a task older than
        the N most recent rows exists in the record and is invisible on the
        page — including the one that is still running. Two things fix that
        here, and they are one read:

          * a **keyset** position ordered by ``(created_at, job_id)``. The
            original ``ORDER BY created_at DESC LIMIT`` had no tiebreak, so two
            jobs recorded in the same millisecond had no defined order and a
            page boundary between them could repeat one and skip the other.
          * an aggregate over the *same owner scope*, not over the page. The
            count and the rows come from one critical section, so a job
            finishing between them cannot make the response say "1 live" over a
            page that shows none — or the reverse.

        The cursor is bound to the owner by checking that its anchor job is in
        the caller's scope (``owner_key`` cannot be part of the cursor: it is a
        workspace path, and a listing is not an authorization token), and to
        the state filter by its payload. A cursor is `None` for the first page.

        ``state`` is ``"all"`` (the record) or ``"live"`` (states that may still
        be consuming a remote resource). The live total is always over all
        states, which is what makes "3 running elsewhere" sayable on a page
        that shows none of them.
        """
        if state not in PAGE_STATES:
            raise ValueError(f"unknown page state: {state!r}")
        try:
            size = max(1, min(int(limit), MAX_PAGE))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        anchor = decode_page_cursor(cursor, state=state) if cursor else None

        if owner_key is None:
            owner_sql, owner_params = "owner_key IS NULL", []
        else:
            owner_sql, owner_params = "owner_key=?", [owner_key]

        where = [owner_sql]
        params: list[Any] = list(owner_params)
        if state == "live":
            placeholders = ",".join("?" for _ in LIVE_STATES)
            where.append(f"status IN ({placeholders})")
            params.extend(LIVE_STATES)
        if anchor is not None:
            where.append("(created_at < ? OR (created_at = ? AND job_id < ?))")
            params.extend((anchor[0], anchor[0], anchor[1]))

        with self._lock:
            if anchor is not None:
                anchored = self._connection.execute(
                    f"SELECT 1 FROM compute_jobs WHERE job_id=? AND {owner_sql}",
                    (anchor[1], *owner_params),
                ).fetchone()
                if anchored is None:
                    raise ComputeCursorError("cursor does not name this owner's job")
            rows = self._connection.execute(
                f"SELECT {','.join(_FIELDS)} FROM compute_jobs "
                f"WHERE {' AND '.join(where)} "
                f"ORDER BY created_at DESC, job_id DESC LIMIT ?",
                (*params, size + 1),
            ).fetchall()
            live_placeholders = ",".join("?" for _ in LIVE_STATES)
            live_row = self._connection.execute(
                f"SELECT COUNT(*) FROM compute_jobs WHERE {owner_sql} "
                f"AND status IN ({live_placeholders})",
                (*owner_params, *LIVE_STATES),
            ).fetchone()

        has_more = len(rows) > size
        rows = rows[:size]
        tasks = [decoded for decoded in (self._decode(row) for row in rows) if decoded]
        next_cursor = None
        if has_more and tasks:
            last = tasks[-1]
            next_cursor = encode_page_cursor(
                created_at=int(last["created_at"]),
                job_id=str(last["job_id"]),
                state=state,
            )
        return {
            "tasks": tasks,
            "total_live_count": int(live_row[0]) if live_row else 0,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def list(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                f"SELECT {','.join(_FIELDS)} FROM compute_jobs "
                f"ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._decode(row) for row in rows if row is not None]

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM compute_job_events WHERE job_id=?", (job_id,)
            )
            self._connection.execute(
                "DELETE FROM compute_jobs WHERE job_id=?", (job_id,)
            )
            self._connection.commit()

    # --- events ----------------------------------------------------------
    def append_event(self, job_id: str, kind: str, payload: Any = None) -> int:
        """Append one event, allocating the next sequence number.

        The sequence is allocated under the same lock as the insert, so two
        threads recording a transition cannot collide on a number.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM compute_job_events WHERE job_id=?",
                (job_id,),
            ).fetchone()
            seq = int(row[0]) + 1
            self._connection.execute(
                "INSERT INTO compute_job_events(job_id,seq,kind,at,payload) "
                "VALUES(?,?,?,?,?)",
                (
                    job_id,
                    seq,
                    kind,
                    self._clock_ms(),
                    json.dumps(payload) if payload is not None else None,
                ),
            )
            self._connection.commit()
        return seq

    def events(self, job_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT seq,kind,at,payload FROM compute_job_events "
                "WHERE job_id=? ORDER BY seq",
                (job_id,),
            ).fetchall()
        out = []
        for row in rows:
            event = {"seq": row[0], "kind": row[1], "at": row[2]}
            if row[3]:
                try:
                    event["payload"] = json.loads(row[3])
                except (ValueError, TypeError):
                    event["payload"] = row[3]
            out.append(event)
        return out

    @staticmethod
    def _decode(row) -> dict | None:
        if row is None:
            return None
        job = dict(zip(_FIELDS, row))
        for column in ("outputs", "input_versions", "artifact_manifest"):
            if job.get(column):
                try:
                    job[column] = json.loads(job[column])
                except (ValueError, TypeError):
                    pass
        return job


__all__ = [
    "ComputeCursorError",
    "ComputeJobRepository",
    "LIVE_STATES",
    "MAX_PAGE",
    "PAGE_STATES",
    "decode_page_cursor",
    "encode_page_cursor",
]

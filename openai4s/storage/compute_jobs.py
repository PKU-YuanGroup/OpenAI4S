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
    "receipt",
    "outputs",
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
    ) -> dict:
        """Record a job before it is submitted.

        Deliberately before: a row written only on success would be absent for
        exactly the failure that matters — the provider accepted the work and
        the response never arrived.
        """
        now = self._clock_ms()
        with self._lock:
            self._connection.execute(
                "INSERT INTO compute_jobs(job_id,idempotency_key,provider,status,"
                "outputs,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    job_id,
                    idempotency_key,
                    provider,
                    status,
                    json.dumps(outputs) if outputs is not None else None,
                    now,
                    now,
                ),
            )
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
        for column in ("outputs", "artifact_manifest"):
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

    def by_idempotency_key(self, key: str) -> dict | None:
        if not key:
            return None
        with self._lock:
            row = self._connection.execute(
                f"SELECT {','.join(_FIELDS)} FROM compute_jobs "
                f"WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        return self._decode(row)

    def live(self) -> list[dict]:
        """Jobs that may still be consuming a remote resource."""
        placeholders = ",".join("?" for _ in LIVE_STATES)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT {','.join(_FIELDS)} FROM compute_jobs "
                f"WHERE status IN ({placeholders}) ORDER BY created_at",
                LIVE_STATES,
            ).fetchall()
        return [self._decode(row) for row in rows if row is not None]

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
        for column in ("outputs", "artifact_manifest"):
            if job.get(column):
                try:
                    job[column] = json.loads(job[column])
                except (ValueError, TypeError):
                    pass
        return job


__all__ = ["ComputeJobRepository", "LIVE_STATES"]

"""Durable workloads and allocations — the reconciler's WorkloadStore (M3a-8).

Two tables from the plan's appendix A, and one index that is the whole of
INV-3:

    CREATE UNIQUE INDEX ux_active_allocation ON allocations(workload_id)
      WHERE phase IN (...)

That partial index is the enforcement, not the Python check beside it. The
check exists to give a readable error; the index exists because the failure
it prevents happens *between* a check and an insert — a reconciler tick that
dies right there, restarts, and submits a second job holding a second GPU.
One of those two is a race the other cannot have.

`submission_token` is UNIQUE for the same reason: INV-8's reconciliation
asks "does anything carry this token", and an answer is only meaningful if
the token could never have been minted twice.

Rows are converted to and from the orchestration dataclasses here, so the
reconciler never sees a database row and this module never sees a backend.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from openai4s.orchestration.models import (
    Allocation,
    DesiredState,
    ExternalHandle,
    Phase,
    Reason,
    ResourceProfile,
    SubmissionToken,
    Workload,
    WorkloadKind,
    WorkloadSpec,
)

#: The phases that hold a resource. Kept in sync with
#: `Phase.is_active_allocation` by a test rather than by hope — they are the
#: same rule written for two different consumers (SQL and Python).
_ACTIVE_SQL = "'SUBMITTING','PENDING','GRANTED','ACTIVE','RELEASING'"

WORKLOAD_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS workloads (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('SESSION','BATCH')),
    owner_user_id  TEXT NOT NULL,
    project_id     TEXT,
    backend        TEXT NOT NULL DEFAULT 'local',
    spec_json      TEXT NOT NULL,
    spec_revision  INTEGER NOT NULL DEFAULT 1,
    desired_state  TEXT NOT NULL,
    phase          TEXT NOT NULL,
    execution_epoch INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_workloads_owner ON workloads(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_workloads_phase ON workloads(phase);

CREATE TABLE IF NOT EXISTS allocations (
    id               TEXT PRIMARY KEY,
    workload_id      TEXT NOT NULL,
    epoch            INTEGER NOT NULL,
    phase            TEXT NOT NULL,
    external_backend TEXT,
    external_ns      TEXT,
    external_id      TEXT,
    submission_token TEXT UNIQUE NOT NULL,
    reason           TEXT,
    observed_json    TEXT,
    created_at       INTEGER NOT NULL,
    released_at      INTEGER,
    UNIQUE (workload_id, epoch)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_allocation
    ON allocations(workload_id) WHERE phase IN ({_ACTIVE_SQL});
CREATE INDEX IF NOT EXISTS ix_allocations_workload ON allocations(workload_id);
"""


def create_workload_schema(conn: sqlite3.Connection) -> None:
    """Idempotent DDL, called from the numbered Store migration."""
    conn.executescript(WORKLOAD_SCHEMA)


class ActiveAllocationExists(RuntimeError):
    """INV-3, surfaced from the index rather than from a check.

    Raised when the database refuses a second live allocation for one
    workload. A caller sees this only when it lost a race it could not have
    detected — which is precisely why the index is the enforcement.
    """


def _spec_to_json(spec: WorkloadSpec) -> str:
    return json.dumps(
        {
            "kind": spec.kind.value,
            "profile": {
                "name": spec.profile.name,
                "cpus": spec.profile.cpus,
                "memory_mb": spec.profile.memory_mb,
                "gpus": spec.profile.gpus,
                "walltime_s": spec.profile.walltime_s,
                "nodes": spec.profile.nodes,
            },
            "command": list(spec.command),
            "workdir": spec.workdir,
            "environment": dict(spec.environment),
            "spec_revision": spec.spec_revision,
        },
        sort_keys=True,
    )


def _spec_from_json(text: str) -> WorkloadSpec:
    data = json.loads(text)
    profile = data.get("profile") or {}
    return WorkloadSpec(
        kind=WorkloadKind(data["kind"]),
        profile=ResourceProfile(
            name=profile.get("name") or "default",
            cpus=int(profile.get("cpus") or 1),
            memory_mb=int(profile.get("memory_mb") or 4096),
            gpus=int(profile.get("gpus") or 0),
            walltime_s=int(profile.get("walltime_s") or 3600),
            nodes=int(profile.get("nodes") or 1),
        ),
        command=tuple(data.get("command") or ()),
        workdir=data.get("workdir"),
        environment=dict(data.get("environment") or {}),
        spec_revision=int(data.get("spec_revision") or 1),
    )


class WorkloadRepository:
    """Persistence for workloads and allocations, in orchestration types."""

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

    # --- workloads --------------------------------------------------------

    def create_workload(
        self,
        *,
        spec: WorkloadSpec,
        owner_user_id: str,
        project_id: str | None = None,
        backend: str = "local",
    ) -> Workload:
        workload = Workload(
            id=Workload.new_id(),
            spec=spec,
            owner_user_id=owner_user_id,
            project_id=project_id,
        )
        now = self._clock_ms()
        with self._lock:
            self._connection.execute(
                "INSERT INTO workloads(id, kind, owner_user_id, project_id,"
                " backend, spec_json, spec_revision, desired_state, phase,"
                " execution_epoch, reason, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    workload.id,
                    spec.kind.value,
                    owner_user_id,
                    project_id,
                    backend,
                    _spec_to_json(spec),
                    spec.spec_revision,
                    workload.desired_state.value,
                    workload.phase.value,
                    workload.execution_epoch,
                    None,
                    now,
                    now,
                ),
            )
            self._connection.commit()
        setattr(workload, "backend", backend)
        return workload

    def _row_to_workload(self, row: Any) -> Workload:
        workload = Workload(
            id=row["id"],
            spec=_spec_from_json(row["spec_json"]),
            owner_user_id=row["owner_user_id"],
            project_id=row["project_id"],
            desired_state=DesiredState(row["desired_state"]),
            phase=Phase(row["phase"]),
            execution_epoch=int(row["execution_epoch"] or 0),
            reason=Reason(row["reason"]) if row["reason"] else None,
        )
        # Which backend runs it is persistence's business, not the model's.
        setattr(workload, "backend", row["backend"])
        setattr(workload, "created_at", row["created_at"])
        setattr(workload, "updated_at", row["updated_at"])
        return workload

    def get_workload(self, workload_id: str) -> Workload | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workloads WHERE id=?", (workload_id,)
            ).fetchone()
        return self._row_to_workload(row) if row else None

    def list_workloads(
        self,
        *,
        owner_user_id: str | None = None,
        project_id: str | None = None,
        include_terminal: bool = True,
        limit: int = 100,
    ) -> list[Workload]:
        clauses, params = [], []
        if owner_user_id:
            clauses.append("owner_user_id=?")
            params.append(owner_user_id)
        if project_id:
            clauses.append("project_id=?")
            params.append(project_id)
        if not include_terminal:
            clauses.append("phase NOT IN ('COMPLETED','FAILED','CANCELLED','LOST')")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM workloads"
                + where
                + " ORDER BY created_at DESC, id DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._row_to_workload(r) for r in rows]

    def workloads_needing_attention(self) -> list[Workload]:
        """The reconciler's queue: everything not yet terminal."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM workloads WHERE phase NOT IN"
                " ('COMPLETED','FAILED','CANCELLED','LOST')"
                " ORDER BY created_at"
            ).fetchall()
        return [self._row_to_workload(r) for r in rows]

    def save_workload(self, workload: Workload) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE workloads SET desired_state=?, phase=?,"
                " execution_epoch=?, reason=?, updated_at=? WHERE id=?",
                (
                    workload.desired_state.value,
                    workload.phase.value,
                    workload.execution_epoch,
                    workload.reason.value if workload.reason else None,
                    self._clock_ms(),
                    workload.id,
                ),
            )
            self._connection.commit()

    def request_stop(self, workload_id: str, *, reason: Reason) -> bool:
        """Ask for cancellation. The reconciler runs the barrier; this only
        records the desire, so a cancel is durable across a restart."""
        with self._lock:
            cur = self._connection.execute(
                "UPDATE workloads SET desired_state=?, reason=?, updated_at=?"
                " WHERE id=? AND phase NOT IN"
                " ('COMPLETED','FAILED','CANCELLED','LOST')",
                (
                    DesiredState.STOPPED.value,
                    reason.value,
                    self._clock_ms(),
                    workload_id,
                ),
            )
            self._connection.commit()
        return cur.rowcount > 0

    # --- allocations ------------------------------------------------------

    def create_allocation(self, workload_id: str, epoch: int) -> Allocation:
        allocation = Allocation(
            id=Allocation.new_id(),
            workload_id=workload_id,
            epoch=epoch,
            submission_token=SubmissionToken.mint(),
        )
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO allocations(id, workload_id, epoch, phase,"
                    " submission_token, created_at) VALUES(?,?,?,?,?,?)",
                    (
                        allocation.id,
                        workload_id,
                        epoch,
                        allocation.phase.value,
                        allocation.submission_token.value,
                        self._clock_ms(),
                    ),
                )
                self._connection.commit()
            except sqlite3.IntegrityError as exc:
                # The partial unique index refused a second live allocation.
                # This is INV-3 doing its job in the window a check cannot
                # cover.
                raise ActiveAllocationExists(
                    f"workload {workload_id} already has a live allocation"
                ) from exc
        return allocation

    def _row_to_allocation(self, row: Any) -> Allocation:
        handle = None
        if row["external_id"]:
            handle = ExternalHandle(
                backend=row["external_backend"] or "",
                external_id=row["external_id"],
                namespace=row["external_ns"],
            )
        return Allocation(
            id=row["id"],
            workload_id=row["workload_id"],
            epoch=int(row["epoch"] or 0),
            submission_token=SubmissionToken(row["submission_token"]),
            phase=Phase(row["phase"]),
            handle=handle,
            reason=Reason(row["reason"]) if row["reason"] else None,
            diagnostics=json.loads(row["observed_json"] or "{}"),
        )

    def active_allocation(self, workload_id: str) -> Allocation | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM allocations WHERE workload_id=? AND phase IN"
                f" ({_ACTIVE_SQL}) ORDER BY epoch DESC LIMIT 1",
                (workload_id,),
            ).fetchone()
        return self._row_to_allocation(row) if row else None

    def list_allocations(self, workload_id: str) -> list[Allocation]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM allocations WHERE workload_id=? ORDER BY epoch",
                (workload_id,),
            ).fetchall()
        return [self._row_to_allocation(r) for r in rows]

    def save_allocation(self, allocation: Allocation) -> None:
        released = self._clock_ms() if allocation.phase.is_terminal else None
        with self._lock:
            self._connection.execute(
                "UPDATE allocations SET phase=?, external_backend=?,"
                " external_ns=?, external_id=?, reason=?, observed_json=?,"
                " released_at=COALESCE(released_at, ?) WHERE id=?",
                (
                    allocation.phase.value,
                    allocation.handle.backend if allocation.handle else None,
                    allocation.handle.namespace if allocation.handle else None,
                    allocation.handle.external_id if allocation.handle else None,
                    allocation.reason.value if allocation.reason else None,
                    json.dumps(allocation.diagnostics, sort_keys=True),
                    released,
                    allocation.id,
                ),
            )
            self._connection.commit()


__all__ = [
    "ActiveAllocationExists",
    "WORKLOAD_SCHEMA",
    "WorkloadRepository",
    "create_workload_schema",
]

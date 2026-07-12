"""Durable identities and lifecycle records for language-kernel generations.

The in-process integer generation owned by :mod:`openai4s.kernel.supervisor`
remains the cheap ABA guard.  Rows in this repository carry the UUID identity
that survives daemon restarts.  They deliberately describe process lifecycle;
they do not claim that an in-memory namespace can be serialized or restored.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any, Callable, TypedDict

KERNEL_GENERATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS kernel_generations (
    generation_id               TEXT PRIMARY KEY,
    root_frame_id               TEXT NOT NULL,
    branch_id                   TEXT NOT NULL,
    language                    TEXT NOT NULL,
    ordinal                     INTEGER NOT NULL CHECK (ordinal >= 0),
    parent_generation_id        TEXT,
    environment_manifest_id     TEXT,
    bootstrap_manifest_id       TEXT,
    environment_json            TEXT,
    bootstrap_json              TEXT,
    worker_pid                  INTEGER,
    owner_instance_id           TEXT,
    state                       TEXT NOT NULL,
    started_at                  INTEGER NOT NULL,
    last_activity_at            INTEGER NOT NULL,
    ended_at                    INTEGER,
    ended_reason                TEXT,
    recovered_from_generation_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_kernel_generation_ordinal
    ON kernel_generations(root_frame_id, branch_id, language, ordinal);
CREATE INDEX IF NOT EXISTS ix_kernel_generation_root
    ON kernel_generations(root_frame_id, language, ordinal);
CREATE INDEX IF NOT EXISTS ix_kernel_generation_live
    ON kernel_generations(state, ended_at, owner_instance_id);
"""

LIVE_STATES = frozenset({"starting", "bootstrapping", "active", "busy", "recovering"})
TERMINAL_STATES = frozenset(
    {
        "released",
        "manually_stopped",
        "crashed",
        "partial",
        "failed",
        "abandoned",
    }
)
KERNEL_STATES = LIVE_STATES | TERMINAL_STATES


class KernelGenerationDTO(TypedDict):
    generation_id: str
    root_frame_id: str
    branch_id: str
    language: str
    ordinal: int
    parent_generation_id: str | None
    environment_manifest_id: str | None
    bootstrap_manifest_id: str | None
    environment: Any
    bootstrap: Any
    worker_pid: int | None
    owner_instance_id: str | None
    state: str
    started_at: int
    last_activity_at: int
    ended_at: int | None
    ended_reason: str | None
    recovered_from_generation_id: str | None


_UNSET = object()


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _state(value: str) -> str:
    value = _required_text("state", value)
    if value not in KERNEL_STATES:
        raise ValueError(f"unknown kernel generation state: {value!r}")
    return value


def _uuid_text(name: str, value: str | None = None) -> str:
    raw = _required_text(name, value or str(uuid.uuid4()))
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _canonical_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _manifest_id(prefix: str, payload: str | None) -> str | None:
    if payload is None:
        return None
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


class KernelGenerationRepository:
    """Append lifecycle identities and apply monotonic state transitions."""

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
        with self._lock:
            self._connection.executescript(KERNEL_GENERATION_SCHEMA)
            self._connection.commit()

    def create(
        self,
        *,
        root_frame_id: str,
        language: str,
        branch_id: str | None = None,
        generation_id: str | None = None,
        parent_generation_id: str | None = None,
        environment: Any = None,
        bootstrap: Any = None,
        worker_pid: int | None = None,
        owner_instance_id: str | None = None,
        state: str = "active",
        recovered_from_generation_id: str | None = None,
        started_at: int | None = None,
    ) -> KernelGenerationDTO:
        root_frame_id = _required_text("root_frame_id", root_frame_id)
        branch_id = _required_text("branch_id", branch_id or root_frame_id)
        language = _required_text("language", language).lower()
        generation_id = _uuid_text("generation_id", generation_id)
        lifecycle_state = _state(state)
        if lifecycle_state not in LIVE_STATES:
            raise ValueError("a new kernel generation must start in a live state")
        now = self._clock_ms() if started_at is None else int(started_at)
        environment_json = _canonical_json(environment)
        bootstrap_json = _canonical_json(bootstrap)
        with self._lock:
            latest = self._connection.execute(
                "SELECT generation_id,ordinal FROM kernel_generations "
                "WHERE root_frame_id=? AND branch_id=? AND language=? "
                "ORDER BY ordinal DESC LIMIT 1",
                (root_frame_id, branch_id, language),
            ).fetchone()
            ordinal = int(latest["ordinal"]) + 1 if latest is not None else 0
            if parent_generation_id is None and latest is not None:
                parent_generation_id = latest["generation_id"]
            try:
                self._connection.execute(
                    "INSERT INTO kernel_generations("
                    "generation_id,root_frame_id,branch_id,language,ordinal,"
                    "parent_generation_id,environment_manifest_id,"
                    "bootstrap_manifest_id,environment_json,bootstrap_json,"
                    "worker_pid,owner_instance_id,state,started_at,"
                    "last_activity_at,recovered_from_generation_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        generation_id,
                        root_frame_id,
                        branch_id,
                        language,
                        ordinal,
                        parent_generation_id,
                        _manifest_id("env", environment_json),
                        _manifest_id("boot", bootstrap_json),
                        environment_json,
                        bootstrap_json,
                        worker_pid,
                        owner_instance_id,
                        lifecycle_state,
                        now,
                        now,
                        recovered_from_generation_id,
                    ),
                )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
            row = self._row_locked(generation_id)
        return self._normalize(row)

    def touch(
        self,
        generation_id: str,
        *,
        state: str | None = None,
        worker_pid: int | None | object = _UNSET,
        bootstrap: Any = _UNSET,
        at: int | None = None,
    ) -> KernelGenerationDTO:
        generation_id = _required_text("generation_id", generation_id)
        now = self._clock_ms() if at is None else int(at)
        updates = ["last_activity_at=?"]
        values: list[Any] = [now]
        if state is not None:
            lifecycle_state = _state(state)
            if lifecycle_state not in LIVE_STATES:
                raise ValueError("touch can only apply a live generation state")
            updates.append("state=?")
            values.append(lifecycle_state)
        if worker_pid is not _UNSET:
            updates.append("worker_pid=?")
            values.append(worker_pid)
        if bootstrap is not _UNSET:
            bootstrap_json = _canonical_json(bootstrap)
            updates.extend(["bootstrap_json=?", "bootstrap_manifest_id=?"])
            values.extend([bootstrap_json, _manifest_id("boot", bootstrap_json)])
        with self._lock:
            row = self._row_locked(generation_id)
            if row["ended_at"] is not None:
                return self._normalize(row)
            values.append(generation_id)
            self._connection.execute(
                f"UPDATE kernel_generations SET {', '.join(updates)} "
                "WHERE generation_id=? AND ended_at IS NULL",
                tuple(values),
            )
            self._connection.commit()
            row = self._row_locked(generation_id)
        return self._normalize(row)

    def compare_and_swap_bootstrap(
        self,
        generation_id: str,
        *,
        expected_manifest_id: str | None,
        bootstrap: Any,
        at: int | None = None,
    ) -> KernelGenerationDTO | None:
        """Atomically replace one live generation's bootstrap manifest.

        Sidecar imports can extend a manifest after the initial bootstrap Cell.
        The expected content-addressed id prevents two observers from losing an
        import record by overwriting the same prior snapshot.  ``None`` means
        the generation ended or its manifest changed; callers may re-read and
        retry only after revalidating their exact worker lease.
        """

        generation_id = _required_text("generation_id", generation_id)
        now = self._clock_ms() if at is None else int(at)
        bootstrap_json = _canonical_json(bootstrap)
        manifest_id = _manifest_id("boot", bootstrap_json)
        with self._lock:
            row = self._row_locked(generation_id)
            if row["ended_at"] is not None:
                return None
            if row["bootstrap_manifest_id"] != expected_manifest_id:
                return None
            cursor = self._connection.execute(
                "UPDATE kernel_generations SET bootstrap_json=?,"
                "bootstrap_manifest_id=?,last_activity_at=? "
                "WHERE generation_id=? AND ended_at IS NULL "
                "AND bootstrap_manifest_id IS ?",
                (
                    bootstrap_json,
                    manifest_id,
                    now,
                    generation_id,
                    expected_manifest_id,
                ),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                return None
            self._connection.commit()
            row = self._row_locked(generation_id)
        return self._normalize(row)

    def finish(
        self,
        generation_id: str,
        *,
        state: str,
        reason: str,
        ended_at: int | None = None,
    ) -> KernelGenerationDTO:
        generation_id = _required_text("generation_id", generation_id)
        lifecycle_state = _state(state)
        if lifecycle_state not in TERMINAL_STATES:
            raise ValueError("finish requires a terminal kernel state")
        reason = _required_text("reason", reason)
        now = self._clock_ms() if ended_at is None else int(ended_at)
        with self._lock:
            row = self._row_locked(generation_id)
            if row["ended_at"] is None:
                self._connection.execute(
                    "UPDATE kernel_generations SET state=?,last_activity_at=?,"
                    "ended_at=?,ended_reason=? WHERE generation_id=? "
                    "AND ended_at IS NULL",
                    (lifecycle_state, now, now, reason, generation_id),
                )
                self._connection.commit()
                row = self._row_locked(generation_id)
        return self._normalize(row)

    def abandon_live(
        self,
        *,
        owner_instance_id: str,
        reason: str = "daemon_restart",
        ended_at: int | None = None,
    ) -> int:
        """End generations owned by an earlier daemon instance.

        The worker PID is evidence only.  We never infer that a namespace is
        recoverable merely because an orphan process still exists.
        """

        owner_instance_id = _required_text("owner_instance_id", owner_instance_id)
        reason = _required_text("reason", reason)
        now = self._clock_ms() if ended_at is None else int(ended_at)
        placeholders = ",".join("?" for _ in LIVE_STATES)
        params: list[Any] = ["abandoned", now, now, reason]
        params.extend(sorted(LIVE_STATES))
        params.append(owner_instance_id)
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE kernel_generations SET state=?,last_activity_at=?,"
                "ended_at=?,ended_reason=? WHERE ended_at IS NULL "
                f"AND state IN ({placeholders}) "
                "AND (owner_instance_id IS NULL OR owner_instance_id<>?)",
                tuple(params),
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def get(self, generation_id: str) -> KernelGenerationDTO | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM kernel_generations WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
        return self._normalize(row) if row is not None else None

    def latest(
        self,
        root_frame_id: str,
        language: str,
        *,
        branch_id: str | None = None,
    ) -> KernelGenerationDTO | None:
        branch_id = branch_id or root_frame_id
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM kernel_generations WHERE root_frame_id=? "
                "AND branch_id=? AND language=? ORDER BY ordinal DESC LIMIT 1",
                (root_frame_id, branch_id, language.lower()),
            ).fetchone()
        return self._normalize(row) if row is not None else None

    def list(
        self,
        root_frame_id: str,
        *,
        language: str | None = None,
        branch_id: str | None = None,
    ) -> list[KernelGenerationDTO]:
        clauses = ["root_frame_id=?"]
        params: list[Any] = [root_frame_id]
        if branch_id is not None:
            clauses.append("branch_id=?")
            params.append(branch_id)
        if language is not None:
            clauses.append("language=?")
            params.append(language.lower())
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM kernel_generations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY language,ordinal",
                tuple(params),
            ).fetchall()
        return [self._normalize(row) for row in rows]

    def _row_locked(self, generation_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM kernel_generations WHERE generation_id=?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown kernel generation {generation_id!r}")
        return row

    @staticmethod
    def _normalize(row: sqlite3.Row) -> KernelGenerationDTO:
        record = dict(row)
        record["ordinal"] = int(record["ordinal"])
        record["environment"] = _json_load(record.pop("environment_json"))
        record["bootstrap"] = _json_load(record.pop("bootstrap_json"))
        return record  # type: ignore[return-value]


__all__ = [
    "KERNEL_GENERATION_SCHEMA",
    "KERNEL_STATES",
    "LIVE_STATES",
    "TERMINAL_STATES",
    "KernelGenerationDTO",
    "KernelGenerationRepository",
]

"""SQLite persistence for RL trajectory records.

The repository stores the JSON contract plus indexed identity columns. It
never rewrites execution events or rewards. A duplicate ID with identical
payload is an idempotent retry; different bytes are a hard conflict.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Callable, Literal, Mapping

from openai4s.rl.admission import AdmissionRecord, ResourceCapacity
from openai4s.rl.budget import BudgetReservation, BudgetState
from openai4s.rl.models import (
    CostRecord,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    RunReceipt,
    Segment,
    Trajectory,
)
from openai4s.rl.snapshot import DatasetSnapshot

RL_SCHEMA = """
CREATE TABLE IF NOT EXISTS rl_episodes (
    episode_id TEXT PRIMARY KEY, task TEXT NOT NULL, session_id TEXT NOT NULL,
    policy_name TEXT, policy_version TEXT, parent_episode_id TEXT,
    status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_episodes_session ON rl_episodes(session_id, created_at);
CREATE TABLE IF NOT EXISTS rl_observations (
    observation_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL,
    created_at INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_observations_episode ON rl_observations(episode_id, created_at);
CREATE TABLE IF NOT EXISTS rl_decisions (
    decision_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, turn INTEGER NOT NULL,
    action_group_id TEXT, created_at INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_decisions_episode ON rl_decisions(episode_id, turn);
CREATE INDEX IF NOT EXISTS ix_rl_decisions_action_group ON rl_decisions(action_group_id);
CREATE TABLE IF NOT EXISTS rl_segments (
    segment_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, status TEXT NOT NULL,
    created_at INTEGER NOT NULL, parent_segment_id TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_segments_episode ON rl_segments(episode_id, created_at);
CREATE INDEX IF NOT EXISTS ix_rl_segments_parent ON rl_segments(parent_segment_id);
CREATE TABLE IF NOT EXISTS rl_execution_events (
    event_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, sequence INTEGER NOT NULL,
    kind TEXT NOT NULL, created_at INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rl_execution_event_sequence
    ON rl_execution_events(episode_id, sequence);
CREATE INDEX IF NOT EXISTS ix_rl_execution_events_episode
    ON rl_execution_events(episode_id, sequence);
CREATE TABLE IF NOT EXISTS rl_reward_annotations (
    annotation_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, verifier TEXT NOT NULL,
    verifier_version TEXT NOT NULL, decision_id TEXT, segment_id TEXT,
    value REAL NOT NULL, created_at INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_rewards_episode ON rl_reward_annotations(episode_id, created_at);
CREATE TABLE IF NOT EXISTS rl_cost_records (
    cost_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, decision_id TEXT,
    created_at INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_costs_episode ON rl_cost_records(episode_id, created_at);
CREATE TABLE IF NOT EXISTS rl_run_receipts (
    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, episode_id TEXT NOT NULL,
    state TEXT NOT NULL, accepted_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_run_receipts_episode ON rl_run_receipts(episode_id, accepted_at);
"""

RL_SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS rl_dataset_snapshots (
    snapshot_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL,
    schema_version TEXT NOT NULL, trajectory_sha256 TEXT NOT NULL,
    event_sequence_max INTEGER NOT NULL, created_at INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_dataset_snapshots_episode
    ON rl_dataset_snapshots(episode_id, created_at);
"""

RL_BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS rl_budget_ledgers (
    budget_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    limits TEXT NOT NULL,
    modes TEXT NOT NULL,
    used TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rl_budget_allocations (
    budget_id TEXT NOT NULL,
    allocation_id TEXT NOT NULL,
    estimated TEXT NOT NULL,
    reserved_at INTEGER NOT NULL,
    provider_receipt TEXT,
    state TEXT NOT NULL,
    actual TEXT NOT NULL,
    PRIMARY KEY (budget_id, allocation_id),
    FOREIGN KEY (budget_id) REFERENCES rl_budget_ledgers(budget_id)
);
CREATE INDEX IF NOT EXISTS ix_rl_budget_allocations_state
    ON rl_budget_allocations(budget_id, state);
"""

RL_RESOURCE_ADMISSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS rl_resource_admissions (
    admission_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    capacity TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rl_resource_allocations (
    admission_id TEXT NOT NULL,
    allocation_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (admission_id, allocation_id),
    FOREIGN KEY (admission_id) REFERENCES rl_resource_admissions(admission_id)
);
CREATE INDEX IF NOT EXISTS ix_rl_resource_allocations_state
    ON rl_resource_allocations(admission_id);
"""


def create_rl_phase_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS rl_phase_bindings (
        episode_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
        segment_id TEXT, revision INTEGER NOT NULL DEFAULT 1
    )""")


def create_rl_schema(conn: sqlite3.Connection) -> None:
    """Install the RL schema inside the caller's migration transaction."""
    for statement in RL_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def create_rl_snapshot_schema(conn: sqlite3.Connection) -> None:
    """Install only the additive dataset-snapshot tables."""
    for statement in RL_SNAPSHOT_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def create_rl_budget_schema(conn: sqlite3.Connection) -> None:
    """Install the durable budget allocation snapshot tables."""
    for statement in RL_BUDGET_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def create_rl_resource_admission_schema(conn: sqlite3.Connection) -> None:
    """Install the durable resource admission snapshot tables."""
    for statement in RL_RESOURCE_ADMISSION_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _snapshot_from_row(row: sqlite3.Row) -> DatasetSnapshot:
    payload = json.loads(row["payload"])
    if not isinstance(payload, Mapping):
        raise ValueError("stored RL dataset snapshot payload is not an object")
    return DatasetSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        episode_id=str(payload["episode_id"]),
        schema_version=str(payload["schema_version"]),
        trajectory_sha256=str(payload["trajectory_sha256"]),
        event_sequence_max=int(payload["event_sequence_max"]),
        created_at=int(payload["created_at"]),
        record_counts=payload["record_counts"],
    )


def _record_from_row(row: sqlite3.Row, record_type: Any) -> Any:
    payload = json.loads(row["payload"])
    if not isinstance(payload, Mapping):
        raise ValueError("stored RL record payload is not an object")
    return record_type(**payload)


def _payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _same_payload(existing: str, value: Any) -> bool:
    try:
        return existing == _payload(value)
    except (TypeError, ValueError):
        return False


def _episode_from_row(row: sqlite3.Row) -> Episode:
    """Decode the indexed episode projection without exposing SQL rows."""
    metadata = json.loads(row["metadata"])
    if not isinstance(metadata, Mapping):
        raise ValueError("stored RL episode metadata is not an object")
    return Episode(
        episode_id=row["episode_id"],
        task=row["task"],
        session_id=row["session_id"],
        created_at=row["created_at"],
        policy_name=row["policy_name"],
        policy_version=row["policy_version"],
        parent_episode_id=row["parent_episode_id"],
        status=row["status"],
        metadata=metadata,
    )


class RLRepository:
    """Store-owned append-only RL records and idempotent run receipts."""

    def __init__(
        self, connection: sqlite3.Connection, lock: Any, *, clock_ms: Callable[[], int]
    ):
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms

    @property
    def runs(self) -> Any:
        from .rl_runs import RunRepository

        return RunRepository(self)

    def budget_ledger(
        self,
        budget_id: str,
        limits: Mapping[str, float | None],
        *,
        modes: Mapping[str, Literal["hard", "soft"]] | None = None,
    ) -> Any:
        """Return a Store-backed RL budget ledger for one stable budget ID."""
        from openai4s.rl.budget import DurableBudgetLedger

        return DurableBudgetLedger(
            self,
            budget_id,
            limits,
            modes=modes,
            clock_ms=self._clock_ms,
        )

    def resource_admission(
        self,
        admission_id: str,
        capacity: ResourceCapacity,
    ) -> Any:
        """Return a Store-backed resource admission controller."""
        from openai4s.rl.admission import DurableResourceAdmission

        return DurableResourceAdmission(
            self,
            admission_id,
            capacity,
            clock_ms=self._clock_ms,
        )

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def append_episode(self, episode: Episode) -> None:
        data = episode.to_dict()
        with self._transaction():
            current = self._connection.execute(
                "SELECT * FROM rl_episodes WHERE episode_id=?", (episode.episode_id,)
            ).fetchone()
            if current is not None:
                same = all(
                    current[key] == data[key]
                    for key in (
                        "task",
                        "session_id",
                        "policy_name",
                        "policy_version",
                        "parent_episode_id",
                        "status",
                        "created_at",
                    )
                ) and _same_payload(current["metadata"], episode.metadata)
                if not same:
                    raise ValueError(
                        f"episode {episode.episode_id!r} conflicts with stored content"
                    )
                return
            self._connection.execute(
                "INSERT INTO rl_episodes(episode_id,task,session_id,policy_name,policy_version,parent_episode_id,status,created_at,updated_at,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    episode.episode_id,
                    episode.task,
                    episode.session_id,
                    episode.policy_name,
                    episode.policy_version,
                    episode.parent_episode_id,
                    episode.status,
                    episode.created_at,
                    self._clock_ms(),
                    _payload(episode.metadata),
                ),
            )

    def get_episode(self, episode_id: str) -> Episode | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM rl_episodes WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            return _episode_from_row(row) if row is not None else None

    def set_active_phase(
        self, episode_id: str, owner_id: str, segment_id: str | None
    ) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT owner_id,revision FROM rl_phase_bindings WHERE episode_id=?",
                    (episode_id,),
                ).fetchone()
                if row is not None and row["owner_id"] != owner_id:
                    raise ValueError("phase belongs to a different execution owner")
                if segment_id is not None:
                    segment = self.get_segment(segment_id)
                    episode = self.get_episode(episode_id)
                    if (
                        segment is None
                        or segment.episode_id != episode_id
                        or segment.status != "open"
                    ):
                        raise ValueError(
                            "only an open phase in this episode can be resumed"
                        )
                    if episode is None or episode.status != "running":
                        raise ValueError("phase resume requires a running episode")
                    info = segment.metadata.get("openai4s.segment", {})
                    if not info.get("persistent") or info.get("owner_id") != owner_id:
                        raise ValueError("phase belongs to a different execution owner")
                revision = int(row["revision"] + 1) if row is not None else 1
                self._connection.execute(
                    "INSERT INTO rl_phase_bindings(episode_id,owner_id,segment_id,revision) VALUES(?,?,?,?) "
                    "ON CONFLICT(episode_id) DO UPDATE SET owner_id=excluded.owner_id,segment_id=excluded.segment_id,revision=excluded.revision",
                    (episode_id, owner_id, segment_id, revision),
                )
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def active_phase(self, episode_id: str, owner_id: str) -> Segment | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT owner_id,segment_id FROM rl_phase_bindings WHERE episode_id=?",
                (episode_id,),
            ).fetchone()
            if row is None or row["segment_id"] is None:
                return None
            if row["owner_id"] != owner_id:
                raise ValueError("phase belongs to a different execution owner")
            segment = self.get_segment(row["segment_id"])
            return segment if segment is not None and segment.status == "open" else None

    def join_active_phase(
        self, episode_id: str, owner_id: str, decision_id: str
    ) -> str | None:
        """Append one membership per phase/decision, never duplicate execution."""
        with self._lock:
            phase = self.active_phase(episode_id, owner_id)
            leaf = phase.segment_id if phase else None
            while phase is not None:
                event_id = f"{phase.segment_id}:decision:{decision_id}"
                row = self._connection.execute(
                    "SELECT payload FROM rl_execution_events WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if row is None:
                    self.append_execution_event(
                        ExecutionEvent(
                            event_id,
                            episode_id,
                            "segment.decision_joined",
                            self._clock_ms(),
                            decision_id=decision_id,
                            segment_id=phase.segment_id,
                        )
                    )
                phase = (
                    self.get_segment(phase.parent_segment_id)
                    if phase.parent_segment_id
                    else None
                )
            return leaf

    def ensure_episode(self, episode: Episode) -> Episode:
        """Return the durable Episode, creating it atomically when absent.

        Segment boundaries can be opened by separate host calls.  Those calls
        intentionally carry their own wall-clock timestamp, so an existing
        episode must be reused rather than treated as a conflicting rewrite of
        its immutable creation time.  The identity and caller supplied
        metadata still have to agree; the first persisted lifecycle projection
        remains authoritative.
        """
        data = episode.to_dict()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._connection.execute(
                    "SELECT * FROM rl_episodes WHERE episode_id=?",
                    (episode.episode_id,),
                ).fetchone()
                if current is not None:
                    same = all(
                        current[key] == data[key]
                        for key in (
                            "task",
                            "session_id",
                            "policy_name",
                            "policy_version",
                            "parent_episode_id",
                        )
                    ) and _same_payload(current["metadata"], episode.metadata)
                    if not same:
                        raise ValueError(
                            f"episode {episode.episode_id!r} conflicts with stored content"
                        )
                    result = _episode_from_row(current)
                    self._connection.execute("COMMIT")
                    return result
                self._connection.execute(
                    "INSERT INTO rl_episodes(episode_id,task,session_id,policy_name,policy_version,parent_episode_id,status,created_at,updated_at,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        episode.episode_id,
                        episode.task,
                        episode.session_id,
                        episode.policy_name,
                        episode.policy_version,
                        episode.parent_episode_id,
                        episode.status,
                        episode.created_at,
                        self._clock_ms(),
                        _payload(episode.metadata),
                    ),
                )
                self._connection.execute("COMMIT")
                return episode
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def update_episode_status(self, episode_id: str, status: str) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT status FROM rl_episodes WHERE episode_id=?", (episode_id,)
            ).fetchone()
            if row is None:
                self._connection.execute("ROLLBACK")
                raise KeyError(episode_id)
            if row["status"] != "running" and row["status"] != status:
                self._connection.execute("ROLLBACK")
                raise ValueError(f"episode {episode_id!r} is already terminal")
            self._connection.execute(
                "UPDATE rl_episodes SET status=?,updated_at=? WHERE episode_id=?",
                (status, self._clock_ms(), episode_id),
            )
            self._connection.execute("COMMIT")

    def update_segment_status(self, segment_id: str, status: str) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT status,payload FROM rl_segments WHERE segment_id=?",
                (segment_id,),
            ).fetchone()
            if row is None:
                self._connection.execute("ROLLBACK")
                raise KeyError(segment_id)
            if row["status"] != "open" and row["status"] != status:
                self._connection.execute("ROLLBACK")
                raise ValueError(f"segment {segment_id!r} is already terminal")
            payload = json.loads(row["payload"])
            if not isinstance(payload, Mapping):
                self._connection.execute("ROLLBACK")
                raise ValueError(f"segment {segment_id!r} has invalid payload")
            # ``status`` is an indexed lifecycle projection, but the payload is
            # what durable trajectory reload decodes. Keep both projections in
            # lockstep or a closed segment would reappear as open after a
            # restart and make a terminal DatasetSnapshot unverifiable.
            updated_payload = dict(payload)
            updated_payload["status"] = status
            self._connection.execute(
                "UPDATE rl_segments SET status=?,payload=? WHERE segment_id=?",
                (status, _payload(updated_payload), segment_id),
            )
            self._connection.execute("COMMIT")

    def _append(
        self, table: str, key: str, value: Any, columns: Mapping[str, Any]
    ) -> None:
        data = value.to_dict()
        with self._transaction():
            current = self._connection.execute(
                f"SELECT payload FROM {table} WHERE {key}=?", (data[key],)
            ).fetchone()
            if current is not None:
                if not _same_payload(current["payload"], data):
                    raise ValueError(
                        f"record {data[key]!r} conflicts with stored content"
                    )
                return
            names = [key, *columns.keys(), "payload"]
            values = [data[key], *columns.values(), _payload(data)]
            self._connection.execute(
                f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",
                values,
            )

    def append_observation(self, value: Observation) -> None:
        self._append(
            "rl_observations",
            "observation_id",
            value,
            {"episode_id": value.episode_id, "created_at": value.created_at},
        )

    def append_decision(self, value: PolicyDecision) -> None:
        self._append(
            "rl_decisions",
            "decision_id",
            value,
            {
                "episode_id": value.episode_id,
                "turn": value.turn,
                "action_group_id": value.action_group_id,
                "created_at": value.created_at,
            },
        )

    def append_segment(self, value: Segment) -> None:
        if value.parent_segment_id is not None:
            parent = self.get_segment(value.parent_segment_id)
            if parent is None:
                raise ValueError(
                    f"parent segment {value.parent_segment_id!r} does not exist"
                )
            if parent.episode_id != value.episode_id:
                raise ValueError("parent segment belongs to a different episode")
            if parent.segment_id == value.segment_id:
                raise ValueError("segment cannot be its own parent")
        self._append(
            "rl_segments",
            "segment_id",
            value,
            {
                "episode_id": value.episode_id,
                "status": value.status,
                "created_at": value.created_at,
                "parent_segment_id": value.parent_segment_id,
            },
        )

    def get_segment(self, segment_id: str) -> Segment | None:
        """Return one durable segment projection for lifecycle validation."""
        # The Store connection is shared by all rollout lanes.  Reads must
        # use the same lock as lifecycle writes; otherwise a concurrent
        # ``BEGIN IMMEDIATE`` on this connection can interleave with the
        # SELECT and expose an incomplete transaction to a second worker.
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM rl_segments WHERE segment_id=?", (segment_id,)
            ).fetchone()
            if row is None:
                return None
            return _record_from_row(row, Segment)

    def append_execution_event(self, value: ExecutionEvent) -> ExecutionEvent:
        """Allocate and append under one transaction, including exact retries."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                result = self._append_event_unlocked(value)
                self._connection.execute("COMMIT")
                return result
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def _append_event_unlocked(self, value: ExecutionEvent) -> ExecutionEvent:
        """Caller owns both the Store lock and write transaction."""
        current = self._connection.execute(
            "SELECT sequence,payload FROM rl_execution_events WHERE event_id=?",
            (value.event_id,),
        ).fetchone()
        if current is not None:
            if value.sequence is None:
                value = replace(value, sequence=int(current["sequence"]))
            if not _same_payload(current["payload"], value.to_dict()):
                raise ValueError(
                    f"record {value.event_id!r} conflicts with stored content"
                )
            return value
        if value.sequence is None:
            row = self._connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS sequence "
                "FROM rl_execution_events WHERE episode_id=?",
                (value.episode_id,),
            ).fetchone()
            value = replace(value, sequence=int(row["sequence"]))
        self._connection.execute(
            "INSERT INTO rl_execution_events(event_id,episode_id,sequence,kind,created_at,payload) "
            "VALUES(?,?,?,?,?,?)",
            (
                value.event_id,
                value.episode_id,
                value.sequence,
                value.kind,
                value.created_at,
                _payload(value.to_dict()),
            ),
        )
        return value

    def transition_segment(
        self, segment: Segment, event: ExecutionEvent
    ) -> tuple[Segment, ExecutionEvent]:
        """Atomically append a lifecycle fact and update its status projection.

        Timestamp and sequence are allocated facts, not retry input. Everything
        else must match on a retry; the original event is returned unchanged.
        """
        from openai4s.rl.segments import validate_transition

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT payload FROM rl_segments WHERE segment_id=?",
                    (segment.segment_id,),
                ).fetchone()
                current = _record_from_row(row, Segment) if row is not None else None
                previous_row = self._connection.execute(
                    "SELECT payload FROM rl_execution_events WHERE event_id=?",
                    (event.event_id,),
                ).fetchone()
                previous = (
                    _record_from_row(previous_row, ExecutionEvent)
                    if previous_row
                    else None
                )
                validate_transition(current, segment, event, previous)
                if previous is not None:
                    assert current is not None
                    self._connection.execute("COMMIT")
                    return current, previous
                episode = self._connection.execute(
                    "SELECT status FROM rl_episodes WHERE episode_id=?",
                    (segment.episode_id,),
                ).fetchone()
                if episode is None or episode["status"] != "running":
                    raise ValueError("segment lifecycle requires a running episode")
                if segment.parent_segment_id is not None:
                    parent = self._connection.execute(
                        "SELECT episode_id,status FROM rl_segments WHERE segment_id=?",
                        (segment.parent_segment_id,),
                    ).fetchone()
                    if (
                        parent is None
                        or parent["episode_id"] != segment.episode_id
                        or parent["status"] != "open"
                    ):
                        raise ValueError(
                            "parent segment must be open in the same episode"
                        )
                if current is not None and segment.status != "open":
                    child = self._connection.execute(
                        "SELECT 1 FROM rl_segments WHERE parent_segment_id=? AND status='open' LIMIT 1",
                        (segment.segment_id,),
                    ).fetchone()
                    if child is not None:
                        raise ValueError("cannot close a segment with open children")
                if current is None:
                    self._connection.execute(
                        "INSERT INTO rl_segments(segment_id,episode_id,status,created_at,parent_segment_id,payload) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            segment.segment_id,
                            segment.episode_id,
                            segment.status,
                            segment.created_at,
                            segment.parent_segment_id,
                            _payload(segment.to_dict()),
                        ),
                    )
                else:
                    self._connection.execute(
                        "UPDATE rl_segments SET status=?,payload=? WHERE segment_id=?",
                        (
                            segment.status,
                            _payload(segment.to_dict()),
                            segment.segment_id,
                        ),
                    )
                info = segment.metadata.get("openai4s.segment", {})
                if isinstance(info, Mapping) and info.get("persistent"):
                    owner = info.get("owner_id")
                    binding = self._connection.execute(
                        "SELECT * FROM rl_phase_bindings WHERE episode_id=?",
                        (segment.episode_id,),
                    ).fetchone()
                    if not isinstance(owner, str) or not owner:
                        raise ValueError("persistent phase requires an execution owner")
                    if binding is not None and binding["owner_id"] != owner:
                        raise ValueError("phase belongs to a different execution owner")
                    active_id = binding["segment_id"] if binding else None
                    if segment.status == "open":
                        if active_id not in {None, segment.parent_segment_id}:
                            raise ValueError("another phase is active")
                        next_id = segment.segment_id
                    else:
                        if active_id != segment.segment_id:
                            raise ValueError("only the active phase can close")
                        next_id = segment.parent_segment_id
                    self._connection.execute(
                        "INSERT INTO rl_phase_bindings(episode_id,owner_id,segment_id,revision) VALUES(?,?,?,1) "
                        "ON CONFLICT(episode_id) DO UPDATE SET segment_id=excluded.segment_id,revision=revision+1",
                        (segment.episode_id, owner, next_id),
                    )
                stored_event = self._append_event_unlocked(event)
                self._connection.execute("COMMIT")
                return segment, stored_event
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def append_reward(self, value: RewardAnnotation) -> None:
        self._append(
            "rl_reward_annotations",
            "annotation_id",
            value,
            {
                "episode_id": value.episode_id,
                "verifier": value.verifier,
                "verifier_version": value.verifier_version,
                "decision_id": value.decision_id,
                "segment_id": value.segment_id,
                "value": value.value,
                "created_at": value.created_at,
            },
        )

    def append_cost(self, value: CostRecord) -> None:
        self._append(
            "rl_cost_records",
            "cost_id",
            value,
            {
                "episode_id": value.episode_id,
                "decision_id": value.decision_id,
                "created_at": value.created_at,
            },
        )

    def append_snapshot(self, value: DatasetSnapshot) -> None:
        self._append(
            "rl_dataset_snapshots",
            "snapshot_id",
            value,
            {
                "episode_id": value.episode_id,
                "schema_version": value.schema_version,
                "trajectory_sha256": value.trajectory_sha256,
                "event_sequence_max": value.event_sequence_max,
                "created_at": value.created_at,
            },
        )

    def get_snapshot(self, snapshot_id: str) -> DatasetSnapshot:
        row = self._connection.execute(
            "SELECT payload FROM rl_dataset_snapshots WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        return _snapshot_from_row(row)

    def load_trajectory(self, episode_id: str) -> Trajectory:
        """Rebuild one immutable trajectory from one SQLite read snapshot.

        A rollout exporter can run while Host RPCs append facts. Holding the
        Store lock across every projection query prevents an episode from
        combining records from different write generations (for example, an
        event sequence that was read before a segment close with rewards read
        after it).
        """
        with self._lock:
            return self._load_trajectory_unlocked(episode_id)

    def _load_trajectory_unlocked(self, episode_id: str) -> Trajectory:
        """Internal loader; the caller must hold ``self._lock``."""
        episode_row = self._connection.execute(
            "SELECT * FROM rl_episodes WHERE episode_id=?", (episode_id,)
        ).fetchone()
        if episode_row is None:
            raise KeyError(episode_id)
        observations = tuple(
            _record_from_row(row, Observation)
            for row in self._connection.execute(
                "SELECT payload FROM rl_observations WHERE episode_id=? "
                "ORDER BY created_at, observation_id",
                (episode_id,),
            )
        )
        decisions = tuple(
            _record_from_row(row, PolicyDecision)
            for row in self._connection.execute(
                "SELECT payload FROM rl_decisions WHERE episode_id=? "
                "ORDER BY turn, decision_id",
                (episode_id,),
            )
        )
        segments = tuple(
            _record_from_row(row, Segment)
            for row in self._connection.execute(
                "SELECT payload FROM rl_segments WHERE episode_id=? "
                "ORDER BY created_at, segment_id",
                (episode_id,),
            )
        )
        events = tuple(
            _record_from_row(row, ExecutionEvent)
            for row in self._connection.execute(
                "SELECT payload FROM rl_execution_events WHERE episode_id=? "
                "ORDER BY sequence, event_id",
                (episode_id,),
            )
        )
        rewards = tuple(
            _record_from_row(row, RewardAnnotation)
            for row in self._connection.execute(
                "SELECT payload FROM rl_reward_annotations WHERE episode_id=? "
                "ORDER BY created_at, annotation_id",
                (episode_id,),
            )
        )
        costs = tuple(
            _record_from_row(row, CostRecord)
            for row in self._connection.execute(
                "SELECT payload FROM rl_cost_records WHERE episode_id=? "
                "ORDER BY created_at, cost_id",
                (episode_id,),
            )
        )
        trajectory = Trajectory(
            episode=_episode_from_row(episode_row),
            observations=observations,
            decisions=decisions,
            segments=segments,
            execution_events=events,
            rewards=rewards,
            costs=costs,
        )
        # Artifact and Compute repositories remain the source of truth.  The
        # projector adds only exact IDs already referenced by RL events, so a
        # trajectory export can carry durable scientific evidence without
        # copying either raw table or resolving a mutable Artifact head.
        from openai4s.rl.projector import DurableFactProjector

        projected_events = DurableFactProjector(self._connection).project(
            trajectory.episode, trajectory.execution_events
        )
        return Trajectory(
            episode=trajectory.episode,
            observations=trajectory.observations,
            decisions=trajectory.decisions,
            segments=trajectory.segments,
            execution_events=projected_events,
            rewards=trajectory.rewards,
            costs=trajectory.costs,
        )

    def admit_receipt(self, receipt: RunReceipt) -> RunReceipt:
        if receipt.state != "accepted":
            raise ValueError("new run receipts must start in accepted state")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM rl_run_receipts WHERE request_id=?",
                    (receipt.request_id,),
                ).fetchone()
                if row is not None:
                    if (
                        row["fingerprint"] != receipt.fingerprint
                        or row["episode_id"] != receipt.episode_id
                    ):
                        raise ValueError(
                            "run request is already bound to different content"
                        )
                    self._connection.execute("COMMIT")
                    return RunReceipt(**dict(row))
                self._connection.execute(
                    "INSERT INTO rl_run_receipts(request_id,fingerprint,episode_id,state,accepted_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (
                        receipt.request_id,
                        receipt.fingerprint,
                        receipt.episode_id,
                        receipt.state,
                        receipt.accepted_at,
                        receipt.updated_at,
                    ),
                )
                self._connection.execute("COMMIT")
                return receipt
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def update_receipt_state(
        self,
        request_id: str,
        state: str,
        *,
        updated_at: int | None = None,
        expected_state: str | None = None,
    ) -> RunReceipt:
        """Advance one durable rollout receipt without reopening a terminal run.

        ``expected_state`` makes the transition a compare-and-set operation;
        rollout admission uses it to ensure that only one caller claims an
        accepted receipt for execution.
        """
        terminal = {"completed", "failed", "cancelled", "interrupted"}
        allowed = {"accepted", "running", *terminal}
        if not isinstance(state, str) or state not in allowed:
            raise ValueError("unsupported receipt state")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM rl_run_receipts WHERE request_id=?", (request_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(request_id)
                current = RunReceipt(**dict(row))
                if expected_state is not None and current.state != expected_state:
                    raise ValueError(
                        f"run receipt {request_id!r} expected state "
                        f"{expected_state!r}, found {current.state!r}"
                    )
                if current.state in terminal and current.state != state:
                    raise ValueError(f"run receipt {request_id!r} is already terminal")
                if current.state == state:
                    self._connection.execute("COMMIT")
                    return current
                if current.state == "accepted" and state != "running":
                    raise ValueError("accepted run receipts must enter running first")
                if current.state == "running" and state == "accepted":
                    raise ValueError("running run receipts cannot return to accepted")
                timestamp = self._clock_ms() if updated_at is None else updated_at
                if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                    raise ValueError("updated_at must be an integer")
                self._connection.execute(
                    "UPDATE rl_run_receipts SET state=?,updated_at=? "
                    "WHERE request_id=?",
                    (state, timestamp, request_id),
                )
                self._connection.execute("COMMIT")
                return RunReceipt(
                    current.request_id,
                    current.fingerprint,
                    current.episode_id,
                    state,
                    current.accepted_at,
                    timestamp,
                )
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_receipt(self, request_id: str) -> RunReceipt | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM rl_run_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
        return RunReceipt(**dict(row)) if row is not None else None

    def load_budget_ledger(self, budget_id: str) -> dict[str, Any] | None:
        """Load one budget snapshot without exposing SQLite rows."""
        row = self._connection.execute(
            "SELECT budget_id,revision,limits,modes,used FROM rl_budget_ledgers "
            "WHERE budget_id=?",
            (budget_id,),
        ).fetchone()
        if row is None:
            return None
        allocations = self._connection.execute(
            "SELECT allocation_id,estimated,reserved_at,provider_receipt,state,actual "
            "FROM rl_budget_allocations WHERE budget_id=? ORDER BY allocation_id",
            (budget_id,),
        ).fetchall()
        return {
            "budget_id": str(row["budget_id"]),
            "revision": int(row["revision"]),
            "limits": json.loads(row["limits"]),
            "modes": json.loads(row["modes"]),
            "used": json.loads(row["used"]),
            "reservations": [
                {
                    "allocation_id": str(item["allocation_id"]),
                    "estimated": json.loads(item["estimated"]),
                    "reserved_at": int(item["reserved_at"]),
                    "provider_receipt": item["provider_receipt"],
                    "state": str(item["state"]),
                    "actual": json.loads(item["actual"]),
                }
                for item in allocations
            ],
        }

    def save_budget_ledger(
        self,
        budget_id: str,
        state: BudgetState,
        *,
        expected_revision: int | None = None,
    ) -> int:
        """Atomically save a budget snapshot and return its new revision."""
        if not isinstance(budget_id, str) or not budget_id.strip():
            raise ValueError("budget_id must be a non-empty string")
        if not isinstance(state, BudgetState):
            raise TypeError("state must be BudgetState")
        payload = state.to_dict()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT revision FROM rl_budget_ledgers WHERE budget_id=?",
                    (budget_id,),
                ).fetchone()
                if row is None:
                    if expected_revision not in (None, 0):
                        raise ValueError("budget ledger revision conflict")
                    revision = 1
                    self._connection.execute(
                        "INSERT INTO rl_budget_ledgers(budget_id,revision,limits,modes,used,updated_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            budget_id,
                            revision,
                            _payload(payload["limits"]),
                            _payload(payload["modes"]),
                            _payload(payload["used"]),
                            self._clock_ms(),
                        ),
                    )
                else:
                    current = int(row["revision"])
                    if expected_revision is not None and current != expected_revision:
                        raise ValueError("budget ledger revision conflict")
                    revision = current + 1
                    self._connection.execute(
                        "UPDATE rl_budget_ledgers SET revision=?,limits=?,modes=?,used=?,updated_at=? "
                        "WHERE budget_id=? AND revision=?",
                        (
                            revision,
                            _payload(payload["limits"]),
                            _payload(payload["modes"]),
                            _payload(payload["used"]),
                            self._clock_ms(),
                            budget_id,
                            current,
                        ),
                    )
                    if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise ValueError("budget ledger revision conflict")
                self._connection.execute(
                    "DELETE FROM rl_budget_allocations WHERE budget_id=?",
                    (budget_id,),
                )
                for item in state.reservations:
                    if not isinstance(item, BudgetReservation):
                        raise TypeError(
                            "state reservations must be BudgetReservation records"
                        )
                    self._connection.execute(
                        "INSERT INTO rl_budget_allocations(budget_id,allocation_id,estimated,reserved_at,provider_receipt,state,actual) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            budget_id,
                            item.allocation_id,
                            _payload(item.estimated),
                            item.reserved_at,
                            item.provider_receipt,
                            item.state,
                            _payload(item.actual),
                        ),
                    )
                self._connection.execute("COMMIT")
                return revision
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def load_resource_admission(self, admission_id: str) -> dict[str, Any] | None:
        # Both SELECTs must observe the same snapshot. A writer replaces the
        # header and all allocation rows in one transaction; taking the
        # repository lock around both reads prevents a controller from
        # combining a new revision with an older allocation set.
        with self._lock:
            row = self._connection.execute(
                "SELECT admission_id,revision,capacity FROM rl_resource_admissions "
                "WHERE admission_id=?",
                (admission_id,),
            ).fetchone()
            if row is None:
                return None
            rows = self._connection.execute(
                "SELECT payload FROM rl_resource_allocations WHERE admission_id=? "
                "ORDER BY allocation_id",
                (admission_id,),
            ).fetchall()
            return {
                "admission_id": str(row["admission_id"]),
                "revision": int(row["revision"]),
                "capacity": json.loads(row["capacity"]),
                "records": [json.loads(item["payload"]) for item in rows],
            }

    def save_resource_admission(
        self,
        admission_id: str,
        capacity: ResourceCapacity,
        records: tuple[AdmissionRecord, ...],
        *,
        expected_revision: int | None = None,
    ) -> int:
        if not isinstance(admission_id, str) or not admission_id.strip():
            raise ValueError("admission_id must be a non-empty string")
        if not isinstance(capacity, ResourceCapacity):
            raise TypeError("capacity must be ResourceCapacity")
        payload = capacity.__dict__.copy()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT revision FROM rl_resource_admissions WHERE admission_id=?",
                    (admission_id,),
                ).fetchone()
                if row is None:
                    if expected_revision not in (None, 0):
                        raise ValueError("resource admission revision conflict")
                    revision = 1
                    self._connection.execute(
                        "INSERT INTO rl_resource_admissions(admission_id,revision,capacity,updated_at) VALUES(?,?,?,?)",
                        (
                            admission_id,
                            revision,
                            _payload(payload),
                            self._clock_ms(),
                        ),
                    )
                else:
                    current = int(row["revision"])
                    if expected_revision is not None and current != expected_revision:
                        raise ValueError("resource admission revision conflict")
                    revision = current + 1
                    self._connection.execute(
                        "UPDATE rl_resource_admissions SET revision=?,capacity=?,updated_at=? "
                        "WHERE admission_id=? AND revision=?",
                        (
                            revision,
                            _payload(payload),
                            self._clock_ms(),
                            admission_id,
                            current,
                        ),
                    )
                    if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise ValueError("resource admission revision conflict")
                self._connection.execute(
                    "DELETE FROM rl_resource_allocations WHERE admission_id=?",
                    (admission_id,),
                )
                for item in records:
                    if not isinstance(item, AdmissionRecord):
                        raise TypeError("records must contain AdmissionRecord values")
                    self._connection.execute(
                        "INSERT INTO rl_resource_allocations(admission_id,allocation_id,payload) VALUES(?,?,?)",
                        (admission_id, item.allocation_id, _payload(item.to_dict())),
                    )
                self._connection.execute("COMMIT")
                return revision
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                if "rl_resource_admissions.admission_id" in str(error):
                    raise ValueError("resource admission revision conflict") from error
                raise
            except Exception:
                self._connection.execute("ROLLBACK")
                raise


__all__ = [
    "RL_SCHEMA",
    "RLRepository",
    "create_rl_budget_schema",
    "create_rl_resource_admission_schema",
    "create_rl_schema",
]

"""SQLite persistence for RL trajectory records.

The repository stores the JSON contract plus indexed identity columns. It
never rewrites execution events or rewards. A duplicate ID with identical
payload is an idempotent retry; different bytes are a hard conflict.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any, Callable, Mapping

from openai4s.rl.models import (
    CostRecord,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    RunReceipt,
    Segment,
)

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
    created_at INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rl_segments_episode ON rl_segments(episode_id, created_at);
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


def create_rl_schema(conn: sqlite3.Connection) -> None:
    """Install the RL schema inside the caller's migration transaction."""
    for statement in RL_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


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

    def append_episode(self, episode: Episode) -> None:
        data = episode.to_dict()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
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
                    self._connection.execute("ROLLBACK")
                    raise ValueError(
                        f"episode {episode.episode_id!r} conflicts with stored content"
                    )
                self._connection.execute("COMMIT")
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
            self._connection.execute("COMMIT")

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
                            "status",
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
                "SELECT status FROM rl_segments WHERE segment_id=?", (segment_id,)
            ).fetchone()
            if row is None:
                self._connection.execute("ROLLBACK")
                raise KeyError(segment_id)
            if row["status"] != "open" and row["status"] != status:
                self._connection.execute("ROLLBACK")
                raise ValueError(f"segment {segment_id!r} is already terminal")
            self._connection.execute(
                "UPDATE rl_segments SET status=? WHERE segment_id=?",
                (status, segment_id),
            )
            self._connection.execute("COMMIT")

    def _append(
        self, table: str, key: str, value: Any, columns: Mapping[str, Any]
    ) -> None:
        data = value.to_dict()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self._connection.execute(
                f"SELECT payload FROM {table} WHERE {key}=?", (data[key],)
            ).fetchone()
            if current is not None:
                if not _same_payload(current["payload"], data):
                    self._connection.execute("ROLLBACK")
                    raise ValueError(
                        f"record {data[key]!r} conflicts with stored content"
                    )
                self._connection.execute("COMMIT")
                return
            names = [key, *columns.keys(), "payload"]
            values = [data[key], *columns.values(), _payload(data)]
            self._connection.execute(
                f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",
                values,
            )
            self._connection.execute("COMMIT")

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
        self._append(
            "rl_segments",
            "segment_id",
            value,
            {
                "episode_id": value.episode_id,
                "status": value.status,
                "created_at": value.created_at,
            },
        )

    def append_execution_event(self, value: ExecutionEvent) -> ExecutionEvent:
        """Append an event while allocating its episode sequence atomically.

        The sequence is part of the event's durable identity.  Reading the
        current maximum before entering the write transaction lets concurrent
        callers choose the same number, so this method owns the complete
        idempotency check, allocation, and insert transaction.
        """
        data = value.to_dict()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._connection.execute(
                    "SELECT sequence, payload FROM rl_execution_events "
                    "WHERE event_id=?",
                    (value.event_id,),
                ).fetchone()
                if current is not None:
                    # A retry may omit the sequence because the first call
                    # allocated it.  Compare against the stored allocation,
                    # then return the durable value to the caller.
                    if value.sequence is None:
                        data["sequence"] = int(current["sequence"])
                        value = replace(value, sequence=int(current["sequence"]))
                    if not _same_payload(current["payload"], data):
                        raise ValueError(
                            f"record {value.event_id!r} conflicts with stored content"
                        )
                    self._connection.execute("COMMIT")
                    return value

                if value.sequence is None:
                    row = self._connection.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence "
                        "FROM rl_execution_events WHERE episode_id=?",
                        (value.episode_id,),
                    ).fetchone()
                    value = replace(value, sequence=int(row["sequence"]))
                    data = value.to_dict()
                names = [
                    "event_id",
                    "episode_id",
                    "sequence",
                    "kind",
                    "created_at",
                    "payload",
                ]
                self._connection.execute(
                    f"INSERT INTO rl_execution_events({','.join(names)}) "
                    f"VALUES({','.join('?' for _ in names)})",
                    (
                        data["event_id"],
                        data["episode_id"],
                        data["sequence"],
                        data["kind"],
                        data["created_at"],
                        _payload(data),
                    ),
                )
                self._connection.execute("COMMIT")
                return value
            except Exception:
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

    def admit_receipt(self, receipt: RunReceipt) -> RunReceipt:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT * FROM rl_run_receipts WHERE request_id=?",
                (receipt.request_id,),
            ).fetchone()
            if row is not None:
                if (
                    row["fingerprint"] != receipt.fingerprint
                    or row["episode_id"] != receipt.episode_id
                ):
                    self._connection.execute("ROLLBACK")
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

    def get_receipt(self, request_id: str) -> RunReceipt | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM rl_run_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
        return RunReceipt(**dict(row)) if row is not None else None


__all__ = ["RL_SCHEMA", "RLRepository", "create_rl_schema"]

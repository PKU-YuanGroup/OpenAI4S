"""Append-only persistence for the canonical agent action ledger.

The ledger is the durable source from which a reducer can reconstruct provider
history, tool batches, code observations, and execution-attempt state.  It
shares :class:`openai4s.store.Store`'s SQLite connection and re-entrant lock;
the repository never opens a second database connection.

Action groups and events are immutable.  Execution attempts are allocated
before work starts, then only their previously-empty lifecycle milestones may
be filled.  In particular, a finished attempt can never be rewritten.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Callable, TypedDict, cast

ACTION_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_groups (
    group_id           TEXT PRIMARY KEY,
    root_frame_id      TEXT NOT NULL,
    branch_id          TEXT NOT NULL,
    turn_id            TEXT NOT NULL,
    ordinal            INTEGER NOT NULL CHECK (ordinal >= 0),
    kind               TEXT NOT NULL,
    provider           TEXT,
    model              TEXT,
    wire_state         TEXT,
    assistant_content  TEXT,
    assistant_message  TEXT,
    created_at         INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_action_group_ordinal
    ON action_groups(root_frame_id, branch_id, ordinal);
CREATE INDEX IF NOT EXISTS ix_action_group_turn
    ON action_groups(root_frame_id, branch_id, turn_id, ordinal);

CREATE TABLE IF NOT EXISTS action_events (
    event_id             TEXT PRIMARY KEY,
    group_id             TEXT NOT NULL,
    sequence             INTEGER NOT NULL CHECK (sequence >= 0),
    type                 TEXT NOT NULL,
    action_id            TEXT,
    tool_call_id         TEXT,
    wire_id              TEXT,
    canonical_arguments  TEXT,
    raw_arguments        TEXT,
    result               TEXT,
    side_effect_class    TEXT,
    resource_keys        TEXT,
    created_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_action_event_sequence
    ON action_events(group_id, sequence);
CREATE INDEX IF NOT EXISTS ix_action_event_action
    ON action_events(action_id, created_at);
CREATE INDEX IF NOT EXISTS ix_action_event_tool_call
    ON action_events(tool_call_id, created_at);

CREATE TABLE IF NOT EXISTS execution_attempts (
    attempt_id             TEXT PRIMARY KEY,
    group_id               TEXT NOT NULL,
    producing_cell_id      TEXT NOT NULL,
    attempt_ordinal        INTEGER NOT NULL CHECK (attempt_ordinal >= 0),
    state_revision         INTEGER CHECK (state_revision >= 1),
    generation_id          TEXT,
    owner_instance_id      TEXT,
    allocated_at           INTEGER NOT NULL,
    started_at             INTEGER,
    response_at            INTEGER,
    capture_at             INTEGER,
    finished_at            INTEGER,
    terminal_state         TEXT,
    error                  TEXT,
    replayed_from_cell_id  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_execution_attempt_ordinal
    ON execution_attempts(producing_cell_id, attempt_ordinal);
CREATE INDEX IF NOT EXISTS ix_execution_attempt_group
    ON execution_attempts(group_id, attempt_ordinal);
"""


class ActionEventDTO(TypedDict):
    """Stable, JSON-decoded event shape consumed by ledger reducers."""

    event_id: str
    group_id: str
    sequence: int
    type: str
    action_id: str | None
    tool_call_id: str | None
    wire_id: str | None
    canonical_arguments: Any
    raw_arguments: Any
    result: Any
    side_effect_class: str | None
    resource_keys: list[str]
    created_at: int


class ActionGroupDTO(TypedDict):
    """Stable group aggregate; ``events`` is always sequence-ordered."""

    group_id: str
    root_frame_id: str
    branch_id: str
    turn_id: str
    ordinal: int
    kind: str
    provider: str | None
    model: str | None
    wire_state: Any
    assistant_content: str | None
    assistant_message: Any
    created_at: int
    events: list[ActionEventDTO]


class ExecutionAttemptDTO(TypedDict):
    """Stable execution-attempt shape with immutable terminal state."""

    attempt_id: str
    group_id: str
    producing_cell_id: str
    attempt_ordinal: int
    state_revision: int | None
    generation_id: str | None
    owner_instance_id: str | None
    allocated_at: int
    started_at: int | None
    response_at: int | None
    capture_at: int | None
    finished_at: int | None
    terminal_state: str | None
    error: Any
    replayed_from_cell_id: str | None


class AttemptStateError(RuntimeError):
    """Raised when a caller tries to rewrite or regress an attempt."""


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    # Ledger payloads must be losslessly reconstructable.  Do not silently
    # coerce unknown objects with ``default=str``.
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
        # Additive migration may expose a malformed historical value.  Keep it
        # visible to the reducer rather than erasing audit evidence.
        return value


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _ordinal(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


class ActionLedgerRepository:
    """Persist immutable action groups/events and monotonic attempts."""

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
        self._install_schema()

    def _install_schema(self) -> None:
        """Install new tables and apply safe additive ledger migrations."""
        with self._lock:
            self._connection.executescript(ACTION_LEDGER_SCHEMA)
            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(action_groups)"
                ).fetchall()
            }
            if "assistant_message" not in columns:
                self._connection.execute(
                    "ALTER TABLE action_groups ADD COLUMN assistant_message TEXT"
                )
            attempt_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(execution_attempts)"
                ).fetchall()
            }
            if "owner_instance_id" not in attempt_columns:
                self._connection.execute(
                    "ALTER TABLE execution_attempts ADD COLUMN owner_instance_id TEXT"
                )
            if "state_revision" not in attempt_columns:
                self._connection.execute(
                    "ALTER TABLE execution_attempts ADD COLUMN state_revision INTEGER"
                )
            self._connection.commit()

    # --- groups and events -----------------------------------------
    def append_group(
        self,
        *,
        root_frame_id: str,
        turn_id: str,
        kind: str,
        branch_id: str | None = None,
        ordinal: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        wire_state: Any = None,
        assistant_content: str | None = None,
        assistant_message: Any = None,
        group_id: str | None = None,
        created_at: int | None = None,
    ) -> ActionGroupDTO:
        root_frame_id = _required_text("root_frame_id", root_frame_id)
        branch_id = _required_text("branch_id", branch_id or root_frame_id)
        turn_id = _required_text("turn_id", turn_id)
        kind = _required_text("kind", kind)
        group_id = _required_text(
            "group_id", group_id or f"ag-{uuid.uuid4().hex[:16]}"
        )
        now = self._clock_ms() if created_at is None else created_at
        with self._lock:
            if ordinal is None:
                ordinal = self._next_group_ordinal_locked(root_frame_id, branch_id)
            else:
                ordinal = _ordinal("ordinal", ordinal)
            try:
                self._insert_group_locked(
                    group_id=group_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    turn_id=turn_id,
                    ordinal=ordinal,
                    kind=kind,
                    provider=provider,
                    model=model,
                    wire_state=wire_state,
                    assistant_content=assistant_content,
                    assistant_message=assistant_message,
                    created_at=now,
                )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
            row = self._group_row_locked(group_id)
        return self._normalize_group(row, events=[])

    def append_event(
        self,
        *,
        group_id: str,
        type: str,
        sequence: int | None = None,
        action_id: str | None = None,
        tool_call_id: str | None = None,
        wire_id: str | None = None,
        canonical_arguments: Any = None,
        raw_arguments: Any = None,
        result: Any = None,
        side_effect_class: str | None = None,
        resource_keys: list[str] | tuple[str, ...] | None = None,
        event_id: str | None = None,
        created_at: int | None = None,
    ) -> ActionEventDTO:
        group_id = _required_text("group_id", group_id)
        event_type = _required_text("type", type)
        event_id = _required_text(
            "event_id", event_id or f"ae-{uuid.uuid4().hex[:16]}"
        )
        now = self._clock_ms() if created_at is None else created_at
        with self._lock:
            self._require_group_locked(group_id)
            if sequence is None:
                sequence = self._next_event_sequence_locked(group_id)
            else:
                sequence = _ordinal("sequence", sequence)
            try:
                self._insert_event_locked(
                    event_id=event_id,
                    group_id=group_id,
                    sequence=sequence,
                    type=event_type,
                    action_id=action_id,
                    tool_call_id=tool_call_id,
                    wire_id=wire_id,
                    canonical_arguments=canonical_arguments,
                    raw_arguments=raw_arguments,
                    result=result,
                    side_effect_class=side_effect_class,
                    resource_keys=resource_keys,
                    created_at=now,
                )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM action_events WHERE event_id=?", (event_id,)
            ).fetchone()
        return self._normalize_event(row)

    def append_tool_group(
        self,
        *,
        root_frame_id: str,
        turn_id: str,
        events: list[dict[str, Any]],
        branch_id: str | None = None,
        ordinal: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        wire_state: Any = None,
        assistant_content: str | None = None,
        assistant_message: Any = None,
        group_id: str | None = None,
        created_at: int | None = None,
    ) -> ActionGroupDTO:
        """Append one provider tool declaration and all events atomically.

        A duplicate event sequence, malformed payload, or any other failure
        rolls back the group too, so a reducer never observes half a native
        tool batch from this boundary.
        """
        if not isinstance(events, list) or not events:
            raise ValueError("events must be a non-empty list")
        root_frame_id = _required_text("root_frame_id", root_frame_id)
        branch_id = _required_text("branch_id", branch_id or root_frame_id)
        turn_id = _required_text("turn_id", turn_id)
        group_id = _required_text(
            "group_id", group_id or f"ag-{uuid.uuid4().hex[:16]}"
        )
        now = self._clock_ms() if created_at is None else created_at
        normalized_events: list[dict[str, Any]] = []
        for index, source in enumerate(events):
            if not isinstance(source, dict):
                raise TypeError("every tool event must be a dict")
            event = dict(source)
            event["sequence"] = _ordinal(
                "sequence", event.get("sequence", index)
            )
            event["type"] = _required_text("type", event.get("type", ""))
            event["event_id"] = _required_text(
                "event_id", event.get("event_id") or f"ae-{uuid.uuid4().hex[:16]}"
            )
            event["created_at"] = event.get("created_at", now)
            normalized_events.append(event)

        with self._lock:
            if ordinal is None:
                ordinal = self._next_group_ordinal_locked(root_frame_id, branch_id)
            else:
                ordinal = _ordinal("ordinal", ordinal)
            try:
                self._insert_group_locked(
                    group_id=group_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    turn_id=turn_id,
                    ordinal=ordinal,
                    kind="native_tools",
                    provider=provider,
                    model=model,
                    wire_state=wire_state,
                    assistant_content=assistant_content,
                    assistant_message=assistant_message,
                    created_at=now,
                )
                for event in normalized_events:
                    self._insert_event_locked(
                        event_id=event["event_id"],
                        group_id=group_id,
                        sequence=event["sequence"],
                        type=event["type"],
                        action_id=event.get("action_id"),
                        tool_call_id=event.get("tool_call_id"),
                        wire_id=event.get("wire_id"),
                        canonical_arguments=event.get("canonical_arguments"),
                        raw_arguments=event.get("raw_arguments"),
                        result=event.get("result"),
                        side_effect_class=event.get("side_effect_class"),
                        resource_keys=event.get("resource_keys"),
                        created_at=event["created_at"],
                    )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
            group_row = self._group_row_locked(group_id)
            event_rows = self._connection.execute(
                "SELECT * FROM action_events WHERE group_id=? ORDER BY sequence",
                (group_id,),
            ).fetchall()
        return self._normalize_group(
            group_row,
            events=[self._normalize_event(row) for row in event_rows],
        )

    def get_group(
        self, group_id: str, *, include_events: bool = True
    ) -> ActionGroupDTO | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM action_groups WHERE group_id=?", (group_id,)
            ).fetchone()
            if row is None:
                return None
            event_rows = (
                self._connection.execute(
                    "SELECT * FROM action_events WHERE group_id=? ORDER BY sequence",
                    (group_id,),
                ).fetchall()
                if include_events
                else []
            )
        return self._normalize_group(
            row,
            events=[self._normalize_event(event) for event in event_rows],
        )

    def list_groups(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        turn_id: str | None = None,
        after_ordinal: int | None = None,
        limit: int | None = None,
        include_events: bool = True,
    ) -> list[ActionGroupDTO]:
        """Read the canonical branch sequence for a reducer.

        ``ordinal`` is branch-monotonic (not merely turn-local).  Omitting
        ``branch_id`` selects the canonical main branch whose id is the root
        frame id.
        """
        root_frame_id = _required_text("root_frame_id", root_frame_id)
        branch_id = _required_text("branch_id", branch_id or root_frame_id)
        clauses = ["root_frame_id=?", "branch_id=?"]
        params: list[Any] = [root_frame_id, branch_id]
        if turn_id is not None:
            clauses.append("turn_id=?")
            params.append(_required_text("turn_id", turn_id))
        if after_ordinal is not None:
            clauses.append("ordinal>?")
            params.append(_ordinal("after_ordinal", after_ordinal))
        sql = (
            "SELECT * FROM action_groups WHERE "
            + " AND ".join(clauses)
            + " ORDER BY ordinal"
        )
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError("limit must be a positive integer")
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
            events_by_group: dict[str, list[ActionEventDTO]] = {
                row["group_id"]: [] for row in rows
            }
            if include_events and rows:
                placeholders = ",".join("?" for _ in rows)
                event_rows = self._connection.execute(
                    "SELECT * FROM action_events WHERE group_id IN ("
                    + placeholders
                    + ") ORDER BY group_id,sequence",
                    tuple(row["group_id"] for row in rows),
                ).fetchall()
                for event_row in event_rows:
                    events_by_group[event_row["group_id"]].append(
                        self._normalize_event(event_row)
                    )
        return [
            self._normalize_group(row, events=events_by_group[row["group_id"]])
            for row in rows
        ]

    def list_events(self, group_id: str) -> list[ActionEventDTO]:
        group_id = _required_text("group_id", group_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM action_events WHERE group_id=? ORDER BY sequence",
                (group_id,),
            ).fetchall()
        return [self._normalize_event(row) for row in rows]

    # --- execution attempts ---------------------------------------
    def allocate_attempt(
        self,
        *,
        group_id: str,
        producing_cell_id: str,
        state_revision: int | None = None,
        generation_id: str | None = None,
        owner_instance_id: str | None = None,
        replayed_from_cell_id: str | None = None,
        attempt_ordinal: int | None = None,
        attempt_id: str | None = None,
        allocated_at: int | None = None,
    ) -> ExecutionAttemptDTO:
        group_id = _required_text("group_id", group_id)
        producing_cell_id = _required_text("producing_cell_id", producing_cell_id)
        attempt_id = _required_text(
            "attempt_id", attempt_id or f"xa-{uuid.uuid4().hex[:16]}"
        )
        now = self._clock_ms() if allocated_at is None else allocated_at
        if state_revision is not None:
            state_revision = _ordinal("state_revision", state_revision)
            if state_revision == 0:
                raise ValueError("state_revision must be positive")
        with self._lock:
            self._require_group_locked(group_id)
            if attempt_ordinal is None:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(attempt_ordinal),-1)+1 AS n "
                    "FROM execution_attempts WHERE producing_cell_id=?",
                    (producing_cell_id,),
                ).fetchone()
                attempt_ordinal = int(row["n"])
            else:
                attempt_ordinal = _ordinal("attempt_ordinal", attempt_ordinal)
            try:
                self._connection.execute(
                    "INSERT INTO execution_attempts("
                    "attempt_id,group_id,producing_cell_id,attempt_ordinal,"
                    "state_revision,generation_id,owner_instance_id,allocated_at,"
                    "replayed_from_cell_id) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        attempt_id,
                        group_id,
                        producing_cell_id,
                        attempt_ordinal,
                        state_revision,
                        generation_id,
                        owner_instance_id,
                        now,
                        replayed_from_cell_id,
                    ),
                )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
            row = self._attempt_row_locked(attempt_id)
        return self._normalize_attempt(row)

    def abandon_incomplete_attempts(
        self,
        *,
        owner_instance_id: str,
        finished_at: int | None = None,
    ) -> int:
        """Terminalize unfinished attempts allocated by an older daemon."""

        owner_instance_id = _required_text("owner_instance_id", owner_instance_id)
        now = self._clock_ms() if finished_at is None else finished_at
        error = _json_dump(
            {
                "type": "daemon_restart",
                "message": "execution interrupted by daemon restart",
            }
        )
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE execution_attempts SET finished_at=?,"
                "terminal_state='abandoned',error=? WHERE finished_at IS NULL "
                "AND terminal_state IS NULL AND (owner_instance_id IS NULL "
                "OR owner_instance_id<>?)",
                (now, error, owner_instance_id),
            )
            self._connection.commit()
            return int(cursor.rowcount)

    def mark_attempt_started(
        self, attempt_id: str, *, started_at: int | None = None
    ) -> ExecutionAttemptDTO:
        return self._mark_attempt_milestone(
            attempt_id,
            column="started_at",
            at=started_at,
            prerequisite=None,
        )

    def bind_attempt_generation(
        self, attempt_id: str, generation_id: str
    ) -> ExecutionAttemptDTO:
        """Bind the worker UUID after lazy language preparation.

        Attempts are allocated before a worker is acquired, so their generation
        starts nullable.  Once set, the identity is immutable: an ABA-safe retry
        receives a new attempt rather than rewriting the old one.
        """

        attempt_id = _required_text("attempt_id", attempt_id)
        generation_id = _required_text("generation_id", generation_id)
        with self._lock:
            row = self._attempt_row_locked(attempt_id)
            current = row["generation_id"]
            if current is not None and current != generation_id:
                raise AttemptStateError(
                    f"attempt {attempt_id!r} is already bound to generation "
                    f"{current!r}"
                )
            if current is None:
                self._connection.execute(
                    "UPDATE execution_attempts SET generation_id=? "
                    "WHERE attempt_id=? AND generation_id IS NULL",
                    (generation_id, attempt_id),
                )
                self._connection.commit()
                row = self._attempt_row_locked(attempt_id)
        return self._normalize_attempt(row)

    def mark_attempt_response(
        self, attempt_id: str, *, response_at: int | None = None
    ) -> ExecutionAttemptDTO:
        return self._mark_attempt_milestone(
            attempt_id,
            column="response_at",
            at=response_at,
            prerequisite="started_at",
        )

    def mark_attempt_capture(
        self, attempt_id: str, *, capture_at: int | None = None
    ) -> ExecutionAttemptDTO:
        return self._mark_attempt_milestone(
            attempt_id,
            column="capture_at",
            at=capture_at,
            prerequisite="response_at",
        )

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        terminal_state: str,
        error: Any = None,
        finished_at: int | None = None,
    ) -> ExecutionAttemptDTO:
        attempt_id = _required_text("attempt_id", attempt_id)
        terminal_state = _required_text("terminal_state", terminal_state)
        if terminal_state in {"allocated", "started", "responded", "captured"}:
            raise ValueError("terminal_state must describe a terminal outcome")
        now = self._clock_ms() if finished_at is None else finished_at
        with self._lock:
            row = self._attempt_row_locked(attempt_id)
            if row["terminal_state"] is not None or row["finished_at"] is not None:
                raise AttemptStateError(
                    f"execution attempt {attempt_id!r} is already finished"
                )
            self._validate_timestamp(row, "finished_at", now)
            if terminal_state in {"completed", "succeeded", "ok"} and row[
                "capture_at"
            ] is None:
                raise AttemptStateError(
                    "a successful execution attempt must finish artifact capture first"
                )
            cursor = self._connection.execute(
                "UPDATE execution_attempts SET finished_at=?,terminal_state=?,"
                "error=? WHERE attempt_id=? AND finished_at IS NULL "
                "AND terminal_state IS NULL",
                (now, terminal_state, _json_dump(error), attempt_id),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                raise AttemptStateError(
                    f"execution attempt {attempt_id!r} cannot be finished twice"
                )
            self._connection.commit()
            row = self._attempt_row_locked(attempt_id)
        return self._normalize_attempt(row)

    def get_attempt(self, attempt_id: str) -> ExecutionAttemptDTO | None:
        attempt_id = _required_text("attempt_id", attempt_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM execution_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        return self._normalize_attempt(row) if row is not None else None

    def list_attempts(
        self,
        *,
        group_id: str | None = None,
        producing_cell_id: str | None = None,
        root_frame_id: str | None = None,
        branch_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[ExecutionAttemptDTO]:
        clauses: list[str] = []
        params: list[Any] = []
        join = ""
        if root_frame_id is not None:
            root_frame_id = _required_text("root_frame_id", root_frame_id)
            branch_id = _required_text("branch_id", branch_id or root_frame_id)
            join = " JOIN action_groups AS g ON g.group_id=a.group_id"
            clauses.extend(["g.root_frame_id=?", "g.branch_id=?"])
            params.extend([root_frame_id, branch_id])
            if turn_id is not None:
                clauses.append("g.turn_id=?")
                params.append(_required_text("turn_id", turn_id))
        elif branch_id is not None or turn_id is not None:
            raise ValueError("branch_id/turn_id require root_frame_id")
        if group_id is not None:
            clauses.append("a.group_id=?")
            params.append(_required_text("group_id", group_id))
        if producing_cell_id is not None:
            clauses.append("a.producing_cell_id=?")
            params.append(_required_text("producing_cell_id", producing_cell_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = (
            " ORDER BY g.ordinal,a.attempt_ordinal"
            if join
            else " ORDER BY a.allocated_at,a.producing_cell_id,a.attempt_ordinal"
        )
        with self._lock:
            rows = self._connection.execute(
                "SELECT a.* FROM execution_attempts AS a" + join + where + order,
                params,
            ).fetchall()
        return [self._normalize_attempt(row) for row in rows]

    # --- internals -------------------------------------------------
    def _mark_attempt_milestone(
        self,
        attempt_id: str,
        *,
        column: str,
        at: int | None,
        prerequisite: str | None,
    ) -> ExecutionAttemptDTO:
        attempt_id = _required_text("attempt_id", attempt_id)
        now = self._clock_ms() if at is None else at
        with self._lock:
            row = self._attempt_row_locked(attempt_id)
            if row["terminal_state"] is not None:
                raise AttemptStateError(
                    f"execution attempt {attempt_id!r} is already finished"
                )
            if row[column] is not None:
                # Retry-safe without allowing the original timestamp to change.
                return self._normalize_attempt(row)
            if prerequisite is not None and row[prerequisite] is None:
                raise AttemptStateError(
                    f"cannot set {column} before {prerequisite}"
                )
            self._validate_timestamp(row, column, now)
            cursor = self._connection.execute(
                f"UPDATE execution_attempts SET {column}=? "
                f"WHERE attempt_id=? AND {column} IS NULL "
                "AND terminal_state IS NULL",
                (now, attempt_id),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                raise AttemptStateError(
                    f"execution attempt {attempt_id!r} milestone raced"
                )
            self._connection.commit()
            row = self._attempt_row_locked(attempt_id)
        return self._normalize_attempt(row)

    @staticmethod
    def _validate_timestamp(row: Any, column: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{column} must be a non-negative integer")
        order = ["allocated_at", "started_at", "response_at", "capture_at"]
        preceding = order if column == "finished_at" else order[: order.index(column)]
        latest = max(
            (row[name] for name in preceding if row[name] is not None), default=0
        )
        if value < latest:
            raise ValueError(f"{column} cannot precede an earlier milestone")

    def _insert_group_locked(self, **values: Any) -> None:
        self._connection.execute(
            "INSERT INTO action_groups("
            "group_id,root_frame_id,branch_id,turn_id,ordinal,kind,provider,model,"
            "wire_state,assistant_content,assistant_message,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                values["group_id"],
                values["root_frame_id"],
                values["branch_id"],
                values["turn_id"],
                values["ordinal"],
                values["kind"],
                values["provider"],
                values["model"],
                _json_dump(values["wire_state"]),
                values["assistant_content"],
                _json_dump(values["assistant_message"]),
                values["created_at"],
            ),
        )

    def _insert_event_locked(self, **values: Any) -> None:
        resource_keys = values["resource_keys"]
        if resource_keys is not None and not isinstance(resource_keys, (list, tuple)):
            raise TypeError("resource_keys must be a list or tuple")
        self._connection.execute(
            "INSERT INTO action_events("
            "event_id,group_id,sequence,type,action_id,tool_call_id,wire_id,"
            "canonical_arguments,raw_arguments,result,side_effect_class,"
            "resource_keys,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                values["event_id"],
                values["group_id"],
                values["sequence"],
                values["type"],
                values["action_id"],
                values["tool_call_id"],
                values["wire_id"],
                _json_dump(values["canonical_arguments"]),
                _json_dump(values["raw_arguments"]),
                _json_dump(values["result"]),
                values["side_effect_class"],
                _json_dump(list(resource_keys or [])),
                values["created_at"],
            ),
        )

    def _next_group_ordinal_locked(self, root_frame_id: str, branch_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(ordinal),-1)+1 AS n FROM action_groups "
            "WHERE root_frame_id=? AND branch_id=?",
            (root_frame_id, branch_id),
        ).fetchone()
        return int(row["n"])

    def _next_event_sequence_locked(self, group_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence),-1)+1 AS n FROM action_events "
            "WHERE group_id=?",
            (group_id,),
        ).fetchone()
        return int(row["n"])

    def _require_group_locked(self, group_id: str) -> None:
        if self._connection.execute(
            "SELECT 1 FROM action_groups WHERE group_id=?", (group_id,)
        ).fetchone() is None:
            raise KeyError(f"unknown action group {group_id!r}")

    def _group_row_locked(self, group_id: str) -> Any:
        row = self._connection.execute(
            "SELECT * FROM action_groups WHERE group_id=?", (group_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown action group {group_id!r}")
        return row

    def _attempt_row_locked(self, attempt_id: str) -> Any:
        row = self._connection.execute(
            "SELECT * FROM execution_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown execution attempt {attempt_id!r}")
        return row

    @staticmethod
    def _normalize_event(row: Any) -> ActionEventDTO:
        data = dict(row)
        for key in ("canonical_arguments", "raw_arguments", "result"):
            data[key] = _json_load(data.get(key))
        resource_keys = _json_load(data.get("resource_keys"))
        data["resource_keys"] = resource_keys if isinstance(resource_keys, list) else []
        return cast(ActionEventDTO, data)

    @staticmethod
    def _normalize_group(
        row: Any, *, events: list[ActionEventDTO]
    ) -> ActionGroupDTO:
        data = dict(row)
        data["wire_state"] = _json_load(data.get("wire_state"))
        data["assistant_message"] = _json_load(data.get("assistant_message"))
        data["events"] = events
        return cast(ActionGroupDTO, data)

    @staticmethod
    def _normalize_attempt(row: Any) -> ExecutionAttemptDTO:
        data = dict(row)
        # Keep the established public DTO byte-for-byte compatible for legacy
        # attempts while exposing ownership on newly allocated daemon attempts.
        if data.get("owner_instance_id") is None:
            data.pop("owner_instance_id", None)
        data["error"] = _json_load(data.get("error"))
        return cast(ExecutionAttemptDTO, data)


__all__ = [
    "ACTION_LEDGER_SCHEMA",
    "ActionEventDTO",
    "ActionGroupDTO",
    "ActionLedgerRepository",
    "AttemptStateError",
    "ExecutionAttemptDTO",
]

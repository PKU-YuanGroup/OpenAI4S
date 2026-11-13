"""Durable Run Receipt details, frozen results, and step operation receipts.

This repository extends :class:`~openai4s.storage.rl.RLRepository` but never
opens a second SQLite connection: it shares the Store-owned connection and lock
so a run claim, result freeze, and trajectory projection commit atomically.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import replace
from typing import Any, Mapping

from openai4s.rl.models import ExecutionEvent, Segment, Trajectory
from openai4s.rl.requests import (
    REQUEST_PROTOCOL,
    canonical_json,
    decode_trajectory,
    request_fingerprint,
)
from openai4s.rl.segments import (
    SEGMENT_METADATA,
    interrupted_status,
    lifecycle_event,
)
from openai4s.storage.rl import RLRepository

TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})

# Episode terminal statuses are richer than the four receipt states.  The
# Episode projection keeps the caller's exact status; only the receipt is
# normalized so recovery can answer with a stable four-state vocabulary.
_RECEIPT_STATE_BY_EPISODE_STATUS = {
    "done": "completed",
    "completed": "completed",
    "success": "completed",
    "submitted": "completed",
    "plan": "completed",
    "cancelled": "cancelled",
    "timeout": "interrupted",
    "interrupted": "interrupted",
    "max_turns": "interrupted",
    "no_progress": "interrupted",
    "budget_exhausted": "interrupted",
    "error": "failed",
    "failed": "failed",
    "scientific_failure": "failed",
    "execution_failure": "failed",
    "verifier_failure": "failed",
    # The local executor stops a cell it cannot confine (allowlist egress
    # without a proven sandbox) before running it.
    "egress_boundary_unavailable": "failed",
}
EPISODE_TERMINAL_STATUSES = frozenset(_RECEIPT_STATE_BY_EPISODE_STATUS)

_STEP_PENDING = "pending"
_STEP_RECORDED = "recorded"


def receipt_state_for_status(status: str) -> str:
    """Map one exact Episode terminal status to a four-state receipt.

    An unknown status is rejected instead of being treated as success.
    """

    try:
        return _RECEIPT_STATE_BY_EPISODE_STATUS[status]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unsupported terminal episode status {status!r}") from exc


RL_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rl_run_requests (
    request_id TEXT PRIMARY KEY,
    protocol INTEGER NOT NULL,
    payload TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    reset_metadata TEXT NOT NULL,
    owner_id TEXT
);
CREATE TABLE IF NOT EXISTS rl_run_results (
    request_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    trajectory_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rl_step_receipts (
    request_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    expected_turn INTEGER NOT NULL,
    state TEXT NOT NULL,
    response TEXT,
    PRIMARY KEY (request_id, step_id),
    UNIQUE (request_id, expected_turn)
);
"""


def create_rl_run_schema(conn: sqlite3.Connection) -> None:
    """Install the additive Run Receipt companion tables."""

    for statement in RL_RUNS_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


# Compatibility spelling used by the stage-3 plan text.
create_rl_runs_schema = create_rl_run_schema


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _turn(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected_turn must be an integer >= 0")
    return value


def _decode(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class RunRepository(RLRepository):
    """Shared-connection repository for idempotent rollout admission and results."""

    def __init__(self, repository: RLRepository) -> None:
        if not isinstance(repository, RLRepository):
            raise TypeError("repository must be an RLRepository")
        super().__init__(
            repository._connection, repository._lock, clock_ms=repository._clock_ms
        )

    # -- admission ---------------------------------------------------------

    def admit(self, request_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one request identity and create its accepted receipt once."""

        request_id = _text("request_id", request_id)
        if not isinstance(request, Mapping):
            raise TypeError("request must be a mapping")
        payload = canonical_json(request)
        fingerprint = request_fingerprint(request)
        episode_id = self._request_episode_id(request)

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                request_row = self._request_row_unlocked(request_id)
                if request_row is not None:
                    # The fingerprint is the logical identity.  The stored
                    # payload keeps the original created_at and must not be
                    # rewritten by a retry that arrived later.
                    if request_row["fingerprint"] != fingerprint:
                        raise ValueError(
                            "run request is already bound to different content"
                        )
                    receipt_row = self._receipt_row_unlocked(request_id)
                    if receipt_row is None:
                        raise RuntimeError(
                            f"run request {request_id!r} has no durable receipt"
                        )
                    details = self._details_unlocked(request_row, receipt_row)
                    self._connection.execute("COMMIT")
                    return details

                if self._receipt_row_unlocked(request_id) is not None:
                    self._raise_legacy(request_id)

                occupied = self._connection.execute(
                    "SELECT request_id FROM rl_run_receipts WHERE episode_id=? LIMIT 1",
                    (episode_id,),
                ).fetchone()
                if occupied is not None:
                    raise ValueError("episode is already bound to another run request")
                now = self._clock_ms()
                reset_metadata = {
                    "reset_id": "reset-" + uuid.uuid4().hex,
                    "workspace_id": "workspace-" + uuid.uuid4().hex,
                }
                self._connection.execute(
                    "INSERT INTO rl_run_requests(request_id,protocol,payload,"
                    "fingerprint,reset_metadata,owner_id) VALUES(?,?,?,?,?,NULL)",
                    (
                        request_id,
                        REQUEST_PROTOCOL,
                        payload,
                        fingerprint,
                        canonical_json(reset_metadata),
                    ),
                )
                self._connection.execute(
                    "INSERT INTO rl_run_receipts(request_id,fingerprint,episode_id,"
                    "state,accepted_at,updated_at) VALUES(?,?,?,'accepted',?,?)",
                    (request_id, fingerprint, episode_id, now, now),
                )
                self._connection.execute("COMMIT")
                return {
                    "request_id": request_id,
                    "protocol": REQUEST_PROTOCOL,
                    "payload": json.loads(payload),
                    "fingerprint": fingerprint,
                    "reset_metadata": reset_metadata,
                    "owner_id": None,
                    "episode_id": episode_id,
                    "state": "accepted",
                    "accepted_at": now,
                    "updated_at": now,
                }
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def claim(self, request_id: str, fingerprint: str, owner_id: str) -> dict[str, Any]:
        """CAS ``accepted`` to ``running``; a running claim is never replayed."""

        request_id = _text("request_id", request_id)
        fingerprint = _text("fingerprint", fingerprint)
        owner_id = _text("owner_id", owner_id)

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                request_row, receipt_row = self._required_rows_unlocked(request_id)
                if (
                    request_row["fingerprint"] != fingerprint
                    or receipt_row["fingerprint"] != fingerprint
                ):
                    raise ValueError(
                        "run request is already bound to different content"
                    )
                state = str(receipt_row["state"])
                if state == "accepted":
                    now = self._clock_ms()
                    if request_row["owner_id"] is not None:
                        raise RuntimeError(
                            f"run request {request_id!r} has a conflicting owner"
                        )
                    self._connection.execute(
                        "UPDATE rl_run_receipts SET state='running',updated_at=? "
                        "WHERE request_id=?",
                        (now, request_id),
                    )
                    self._connection.execute(
                        "UPDATE rl_run_requests SET owner_id=? WHERE request_id=?",
                        (owner_id, request_id),
                    )
                    request_row = self._request_row_unlocked(request_id)
                    receipt_row = self._receipt_row_unlocked(request_id)
                    assert request_row is not None and receipt_row is not None
                    details = self._details_unlocked(request_row, receipt_row)
                    self._connection.execute("COMMIT")
                    return details
                if state == "running":
                    raise RuntimeError(
                        f"run request {request_id!r} is already claimed for execution"
                    )
                if state in TERMINAL_RUN_STATES:
                    details = self._details_unlocked(request_row, receipt_row)
                    self._connection.execute("COMMIT")
                    return details
                raise RuntimeError(
                    f"run receipt {request_id!r} has unknown state {state!r}; "
                    "recovery is required"
                )
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def get(self, request_id: str) -> dict[str, Any] | None:
        """Return combined request details and current receipt state."""

        request_id = _text("request_id", request_id)
        with self._lock:
            request_row = self._request_row_unlocked(request_id)
            receipt_row = self._receipt_row_unlocked(request_id)
            if request_row is None:
                if receipt_row is not None:
                    self._raise_legacy(request_id)
                return None
            self._check_protocol(request_row)
            if receipt_row is None:
                raise RuntimeError(f"run request {request_id!r} has no durable receipt")
            return self._details_unlocked(request_row, receipt_row)

    # -- immutable results -------------------------------------------------

    def result(self, request_id: str) -> Trajectory | None:
        """Return the frozen canonical trajectory, verifying bytes and schema."""

        request_id = _text("request_id", request_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT payload,trajectory_sha256 FROM rl_run_results "
                "WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            return self._verified_result_unlocked(row)

    def finalize(
        self,
        request_id: str,
        owner_id: str,
        episode_id: str,
        status: str,
        terminal_event: ExecutionEvent,
    ) -> Trajectory:
        """Atomically close lifecycle, freeze the trajectory, and settle receipt."""

        request_id = _text("request_id", request_id)
        owner_id = _text("owner_id", owner_id)
        episode_id = _text("episode_id", episode_id)
        receipt_state = receipt_state_for_status(status)
        if not isinstance(terminal_event, ExecutionEvent):
            raise TypeError("terminal_event must be an ExecutionEvent")
        if terminal_event.episode_id != episode_id:
            raise ValueError("terminal event belongs to a different episode")
        if terminal_event.kind != "episode.finished":
            raise ValueError("terminal event must be episode.finished")
        reason = terminal_event.payload.get("stop_reason")
        if reason != status and not (
            status == "completed" and reason in {"submitted", "plan"}
        ):
            raise ValueError("terminal event stop_reason does not match episode status")

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                request_row, receipt_row = self._required_rows_unlocked(request_id)
                if receipt_row["episode_id"] != episode_id:
                    raise ValueError("run receipt belongs to a different episode")
                if request_row["owner_id"] != owner_id:
                    raise ValueError("run request belongs to a different owner")

                state = str(receipt_row["state"])
                if state in TERMINAL_RUN_STATES:
                    # The Episode projection keeps the exact terminal status.
                    # Several Episode statuses share one receipt state, so a
                    # retry must compare the original status, not the mapping.
                    stored_episode = self._connection.execute(
                        "SELECT status FROM rl_episodes WHERE episode_id=?",
                        (episode_id,),
                    ).fetchone()
                    if stored_episode is None:
                        raise KeyError(episode_id)
                    stored_status = str(stored_episode["status"])
                    if stored_status != status:
                        raise ValueError(
                            f"run request {request_id!r} is already terminal as "
                            f"{stored_status!r}"
                        )
                    frozen = self._connection.execute(
                        "SELECT payload,trajectory_sha256 FROM rl_run_results "
                        "WHERE request_id=?",
                        (request_id,),
                    ).fetchone()
                    if frozen is None:
                        raise RuntimeError(
                            f"terminal run request {request_id!r} has no frozen result"
                        )
                    trajectory = self._verified_result_unlocked(frozen)
                    self._connection.execute("COMMIT")
                    return trajectory
                if state != "running":
                    raise ValueError(
                        f"run request {request_id!r} must be claimed before finalize"
                    )

                existing_terminal = self._connection.execute(
                    "SELECT event_id FROM rl_execution_events WHERE episode_id=? AND kind='episode.finished'",
                    (episode_id,),
                ).fetchone()
                if existing_terminal is not None:
                    raise ValueError(
                        "run already has terminal evidence without a frozen result"
                    )
                now = self._clock_ms()
                self._close_open_segments_unlocked(episode_id, status, now)
                self._append_event_unlocked(replace(terminal_event, sequence=None))
                episode_row = self._connection.execute(
                    "SELECT status FROM rl_episodes WHERE episode_id=?",
                    (episode_id,),
                ).fetchone()
                if episode_row is None:
                    raise KeyError(episode_id)
                if episode_row["status"] not in {"running", status}:
                    raise ValueError(f"episode {episode_id!r} is already terminal")
                self._connection.execute(
                    "UPDATE rl_episodes SET status=?,updated_at=? WHERE episode_id=?",
                    (status, now, episode_id),
                )

                trajectory = self._load_trajectory_unlocked(episode_id)
                payload = trajectory.canonical_json()
                trajectory_sha256 = trajectory.sha256()
                frozen = self._connection.execute(
                    "SELECT payload,trajectory_sha256 FROM rl_run_results "
                    "WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if frozen is None:
                    self._connection.execute(
                        "INSERT INTO rl_run_results(request_id,payload,"
                        "trajectory_sha256) VALUES(?,?,?)",
                        (request_id, payload, trajectory_sha256),
                    )
                else:
                    existing = self._verified_result_unlocked(frozen)
                    if existing.canonical_json() != payload:
                        raise ValueError(
                            "run result is already frozen with different content"
                        )
                self._connection.execute(
                    "UPDATE rl_run_receipts SET state=?,updated_at=? WHERE request_id=?",
                    (receipt_state, now, request_id),
                )
                self._connection.execute("COMMIT")
                return trajectory
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    # -- durable step operations ------------------------------------------

    def claim_step(
        self,
        request_id: str,
        owner_id: str,
        step_id: str,
        expected_turn: int,
        fingerprint: str,
    ) -> dict[str, Any]:
        """Claim one step ID for one turn; exact retries return the same row."""

        request_id = _text("request_id", request_id)
        owner_id = _text("owner_id", owner_id)
        step_id = _text("step_id", step_id)
        expected_turn = _turn(expected_turn)
        fingerprint = _text("fingerprint", fingerprint)

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                request_row, receipt_row = self._required_rows_unlocked(request_id)
                existing = self._step_row_unlocked(request_id, step_id)
                if existing is not None:
                    self._check_owner(request_row, owner_id)
                    if (
                        existing["fingerprint"] != fingerprint
                        or int(existing["expected_turn"]) != expected_turn
                    ):
                        raise ValueError(
                            f"step {step_id!r} is bound to different content"
                        )
                    details = self._step_details(existing)
                    self._connection.execute("COMMIT")
                    return details

                if receipt_row["state"] != "running":
                    raise ValueError(
                        f"run request {request_id!r} is not running for a new step"
                    )
                self._check_owner(request_row, owner_id)
                turn_row = self._connection.execute(
                    "SELECT step_id FROM rl_step_receipts "
                    "WHERE request_id=? AND expected_turn=?",
                    (request_id, expected_turn),
                ).fetchone()
                if turn_row is not None:
                    raise ValueError(
                        f"turn {expected_turn} is already claimed by step "
                        f"{turn_row['step_id']!r}"
                    )
                self._connection.execute(
                    "INSERT INTO rl_step_receipts(request_id,step_id,fingerprint,"
                    "expected_turn,state,response) VALUES(?,?,?,?,?,NULL)",
                    (
                        request_id,
                        step_id,
                        fingerprint,
                        expected_turn,
                        _STEP_PENDING,
                    ),
                )
                row = self._step_row_unlocked(request_id, step_id)
                assert row is not None
                details = self._step_details(row)
                self._connection.execute("COMMIT")
                return details
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def record_step(
        self,
        request_id: str,
        owner_id: str,
        step_id: str,
        response: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Freeze one completed step response; different bytes conflict."""

        request_id = _text("request_id", request_id)
        owner_id = _text("owner_id", owner_id)
        step_id = _text("step_id", step_id)
        if not isinstance(response, Mapping):
            raise TypeError("response must be a mapping")
        payload = canonical_json(response)

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                request_row, receipt_row = self._required_rows_unlocked(request_id)
                self._check_owner(request_row, owner_id)
                row = self._step_row_unlocked(request_id, step_id)
                if row is None:
                    raise KeyError(step_id)
                if row["state"] == _STEP_RECORDED:
                    if row["response"] != payload:
                        raise ValueError(
                            f"step {step_id!r} is already recorded with different content"
                        )
                    details = self._step_details(row)
                    self._connection.execute("COMMIT")
                    return details
                if row["state"] != _STEP_PENDING:
                    raise ValueError(
                        f"step {step_id!r} has unknown state {row['state']!r}"
                    )
                if (
                    receipt_row["state"] != "running"
                    and self._connection.execute(
                        "SELECT 1 FROM rl_run_results WHERE request_id=?", (request_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError(
                        f"run request {request_id!r} has no confirmed terminal result"
                    )
                self._connection.execute(
                    "UPDATE rl_step_receipts SET state=?,response=? "
                    "WHERE request_id=? AND step_id=?",
                    (_STEP_RECORDED, payload, request_id, step_id),
                )
                row = self._step_row_unlocked(request_id, step_id)
                assert row is not None
                details = self._step_details(row)
                self._connection.execute("COMMIT")
                return details
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def step_result(self, request_id: str, step_id: str) -> dict[str, Any] | None:
        """Return one frozen step row, including after the run is terminal."""

        request_id = _text("request_id", request_id)
        step_id = _text("step_id", step_id)
        with self._lock:
            row = self._step_row_unlocked(request_id, step_id)
            return None if row is None else self._step_details(row)

    # -- internals ---------------------------------------------------------

    def _request_row_unlocked(self, request_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM rl_run_requests WHERE request_id=?", (request_id,)
        ).fetchone()

    def _receipt_row_unlocked(self, request_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM rl_run_receipts WHERE request_id=?", (request_id,)
        ).fetchone()

    def _required_rows_unlocked(
        self, request_id: str
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        request_row = self._request_row_unlocked(request_id)
        receipt_row = self._receipt_row_unlocked(request_id)
        if request_row is None:
            if receipt_row is not None:
                self._raise_legacy(request_id)
            raise KeyError(request_id)
        self._check_protocol(request_row)
        if receipt_row is None:
            raise RuntimeError(f"run request {request_id!r} has no durable receipt")
        return request_row, receipt_row

    @staticmethod
    def _check_protocol(request_row: sqlite3.Row) -> None:
        if int(request_row["protocol"]) != REQUEST_PROTOCOL:
            raise RuntimeError(
                f"run request {request_row['request_id']!r} uses unsupported "
                f"protocol {request_row['protocol']!r}; recovery is required"
            )

    @staticmethod
    def _raise_legacy(request_id: str) -> None:
        raise RuntimeError(
            f"run receipt {request_id!r} predates run request details; "
            "recovery_required: explicit legacy recovery is required"
        )

    @staticmethod
    def _check_owner(request_row: sqlite3.Row, owner_id: str) -> None:
        if request_row["owner_id"] != owner_id:
            raise ValueError("run request belongs to a different owner")

    @staticmethod
    def _request_episode_id(request: Mapping[str, Any]) -> str:
        episode = request.get("episode")
        if not isinstance(episode, Mapping):
            raise ValueError("request episode must be an object")
        return _text("episode_id", episode.get("episode_id"))

    def _details_unlocked(
        self, request_row: sqlite3.Row, receipt_row: sqlite3.Row
    ) -> dict[str, Any]:
        self._check_protocol(request_row)
        return {
            "request_id": str(request_row["request_id"]),
            "protocol": int(request_row["protocol"]),
            "payload": json.loads(request_row["payload"]),
            "fingerprint": str(request_row["fingerprint"]),
            "reset_metadata": json.loads(request_row["reset_metadata"]),
            "owner_id": request_row["owner_id"],
            "episode_id": str(receipt_row["episode_id"]),
            "state": str(receipt_row["state"]),
            "accepted_at": int(receipt_row["accepted_at"]),
            "updated_at": int(receipt_row["updated_at"]),
        }

    def _verified_result_unlocked(self, row: sqlite3.Row) -> Trajectory:
        payload = str(row["payload"])
        expected = str(row["trajectory_sha256"])
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != expected:
            raise ValueError("stored run result hash does not match its payload")
        trajectory = decode_trajectory(payload)
        if trajectory.canonical_json() != payload or trajectory.sha256() != expected:
            raise ValueError("stored run result is not a canonical trajectory")
        return trajectory

    def _step_row_unlocked(self, request_id: str, step_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM rl_step_receipts WHERE request_id=? AND step_id=?",
            (request_id, step_id),
        ).fetchone()

    @staticmethod
    def _step_details(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "request_id": str(row["request_id"]),
            "step_id": str(row["step_id"]),
            "fingerprint": str(row["fingerprint"]),
            "expected_turn": int(row["expected_turn"]),
            "state": str(row["state"]),
            "response": _decode(row["response"]),
        }

    def _close_open_segments_unlocked(
        self, episode_id: str, status: str, now: int
    ) -> None:
        """Close every open Segment child-first and clear stale phase binding."""

        rows = self._connection.execute(
            "SELECT payload FROM rl_segments WHERE episode_id=? AND status='open'",
            (episode_id,),
        ).fetchall()
        segments: dict[str, Segment] = {}
        for row in rows:
            segment = Segment(**json.loads(row["payload"]))
            if segment.status != "open":
                raise ValueError(
                    f"segment {segment.segment_id!r} has inconsistent status"
                )
            segments[segment.segment_id] = segment

        def depth(segment: Segment, seen: frozenset[str]) -> int:
            if segment.parent_segment_id is None:
                return 0
            if segment.parent_segment_id in seen:
                raise ValueError("segment parent cycle detected")
            parent = segments.get(segment.parent_segment_id)
            if parent is None:
                return 0
            return 1 + depth(parent, seen | {segment.segment_id})

        ordered = sorted(
            segments.values(),
            key=lambda item: (-depth(item, frozenset()), item.segment_id),
        )
        # An unclosed phase is interrupted, never inferred successful: the
        # same label the recorder path gives it, whatever the admission mode.
        closed_status = interrupted_status(status)
        for segment in ordered:
            closed = replace(segment, status=closed_status)
            self._connection.execute(
                "UPDATE rl_segments SET status=?,payload=? WHERE segment_id=?",
                (closed_status, canonical_json(closed.to_dict()), segment.segment_id),
            )
            self._append_event_unlocked(
                lifecycle_event(closed, started=False, at=now, reason=status)
            )
            info = segment.metadata.get(SEGMENT_METADATA, {})
            if isinstance(info, Mapping) and info.get("persistent"):
                self._connection.execute(
                    "UPDATE rl_phase_bindings SET segment_id=?,revision=revision+1 "
                    "WHERE episode_id=? AND segment_id=?",
                    (segment.parent_segment_id, episode_id, segment.segment_id),
                )

        self._connection.execute(
            "UPDATE rl_phase_bindings SET segment_id=NULL,revision=revision+1 "
            "WHERE episode_id=? AND segment_id IS NOT NULL AND segment_id NOT IN "
            "(SELECT segment_id FROM rl_segments WHERE episode_id=? AND status='open')",
            (episode_id, episode_id),
        )


__all__ = [
    "RL_RUNS_SCHEMA",
    "RunRepository",
    "create_rl_run_schema",
    "create_rl_runs_schema",
]

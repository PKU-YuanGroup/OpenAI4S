"""Durable Lab ledger over the Store connection; no provider or in-memory fallback."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping

from openai4s.lab.models import (
    TERMINAL_COMMAND_STATES,
    TERMINAL_RUN_STATUSES,
    CommandState,
    EndReason,
    ErrorCode,
    LabError,
    RunStatus,
    command_sources_for,
    run_sources_for,
)
from openai4s.storage.migrations import apply_ddl_script

LAB_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS lab_runs (
  run_id TEXT PRIMARY KEY,
  root_frame_id TEXT NOT NULL,
  frame_id TEXT,
  owner_user_id TEXT,
  mode TEXT NOT NULL,
  backend TEXT NOT NULL,
  device_id TEXT NOT NULL,
  profile TEXT NOT NULL,
  adapter_version TEXT NOT NULL,
  backend_source_sha TEXT,
  capability_revision TEXT NOT NULL,
  descriptor_json TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  seed INTEGER,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  step_count INTEGER NOT NULL DEFAULT 0,
  command_count INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  end_reason TEXT,
  raw_terminated INTEGER,
  raw_truncated INTEGER,
  budgets_json TEXT NOT NULL,
  parent_run_id TEXT,
  daemon_instance TEXT,
  create_idempotency_key TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  ended_at INTEGER,
  UNIQUE (root_frame_id, create_idempotency_key)
);
CREATE INDEX IF NOT EXISTS lab_runs_root_created ON lab_runs(root_frame_id, created_at);
CREATE INDEX IF NOT EXISTS lab_runs_status ON lab_runs(status);

CREATE TABLE IF NOT EXISTS lab_commands (
  command_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  root_frame_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  operation TEXT NOT NULL,
  capability_id TEXT,
  request_json TEXT NOT NULL,
  expected_revision INTEGER,
  applied_revision INTEGER,
  origin TEXT NOT NULL,
  actor_frame_id TEXT,
  owner_user_id TEXT,
  approval_ref TEXT,
  resources_json TEXT NOT NULL DEFAULT '[]',
  fencing_token INTEGER,
  state TEXT NOT NULL,
  error_code TEXT,
  error TEXT,
  provider_action_json TEXT,
  receipt_json TEXT,
  observation_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  dispatched_at INTEGER,
  completed_at INTEGER,
  UNIQUE (run_id, idempotency_key),
  UNIQUE (run_id, seq)
);
CREATE INDEX IF NOT EXISTS lab_commands_state ON lab_commands(state);

CREATE TABLE IF NOT EXISTS lab_observations (
  observation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  root_frame_id TEXT NOT NULL,
  command_id TEXT,
  sequence INTEGER NOT NULL,
  sim_time REAL,
  sim_time_unit TEXT,
  wall_time_ms INTEGER NOT NULL,
  channels_json TEXT NOT NULL,
  artifact_version_id TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE (run_id, sequence)
);

CREATE TABLE IF NOT EXISTS lab_evaluations (
  evaluation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  root_frame_id TEXT NOT NULL,
  command_id TEXT,
  sequence INTEGER NOT NULL,
  reward REAL,
  ground_truth_json TEXT,
  metrics_json TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS lab_evaluations_run ON lab_evaluations(run_id, sequence);

CREATE TABLE IF NOT EXISTS lab_leases (
  resource_key TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  root_frame_id TEXT NOT NULL,
  state TEXT NOT NULL,
  holder_command_id TEXT,
  fencing_token INTEGER NOT NULL DEFAULT 0,
  acquired_at INTEGER,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lab_events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  root_frame_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  state TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS lab_events_root ON lab_events(root_frame_id, event_seq);
"""


# CONTRACT §4. The state tables live once, in openai4s.lab.models; the ledger
# derives its CAS sources from them so the two can never disagree.
# Reconciliation (a receipt, or proof the device never received the command)
# is the only way out of outcome_unknown.
_COMMAND_SOURCES: Mapping[str, frozenset[str]] = {
    target.value: frozenset(source.value for source in command_sources_for(target))
    for target in CommandState
    if command_sources_for(target)
}
_COMMAND_TERMINAL = frozenset(state.value for state in TERMINAL_COMMAND_STATES)
_RUN_TERMINAL = frozenset(status.value for status in TERMINAL_RUN_STATUSES)
_RUN_SOURCES: Mapping[str, frozenset[str]] = {
    target.value: frozenset(source.value for source in run_sources_for(target))
    for target in RunStatus
    if run_sources_for(target)
}
_DISPATCHED = frozenset({"dispatching", "running", "stop_requested", "outcome_unknown"})
_DISPATCH_EXITS = frozenset(
    {"succeeded", "failed", "outcome_unknown", "not_dispatched", "stopped"}
)
_END_REASONS = frozenset(reason.value for reason in EndReason)
_ERROR_CODES = frozenset(code.value for code in ErrorCode)
_RUN_FIELDS = frozenset(
    {"daemon_instance", "end_reason", "raw_terminated", "raw_truncated"}
)
_COMMAND_FIELDS = frozenset({"error_code", "error", "approval_ref", "capability_id"})


def _ledger_error(
    code: str, message: str, details: Mapping[str, Any] | None = None
) -> Exception:
    """The single place the ledger builds a domain error (CONTRACT §6)."""
    return LabError(ErrorCode(code), message, details)


def create_lab_ledger_schema(conn: sqlite3.Connection) -> None:
    """Apply idempotent DDL inside the Store's migration transaction."""
    apply_ddl_script(conn, LAB_LEDGER_SCHEMA)


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise _ledger_error("invalid_parameters", "Invalid ledger JSON") from None


def _bounded_error(value: str | None) -> str | None:
    if value is None:
        return None
    marker = "…[truncated]"
    return value if len(value) <= 2000 else value[: 2000 - len(marker)] + marker


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = {}
    for key in row.keys():
        value = row[key]
        if key.endswith("_json"):
            key = key[:-5]
            try:
                value = json.loads(value) if value is not None else None
            except (TypeError, ValueError):
                raise _ledger_error(
                    "persistence_unavailable", "Invalid stored ledger JSON"
                ) from None
        elif key in {"raw_terminated", "raw_truncated"}:
            value = bool(value) if value is not None else None
        result[key] = value
    return result


def _sources(
    target: str, requested: Any, table: Mapping[str, frozenset[str]]
) -> tuple[str, ...]:
    if target not in table:
        raise ValueError("Unreachable ledger transition target")
    sources = table[target] if requested is None else frozenset(requested)
    if not sources <= table[target]:
        raise ValueError("Unreachable ledger transition source")
    return tuple(sorted(sources))


def _fields(fields: dict, allowed: frozenset[str]) -> dict:
    if fields.keys() - allowed:
        raise _ledger_error("invalid_parameters", "Unsupported ledger update fields")
    result = dict(fields)
    if (
        result.get("end_reason") is not None
        and result["end_reason"] not in _END_REASONS
    ):
        raise _ledger_error("invalid_parameters", "Invalid run end reason")
    if (
        result.get("error_code") is not None
        and result["error_code"] not in _ERROR_CODES
    ):
        raise _ledger_error("invalid_parameters", "Invalid command error code")
    for key in ("raw_terminated", "raw_truncated"):
        if key in result and result[key] is not None and type(result[key]) is not bool:
            raise _ledger_error("invalid_parameters", "Invalid raw termination flag")
    if "error" in result:
        if result["error"] is not None and not isinstance(result["error"], str):
            raise _ledger_error("invalid_parameters", "Invalid command error text")
        result["error"] = _bounded_error(result["error"])
    return result


class LabLedger:
    """The Store owns the connection, lock and clock. Each write commits once.

    Private SQL helpers never acquire a new transaction or commit. Public reads
    translate SQLite errors too; unavailable persistence is never an empty list.
    Domain errors have fixed messages so SQLite/provider payloads cannot leak.
    """

    def __init__(
        self, conn: sqlite3.Connection, lock: Any, *, clock_ms: Callable[[], int]
    ):
        self._conn = conn
        self._lock = lock
        self._clock_ms = clock_ms

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            begun = False
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                begun = True
                yield
                self._conn.commit()
            except BaseException as exc:
                if begun:
                    try:
                        self._conn.rollback()
                    except sqlite3.Error:
                        raise _ledger_error(
                            "persistence_unavailable", "Lab ledger rollback failed"
                        ) from None
                if isinstance(exc, sqlite3.Error):
                    raise _ledger_error(
                        "persistence_unavailable", "Lab ledger persistence unavailable"
                    ) from None
                raise

    def _one(self, table: str, key: str, value: str) -> dict | None:
        return _row(
            self._conn.execute(
                f"SELECT * FROM {table} WHERE {key}=?", (value,)
            ).fetchone()
        )

    def _run(self, run_id: str) -> dict:
        row = self._one("lab_runs", "run_id", run_id)
        if row is None:
            raise _ledger_error("run_not_found", "Lab run not found")
        return row

    def _command(self, command_id: str) -> dict:
        row = self._one("lab_commands", "command_id", command_id)
        if row is None:
            raise ValueError("Lab command not found")
        return row

    def _read(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            try:
                return [_row(row) for row in self._conn.execute(sql, params).fetchall()]
            except sqlite3.Error:
                raise _ledger_error(
                    "persistence_unavailable", "Lab ledger persistence unavailable"
                ) from None

    def _insert(self, table: str, values: dict) -> None:
        self._conn.execute(
            f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
            tuple(values.values()),
        )

    def _update(self, table: str, key: str, value: str, fields: dict) -> None:
        self._conn.execute(
            f"UPDATE {table} SET {','.join(k + '=?' for k in fields)} WHERE {key}=?",
            (*fields.values(), value),
        )

    def _event(
        self, run: dict, kind: str, ref_id: str, state: str | None, now: int
    ) -> None:
        self._insert(
            "lab_events",
            {
                "root_frame_id": run["root_frame_id"],
                "run_id": run["run_id"],
                "kind": kind,
                "ref_id": ref_id,
                "state": state,
                "created_at": now,
            },
        )

    def create_run(self, run) -> tuple[dict, bool]:
        with self._transaction():
            existing = _row(
                self._conn.execute(
                    "SELECT * FROM lab_runs WHERE root_frame_id=? AND create_idempotency_key=?",
                    (run["root_frame_id"], run["create_idempotency_key"]),
                ).fetchone()
            )
            if existing:
                if existing["config_hash"] != run["config_hash"]:
                    raise _ledger_error(
                        "idempotency_conflict",
                        "Run key already names another configuration",
                    )
                return existing, False
            if run["mode"] != "simulation":
                raise _ledger_error(
                    "mode_mismatch", "Lab ledger requires simulation mode"
                )
            now = self._clock_ms()
            values = {
                key: run[key]
                for key in (
                    "run_id",
                    "root_frame_id",
                    "mode",
                    "backend",
                    "device_id",
                    "profile",
                    "adapter_version",
                    "capability_revision",
                    "config_hash",
                    "create_idempotency_key",
                )
            }
            values.update(
                {
                    key: run.get(key)
                    for key in (
                        "frame_id",
                        "owner_user_id",
                        "backend_source_sha",
                        "seed",
                        "parent_run_id",
                        "daemon_instance",
                    )
                }
            )
            values.update(
                {
                    key + "_json": _json(run[key])
                    for key in ("descriptor", "config", "budgets")
                }
            )
            values.update(status="creating", revision=0, created_at=now, updated_at=now)
            self._insert("lab_runs", values)
            result = self._run(run["run_id"])
            self._event(result, "run", result["run_id"], "creating", now)
            return result, True

    def get_run(self, run_id) -> dict | None:
        rows = self._read("SELECT * FROM lab_runs WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    def list_runs(self, root_frame_id, *, limit=20) -> list[dict]:
        return self._read(
            "SELECT * FROM lab_runs WHERE root_frame_id=? ORDER BY created_at DESC,run_id DESC LIMIT ?",
            (root_frame_id, max(0, limit)),
        )

    def _cas(
        self,
        table: str,
        key: str,
        value: str,
        column: str,
        target: str,
        sources: tuple,
        fields: dict,
    ) -> bool:
        fields = {**fields, column: target}
        return bool(
            self._conn.execute(
                f"UPDATE {table} SET {','.join(k + '=?' for k in fields)} WHERE {key}=? AND {column} IN ({','.join('?' for _ in sources)})",
                (*fields.values(), value, *sources),
            ).rowcount
        )

    def set_run_status(
        self, run_id, *, to_status, from_statuses=None, **fields
    ) -> bool:
        sources = _sources(to_status, from_statuses, _RUN_SOURCES)
        values = _fields(fields, _RUN_FIELDS)
        with self._transaction():
            now = self._clock_ms()
            values["updated_at"] = now
            if to_status in _RUN_TERMINAL:
                values["ended_at"] = now
            if not self._cas(
                "lab_runs", "run_id", run_id, "status", to_status, sources, values
            ):
                return False
            self._event(self._run(run_id), "run", run_id, to_status, now)
            return True

    def end_run(self, run_id, *, end_reason, status="ended") -> bool:
        if status not in _RUN_TERMINAL:
            raise ValueError("Run end status must be terminal")
        values = _fields({"end_reason": end_reason}, _RUN_FIELDS)
        with self._transaction():
            now = self._clock_ms()
            values.update(updated_at=now, ended_at=now)
            if not self._cas(
                "lab_runs",
                "run_id",
                run_id,
                "status",
                status,
                tuple(sorted(_RUN_SOURCES[status])),
                values,
            ):
                return False
            self._conn.execute(
                "UPDATE lab_leases SET state='free',holder_command_id=NULL,updated_at=? WHERE run_id=? AND state='held'",
                (now, run_id),
            )
            self._event(self._run(run_id), "run", run_id, status, now)
            return True

    def nonterminal_runs(self) -> list[dict]:
        return self._read(
            "SELECT * FROM lab_runs WHERE status NOT IN ('ended','failed') ORDER BY created_at,run_id"
        )

    def insert_command(self, command) -> tuple[dict, bool]:
        with self._transaction():
            existing = _row(
                self._conn.execute(
                    "SELECT * FROM lab_commands WHERE run_id=? AND idempotency_key=?",
                    (command["run_id"], command["idempotency_key"]),
                ).fetchone()
            )
            if existing:
                if existing["request_hash"] != command["request_hash"]:
                    raise _ledger_error(
                        "idempotency_conflict",
                        "Command key already names another request",
                    )
                return existing, False
            run = self._run(command["run_id"])
            if (
                command.get("root_frame_id", run["root_frame_id"])
                != run["root_frame_id"]
            ):
                raise _ledger_error(
                    "invalid_parameters", "Command root does not match run"
                )
            state = command.get("state", "created")
            if state not in {"created", *_COMMAND_SOURCES}:
                raise _ledger_error("invalid_parameters", "Invalid command state")
            if command["origin"] not in {
                "agent_tool",
                "host_sdk",
                "manual_ui",
                "system",
            }:
                raise _ledger_error("invalid_parameters", "Invalid command origin")
            now = self._clock_ms()
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM lab_commands WHERE run_id=?",
                (run["run_id"],),
            ).fetchone()[0]
            values = {
                key: command[key]
                for key in (
                    "command_id",
                    "run_id",
                    "idempotency_key",
                    "request_hash",
                    "operation",
                    "origin",
                )
            }
            values.update(
                {
                    key: command.get(key)
                    for key in (
                        "capability_id",
                        "expected_revision",
                        "actor_frame_id",
                        "owner_user_id",
                        "approval_ref",
                    )
                }
            )
            values.update(
                _fields(
                    {
                        key: command[key]
                        for key in ("error_code", "error")
                        if key in command
                    },
                    _COMMAND_FIELDS,
                )
            )
            values.update(
                root_frame_id=run["root_frame_id"],
                seq=seq,
                state=state,
                request_json=_json(command["request"]),
                created_at=now,
                updated_at=now,
                completed_at=now if state in _COMMAND_TERMINAL else None,
            )
            self._insert("lab_commands", values)
            self._conn.execute(
                "UPDATE lab_runs SET command_count=command_count+1,updated_at=? WHERE run_id=?",
                (now, run["run_id"]),
            )
            self._event(run, "command", command["command_id"], state, now)
            return self._command(command["command_id"]), True

    def get_command(self, command_id) -> dict | None:
        rows = self._read(
            "SELECT * FROM lab_commands WHERE command_id=?", (command_id,)
        )
        return rows[0] if rows else None

    def transition_command(
        self, command_id, *, to_state, from_states=None, **fields
    ) -> bool:
        sources = _sources(to_state, from_states, _COMMAND_SOURCES)
        values = _fields(fields, _COMMAND_FIELDS)
        with self._transaction():
            # Once a command has been sent, leaving dispatch changes leases and
            # the run row too: record_receipt, mark_outcome_unknown and
            # mark_not_dispatched own those exits. A bare CAS has no receipt and
            # must not consume the only chance to record what happened.
            if to_state in _DISPATCH_EXITS and _DISPATCHED.intersection(sources):
                current = self._one("lab_commands", "command_id", command_id)
                if current is not None and current["state"] in _DISPATCHED:
                    raise ValueError(
                        "A dispatched command leaves dispatch through its receipt"
                    )
            now = self._clock_ms()
            values["updated_at"] = now
            if to_state in _COMMAND_TERMINAL:
                values["completed_at"] = now
            if not self._cas(
                "lab_commands",
                "command_id",
                command_id,
                "state",
                to_state,
                sources,
                values,
            ):
                return False
            command = self._command(command_id)
            self._event(command, "command", command_id, to_state, now)
            return True

    def list_commands(self, run_id, *, after_seq=0, limit=100) -> list[dict]:
        return self._read(
            "SELECT * FROM lab_commands WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
            (run_id, after_seq, max(0, limit)),
        )

    def inflight_commands(self, run_id=None) -> list[dict]:
        where = " AND run_id=?" if run_id is not None else ""
        return self._read(
            "SELECT * FROM lab_commands WHERE state IN ('admitted','dispatching','running','stop_requested')"
            + where
            + " ORDER BY created_at,command_id",
            (run_id,) if run_id is not None else (),
        )

    def begin_dispatch(
        self, command_id, *, resource_keys, expected_revision, now_ms=None
    ) -> int:
        keys = sorted(set(resource_keys))
        with self._transaction():
            command = self._command(command_id)
            if command["state"] != "admitted":
                raise ValueError("Dispatch requires an admitted command")
            run = self._run(command["run_id"])
            if run["status"] in {"busy", "creating"}:
                raise _ledger_error("resource_busy", "Lab run is busy")
            if run["status"] == "quarantined":
                raise _ledger_error("resource_quarantined", "Lab run is quarantined")
            if run["status"] in _RUN_TERMINAL:
                raise _ledger_error("run_ended", "Lab run has ended")
            if run["revision"] != expected_revision:
                raise _ledger_error(
                    "stale_revision",
                    "Lab run revision changed",
                    {"revision": run["revision"]},
                )
            now = self._clock_ms() if now_ms is None else now_ms
            leases = []
            for key in keys:
                lease = self._one("lab_leases", "resource_key", key)
                if lease is None:
                    self._insert(
                        "lab_leases",
                        {
                            "resource_key": key,
                            "run_id": run["run_id"],
                            "root_frame_id": run["root_frame_id"],
                            "state": "free",
                            "updated_at": now,
                        },
                    )
                    lease = self._one("lab_leases", "resource_key", key)
                if lease["state"] == "quarantined":
                    raise _ledger_error(
                        "resource_quarantined", "Lab resource is quarantined"
                    )
                if lease["state"] == "held":
                    holder = self._one(
                        "lab_commands", "command_id", lease["holder_command_id"]
                    )
                    # Missing ownership evidence is not proof of a terminal holder.
                    if holder is None or holder["state"] not in _COMMAND_TERMINAL:
                        raise _ledger_error("resource_busy", "Lab resource is held")
                leases.append(lease)
            token = max((lease["fencing_token"] for lease in leases), default=0) + 1
            for key in keys:
                self._update(
                    "lab_leases",
                    "resource_key",
                    key,
                    {
                        "run_id": run["run_id"],
                        "root_frame_id": run["root_frame_id"],
                        "state": "held",
                        "holder_command_id": command_id,
                        "fencing_token": token,
                        "acquired_at": now,
                        "updated_at": now,
                    },
                )
            self._update(
                "lab_commands",
                "command_id",
                command_id,
                {
                    "state": "dispatching",
                    "dispatched_at": now,
                    "fencing_token": token,
                    "resources_json": _json(keys),
                    "updated_at": now,
                },
            )
            self._update(
                "lab_runs",
                "run_id",
                run["run_id"],
                {"status": "busy", "updated_at": now},
            )
            self._event(run, "command", command_id, "dispatching", now)
            self._event(run, "run", run["run_id"], "busy", now)
            return token

    def _append_observation(
        self,
        run: dict,
        command_id: str | None,
        sequence: int,
        observation: Mapping[str, Any],
        evaluation: Mapping[str, Any] | None,
        now: int,
    ) -> dict:
        observation_id = observation.get(
            "observation_id"
        ) or "labobs-" + secrets.token_hex(6)
        values = {
            key: observation.get(key)
            for key in ("sim_time", "sim_time_unit", "artifact_version_id")
        }
        values.update(
            observation_id=observation_id,
            run_id=run["run_id"],
            root_frame_id=run["root_frame_id"],
            command_id=command_id,
            sequence=sequence,
            wall_time_ms=observation.get("wall_time_ms", now),
            channels_json=_json(observation["channels"]),
            created_at=now,
        )
        self._insert("lab_observations", values)
        if evaluation is not None:
            self._insert(
                "lab_evaluations",
                {
                    "evaluation_id": evaluation.get("evaluation_id")
                    or "labeval-" + secrets.token_hex(6),
                    "run_id": run["run_id"],
                    "root_frame_id": run["root_frame_id"],
                    "command_id": command_id,
                    "sequence": sequence,
                    "reward": evaluation.get("reward"),
                    "ground_truth_json": (
                        _json(evaluation["ground_truth"])
                        if evaluation.get("ground_truth") is not None
                        else None
                    ),
                    "metrics_json": (
                        _json(evaluation["metrics"])
                        if evaluation.get("metrics") is not None
                        else None
                    ),
                    "created_at": now,
                },
            )
        return self._one("lab_observations", "observation_id", observation_id)

    def _release_command_leases(self, command_id: str, now: int) -> None:
        self._conn.execute(
            "UPDATE lab_leases SET state='free',holder_command_id=NULL,updated_at=? WHERE holder_command_id=?",
            (now, command_id),
        )

    def record_receipt(self, command_id, *, receipt, observation, evaluation) -> dict:
        with self._transaction():
            command = self._command(command_id)
            if command["state"] not in {
                "dispatching",
                "running",
                "stop_requested",
                "outcome_unknown",
            }:
                raise ValueError("Receipt requires a dispatched or unknown command")
            run = self._run(command["run_id"])
            now = self._clock_ms()
            applied = receipt["applied"]
            if (
                type(applied) is not bool
                or receipt["status"] not in {"succeeded", "failed", "rejected"}
                or (applied and receipt["status"] != "succeeded")
            ):
                raise _ledger_error("invalid_parameters", "Invalid receipt outcome")
            state = (
                "succeeded"
                if applied and receipt["status"] == "succeeded"
                else "failed"
            )
            error = receipt.get("error") or {}
            command_fields = _fields(
                {
                    "error_code": error.get("code") if state == "failed" else None,
                    "error": error.get("message") if state == "failed" else None,
                },
                _COMMAND_FIELDS,
            )
            raw = receipt.get("raw") or {}
            # Even an unapplied receipt can signal termination. Validate the
            # flags before interpreting them, rather than trusting truthiness.
            raw_fields = _fields(
                {
                    "raw_terminated": raw.get("terminated"),
                    "raw_truncated": raw.get("truncated"),
                },
                _RUN_FIELDS,
            )
            run_fields = raw_fields if applied else {}
            run_fields["updated_at"] = now
            obs = None
            if applied:
                if observation is None:
                    raise _ledger_error(
                        "invalid_parameters", "Applied receipt requires an observation"
                    )
                revision = run["revision"] + 1
                run_fields.update(
                    revision=revision,
                    step_count=run["step_count"] + 1,
                    consecutive_failures=0,
                )
                sequence = self._conn.execute(
                    "SELECT COALESCE(MAX(sequence),-1)+1 FROM lab_observations WHERE run_id=?",
                    (run["run_id"],),
                ).fetchone()[0]
                obs = self._append_observation(
                    run, command_id, sequence, observation, evaluation, now
                )
                command_fields.update(
                    applied_revision=revision, observation_id=obs["observation_id"]
                )
            else:
                run_fields["consecutive_failures"] = run["consecutive_failures"] + 1
            end_reason = receipt.get("end_reason")
            if end_reason or raw.get("terminated") or raw.get("truncated"):
                run_fields.update(
                    _fields({"end_reason": end_reason or "env_terminated"}, _RUN_FIELDS)
                )
                run_fields.update(status="ended", ended_at=now)
            elif run["status"] == "busy":
                run_fields["status"] = "ready"
            elif (
                run["status"] == "quarantined" and command["state"] == "outcome_unknown"
            ):
                other_unknown = self._conn.execute(
                    "SELECT 1 FROM lab_commands WHERE run_id=? AND state='outcome_unknown' AND command_id!=? LIMIT 1",
                    (run["run_id"], command_id),
                ).fetchone()
                if other_unknown is None:
                    run_fields["status"] = "ready"
            self._update("lab_runs", "run_id", run["run_id"], run_fields)
            self._release_command_leases(command_id, now)
            saved_receipt = {
                key: value
                for key, value in receipt.items()
                if key not in {"evaluation", "provider_action"}
            }
            if saved_receipt.get("error"):
                saved_receipt["error"] = {
                    **saved_receipt["error"],
                    "message": _bounded_error(saved_receipt["error"].get("message")),
                }
            command_fields.update(
                state=state,
                receipt_json=_json(saved_receipt),
                provider_action_json=(
                    _json(receipt["provider_action"])
                    if receipt.get("provider_action") is not None
                    else None
                ),
                completed_at=now,
                updated_at=now,
            )
            self._update("lab_commands", "command_id", command_id, command_fields)
            updated_run = self._run(run["run_id"])
            self._event(run, "command", command_id, state, now)
            if obs:
                self._event(run, "observation", obs["observation_id"], None, now)
            self._event(run, "run", run["run_id"], updated_run["status"], now)
            return {
                "run": updated_run,
                "command": self._command(command_id),
                "observation": obs,
            }

    def mark_outcome_unknown(self, command_id, *, error) -> bool:
        fields = _fields(
            {"error_code": "outcome_unknown", "error": error}, _COMMAND_FIELDS
        )
        with self._transaction():
            now = self._clock_ms()
            fields["updated_at"] = now
            if not self._cas(
                "lab_commands",
                "command_id",
                command_id,
                "state",
                "outcome_unknown",
                ("dispatching", "running", "stop_requested"),
                fields,
            ):
                return False
            command = self._command(command_id)
            run = self._run(command["run_id"])
            # Late uncertainty must not reopen an ended provider-lost run.
            if run["status"] not in _RUN_TERMINAL:
                self._update(
                    "lab_runs",
                    "run_id",
                    run["run_id"],
                    {"status": "quarantined", "updated_at": now},
                )
            self._conn.execute(
                "UPDATE lab_leases SET state='quarantined',updated_at=? WHERE holder_command_id=?",
                (now, command_id),
            )
            self._event(run, "command", command_id, "outcome_unknown", now)
            self._event(
                run, "run", run["run_id"], self._run(run["run_id"])["status"], now
            )
            return True

    def mark_not_dispatched(self, command_id, *, error_code, error) -> bool:
        fields = _fields({"error_code": error_code, "error": error}, _COMMAND_FIELDS)
        with self._transaction():
            command = self._one("lab_commands", "command_id", command_id)
            now = self._clock_ms()
            fields.update(updated_at=now, completed_at=now)
            # The caller holds proof that the device never received the command:
            # a failed write, or an authoritative ``known: false`` from a session
            # that remembers every id it was sent. A missing receipt is not proof.
            if not self._cas(
                "lab_commands",
                "command_id",
                command_id,
                "state",
                "not_dispatched",
                ("admitted", "dispatching", "outcome_unknown"),
                fields,
            ):
                return False
            run = self._run(command["run_id"])
            self._release_command_leases(command_id, now)
            resolved = None
            if run["status"] == "busy" and command["state"] == "dispatching":
                resolved = "ready"
            elif (
                run["status"] == "quarantined" and command["state"] == "outcome_unknown"
            ):
                other_unknown = self._conn.execute(
                    "SELECT 1 FROM lab_commands WHERE run_id=? AND state='outcome_unknown' AND command_id!=? LIMIT 1",
                    (run["run_id"], command_id),
                ).fetchone()
                if other_unknown is None:
                    resolved = "ready"
            if resolved is not None:
                self._update(
                    "lab_runs",
                    "run_id",
                    run["run_id"],
                    {"status": resolved, "updated_at": now},
                )
                self._event(run, "run", run["run_id"], resolved, now)
            self._event(run, "command", command_id, "not_dispatched", now)
            return True

    def append_initial_observation(self, run_id, *, observation, evaluation) -> dict:
        with self._transaction():
            run = self._run(run_id)
            if run["status"] != "creating":
                raise ValueError("Initial observation requires a creating run")
            now = self._clock_ms()
            obs = self._append_observation(run, None, 0, observation, evaluation, now)
            self._update(
                "lab_runs", "run_id", run_id, {"status": "ready", "updated_at": now}
            )
            self._event(run, "observation", obs["observation_id"], None, now)
            self._event(run, "run", run_id, "ready", now)
            return self._run(run_id)

    def latest_observation(self, run_id) -> dict | None:
        rows = self._read(
            "SELECT * FROM lab_observations WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        )
        return rows[0] if rows else None

    def list_observations(self, run_id, *, after_sequence=-1, limit=100) -> list[dict]:
        return self._read(
            "SELECT * FROM lab_observations WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, max(0, limit)),
        )

    def list_evaluations(self, run_id, *, limit=1000) -> list[dict]:
        return self._read(
            "SELECT * FROM lab_evaluations WHERE run_id=? ORDER BY sequence,evaluation_id LIMIT ?",
            (run_id, max(0, limit)),
        )

    def events_since(self, root_frame_id, *, after_seq=0, limit=200) -> list[dict]:
        return self._read(
            "SELECT * FROM lab_events WHERE root_frame_id=? AND event_seq>? ORDER BY event_seq LIMIT ?",
            (root_frame_id, after_seq, max(0, limit)),
        )

    def latest_event_seq(self, root_frame_id) -> int:
        return self._read(
            "SELECT COALESCE(MAX(event_seq),0) AS seq FROM lab_events WHERE root_frame_id=?",
            (root_frame_id,),
        )[0]["seq"]

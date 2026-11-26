"""Submission receipts for plain messages, on a Store-owned SQLite connection.

A browser whose `POST /frames/{fid}/message` answer is lost -- a dropped
connection, a 5xx from a proxy, a reload -- knows only that it sent
something. Resending may run (and bill) the turn twice; giving up may lose it.
The receipt is what it asks instead: the client mints a 128-bit id, stores it
before dispatch, and the server claims ``(session root, initiating identity,
id)`` before it freezes a model, takes a ticket or materializes a file. Every
later request naming the same key reads that row and never submits again.

One row per key, and the key is the primary key, so two concurrent identical
requests cannot both claim it. ``request_digest`` is the canonical SHA-256 of
the normalized client input; the same key with different input is a
conflict, not a replay.

States:

``pending``
    Claimed. The submission has not been confirmed started or refused.
``accepted``
    The turn's worker started; ``job_id`` / ``request_id`` / ``execution_id``
    / ``branch_id`` and the frozen model binding name it.
``refused``
    The turn was not started and will not be; ``refusal_code`` says why.
``indeterminate``
    Never written. :func:`effective_state` reports it for a row a previous
    daemon left ``pending`` with a ticket correlated, because nothing durable
    says whether that worker started.

The constructor is passive. DDL is applied by the numbered Store migration.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from typing import Any, Callable, Mapping

from openai4s.storage.migrations import apply_ddl_script

PENDING = "pending"
ACCEPTED = "accepted"
REFUSED = "refused"
INDETERMINATE = "indeterminate"
STATES = frozenset({PENDING, ACCEPTED, REFUSED, INDETERMINATE})

#: Exactly 32 lowercase hex characters: 128 bits from the client's CSPRNG.
CLIENT_REQUEST_ID = re.compile(r"[0-9a-f]{32}")

#: Bounds on what a row may hold. Every id here is server-minted and short; a
#: longer value is a bug upstream, refused rather than truncated, because a
#: truncated correlation id names a different (or no) execution.
MAX_ID_CHARS = 128
MAX_CODE_CHARS = 64

#: Written when a previous daemon claimed a key and stopped before it
#: correlated a ticket: such a worker never started (correlation is durable
#: before `Thread.start`), so the submission was not admitted.
NOT_STARTED_CODE = "daemon_restarted_before_admission"

RECEIPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_admissions (
    root_frame_id TEXT NOT NULL,
    initiator TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'pending','accepted','refused','indeterminate'
    )),
    daemon_instance TEXT NOT NULL,
    job_id TEXT,
    request_id TEXT,
    execution_id TEXT,
    branch_id TEXT,
    model_profile_id TEXT,
    model_profile_revision INTEGER,
    refusal_code TEXT,
    refusal_status INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (root_frame_id, initiator, client_request_id)
);
"""


def create_message_admissions_schema(conn: sqlite3.Connection) -> None:
    """Idempotent DDL, called from the numbered Store migration."""
    apply_ddl_script(conn, RECEIPT_SCHEMA)


def request_digest(
    *,
    request: str,
    mentions: list[dict] | None,
    model: Any,
    plan: bool,
    explore: bool,
    task_mode: Any,
    attachments: list[dict] | None = None,
) -> str:
    """Canonical SHA-256 of the normalized client input.

    Only what the client sent, after the route normalized it: the request
    text, the parsed `@` pins, the uploads it names as going with it, the
    explicit model and the task options. Not the resolved model, branch or
    anything the server derives, so a replay after a re-pin or a branch switch
    still matches its first send.
    """
    payload = {
        "request": str(request or ""),
        "mentions": [
            {str(key): str(value) for key, value in sorted(dict(item).items())}
            for item in (mentions or [])
        ],
        "attachments": [
            {str(key): str(value) for key, value in sorted(dict(item).items())}
            for item in (attachments or [])
        ],
        "model": None if model in (None, "") else str(model),
        "plan": bool(plan),
        "explore": bool(explore),
        "task_mode": (
            None
            if task_mode is None or not str(task_mode).strip()
            else str(task_mode).strip()
        ),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded(name: str, value: Any, limit: int = MAX_ID_CHARS) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    if len(text) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return text


class LocalOutcomes:
    """What this daemon knows about its own receipts that the row may not say.

    The request that claimed a key registers it here and records the outcome
    it observed -- worker started, or not -- so a reader is not misled when
    the durable state write that should follow fails. An entry is dropped as
    soon as the row itself carries a terminal state; what remains is only
    in-flight claims and failed writes, so the map stays tiny.

    Per process on purpose: a restarted daemon starts empty, which is what
    makes a previous daemon's ``pending`` row recognisable as such.
    """

    IN_FLIGHT = "in_flight"
    STARTED = "started"
    NOT_STARTED = "not_started"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str, str], dict[str, Any]] = {}

    def begin(self, key: tuple[str, str, str]) -> None:
        with self._lock:
            self._entries[key] = {"phase": self.IN_FLIGHT}

    def record(self, key: tuple[str, str, str], phase: str, **facts: Any) -> None:
        with self._lock:
            self._entries[key] = {"phase": phase, **facts}

    def settle(self, key: tuple[str, str, str]) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def get(self, key: tuple[str, str, str]) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            return dict(entry) if entry is not None else None


def effective_state(
    row: Mapping[str, Any],
    *,
    current_instance: str,
    local: Mapping[str, Any] | None = None,
) -> tuple[str, str | None]:
    """``(state, refusal_code)`` a reader is told. Pure; it never writes.

    Terminal rows pass through. A ``pending`` row is resolved from, in order:
    this daemon's own record of the request; whether this daemon wrote the
    row at all; and, for a previous daemon's row, whether a ticket was ever
    correlated. Correlation is durable before the worker thread starts, so a
    row without one was never started -- and a row with one, from a daemon
    that is gone, is the one case nothing here can settle.
    """
    state = str(row.get("state") or "")
    code = row.get("refusal_code")
    if state != PENDING:
        return state, code
    if local is not None:
        phase = local.get("phase")
        if phase == LocalOutcomes.STARTED:
            return ACCEPTED, None
        if phase == LocalOutcomes.NOT_STARTED:
            return REFUSED, local.get("code")
        return PENDING, None
    if str(row.get("daemon_instance") or "") == str(current_instance or ""):
        # This daemon's row, between the claim and the moment the claiming
        # request registered it: still in flight.
        return PENDING, None
    if not row.get("job_id"):
        return REFUSED, NOT_STARTED_CODE
    return INDETERMINATE, None


_KEY_SQL = "root_frame_id=? AND initiator=? AND client_request_id=?"


class MessageAdmissionRepository:
    """One receipt per (session root, initiator, client id).

    Shares ``Store``'s connection and re-entrant lock. Every transition is a
    compare-and-set on ``state='pending'``, so nothing that happens later --
    a late correlation, a retry, a sweep -- rewrites an outcome once one is
    recorded.
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

    @staticmethod
    def _key(
        root_frame_id: str, initiator: str, client_request_id: str
    ) -> tuple[str, str, str]:
        if not isinstance(root_frame_id, str) or not root_frame_id:
            raise ValueError("root_frame_id must be a non-empty string")
        if not isinstance(initiator, str) or len(initiator) > MAX_ID_CHARS:
            raise ValueError("initiator must be a string of at most 128 characters")
        if not isinstance(client_request_id, str) or not CLIENT_REQUEST_ID.fullmatch(
            client_request_id
        ):
            raise ValueError("client_request_id must be 32 lowercase hex characters")
        return (
            _bounded("root_frame_id", root_frame_id) or "",
            initiator,
            client_request_id,
        )

    def claim(
        self,
        *,
        root_frame_id: str,
        initiator: str,
        client_request_id: str,
        request_digest: str,
        daemon_instance: str,
    ) -> tuple[bool, dict]:
        """Insert the ``pending`` row, or return the one already there.

        ``(True, row)`` for the request that claimed the key; ``(False, row)``
        for every other. One write transaction, so two connections racing for
        one key cannot both see themselves as the claimer.
        """
        key = self._key(root_frame_id, initiator, client_request_id)
        digest = _bounded("request_digest", request_digest, 64)
        if not digest:
            raise ValueError("request_digest is required")
        instance = _bounded("daemon_instance", daemon_instance)
        if not instance:
            raise ValueError("daemon_instance is required")
        now = self._clock_ms()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    "INSERT OR IGNORE INTO message_admissions(root_frame_id,"
                    "initiator,client_request_id,request_digest,state,"
                    "daemon_instance,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (*key, digest, PENDING, instance, now, now),
                )
                created = bool(cursor.rowcount)
                row = self._connection.execute(
                    f"SELECT * FROM message_admissions WHERE {_KEY_SQL}", key
                ).fetchone()
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        return created, dict(row)

    def correlate(
        self,
        *,
        root_frame_id: str,
        initiator: str,
        client_request_id: str,
        job_id: str,
        request_id: str,
        execution_id: str | None,
        branch_id: str | None,
        model_profile_id: str | None,
        model_profile_revision: int | None,
    ) -> bool:
        """Name the ticket a still-pending receipt belongs to.

        Written before the worker starts; a False return or a raise aborts
        that start. Once only: a row already correlated is not re-pointed.
        """
        key = self._key(root_frame_id, initiator, client_request_id)
        values = (
            _bounded("job_id", job_id),
            _bounded("request_id", request_id),
            _bounded("execution_id", execution_id),
            _bounded("branch_id", branch_id),
            _bounded("model_profile_id", model_profile_id),
            int(model_profile_revision or 0),
        )
        if not values[0]:
            raise ValueError("job_id is required")
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE message_admissions SET job_id=?, request_id=?, "
                "execution_id=?, branch_id=?, model_profile_id=?, "
                "model_profile_revision=?, updated_at=? "
                f"WHERE {_KEY_SQL} AND state='pending' AND job_id IS NULL",
                (*values, self._clock_ms(), *key),
            )
            self._connection.commit()
            return bool(cursor.rowcount)

    def accept(
        self,
        *,
        root_frame_id: str,
        initiator: str,
        client_request_id: str,
        job_id: str,
    ) -> bool:
        """The worker for exactly this job started."""
        key = self._key(root_frame_id, initiator, client_request_id)
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE message_admissions SET state='accepted', updated_at=? "
                f"WHERE {_KEY_SQL} AND state='pending' AND job_id=?",
                (self._clock_ms(), *key, job_id),
            )
            self._connection.commit()
            return bool(cursor.rowcount)

    def refuse(
        self,
        *,
        root_frame_id: str,
        initiator: str,
        client_request_id: str,
        code: str,
        status: int | None = None,
    ) -> bool:
        """The turn was not started. Its correlation goes with it.

        A refused row keeping a job and an execution id would send a client
        looking for an execution that never ran, so they are cleared in the
        same statement that records the refusal.
        """
        key = self._key(root_frame_id, initiator, client_request_id)
        refusal = str(code or "refused")[:MAX_CODE_CHARS]
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE message_admissions SET state='refused', refusal_code=?, "
                "refusal_status=?, job_id=NULL, request_id=NULL, "
                "execution_id=NULL, branch_id=NULL, model_profile_id=NULL, "
                "model_profile_revision=NULL, updated_at=? "
                f"WHERE {_KEY_SQL} AND state='pending'",
                (
                    refusal,
                    int(status) if status is not None else None,
                    self._clock_ms(),
                    *key,
                ),
            )
            self._connection.commit()
            return bool(cursor.rowcount)

    def get(
        self, *, root_frame_id: str, initiator: str, client_request_id: str
    ) -> dict | None:
        key = self._key(root_frame_id, initiator, client_request_id)
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM message_admissions WHERE {_KEY_SQL}", key
            ).fetchone()
        return dict(row) if row else None


__all__ = [
    "ACCEPTED",
    "CLIENT_REQUEST_ID",
    "INDETERMINATE",
    "LocalOutcomes",
    "MessageAdmissionRepository",
    "NOT_STARTED_CODE",
    "PENDING",
    "REFUSED",
    "STATES",
    "create_message_admissions_schema",
    "effective_state",
    "request_digest",
]

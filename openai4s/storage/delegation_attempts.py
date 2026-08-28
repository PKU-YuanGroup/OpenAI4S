"""Durable delegation request identity and per-attempt rows.

Request identity is ``(root_frame_id, parent_action_group_id, native_call_id)``.
Attempt identity is ``(request_id, attempt_no)``. Same key + same digest reuses
the original child and never charges the session spawn budget again; same key
with a different digest is a conflict. A new attempt is created only by an
explicit continue, never by restore.

The constructor is passive. ``create_delegation_request_schema`` is invoked
only by the numbered Store migration so a failed upgrade cannot leave half a
schema advertised as current.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Callable

_LIVE_ATTEMPT_STATES = frozenset({"pending", "running"})
_ATTEMPT_STATES = _LIVE_ATTEMPT_STATES | frozenset({"done", "failed", "stopped"})

_DIGEST_KEYS = (
    "request",
    "task",
    "name",
    "context_summary",
    "output_schema",
    "model",
    "provider",
    "steps",
    "max_steps",
    "max_turns",
    "permissions",
    "capabilities",
    "skill_names",
    "connectors",
    "unrestricted",
    "require_artifacts",
    "retries",
)

DELEGATION_REQUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS delegation_requests (
    request_id TEXT PRIMARY KEY,
    root_frame_id TEXT NOT NULL,
    parent_action_group_id TEXT NOT NULL,
    native_call_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    child_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(root_frame_id, parent_action_group_id, native_call_id)
);
CREATE INDEX IF NOT EXISTS ix_delegation_requests_child
    ON delegation_requests(root_frame_id, child_id);
CREATE TABLE IF NOT EXISTS delegation_attempts (
    attempt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    previous_attempt_id TEXT,
    child_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending','running','done','failed','stopped')
    ),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    artifact_refs_json TEXT,
    UNIQUE(request_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS ix_delegation_attempts_request
    ON delegation_attempts(request_id, attempt_no);
CREATE INDEX IF NOT EXISTS ix_delegation_attempts_child
    ON delegation_attempts(child_id);
"""


class DelegationRequestConflict(RuntimeError):
    """Same durable key, different request digest — HTTP 409."""

    http_status = 409


def canonical_request_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Identity-relevant fields of one delegate spec, omitting wait/identity."""

    return {
        key: spec[key] for key in _DIGEST_KEYS if key in spec and spec[key] is not None
    }


def request_sha256(spec: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        canonical_request_payload(spec),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def delegation_identity(spec: Mapping[str, Any]) -> tuple[str, str] | None:
    group = str(spec.get("parent_action_group_id") or "").strip()
    call = str(spec.get("native_call_id") or "").strip()
    if group and call:
        return group, call
    return None


def encode_payload(spec: Mapping[str, Any]) -> str:
    return json.dumps(
        canonical_request_payload(spec),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def decode_payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def encode_artifact_refs(refs: Sequence[Mapping[str, Any]] | None) -> str | None:
    if not refs:
        return None
    cleaned = []
    for item in refs:
        if not isinstance(item, Mapping):
            continue
        cleaned.append(
            {
                "artifact_id": str(item.get("artifact_id") or ""),
                "version_id": str(item.get("version_id") or ""),
                "filename": str(item.get("filename") or ""),
                "checksum": str(item.get("checksum") or ""),
                "frame_id": str(item.get("frame_id") or ""),
            }
        )
    return json.dumps(
        cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def decode_artifact_refs(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def create_delegation_request_schema(connection: sqlite3.Connection) -> None:
    """Install the request/attempt tables without committing the caller."""

    from openai4s.storage.migrations import apply_ddl_script

    apply_ddl_script(connection, DELEGATION_REQUEST_SCHEMA)


def new_request_id() -> str:
    return f"dreq-{uuid.uuid4().hex[:16]}"


def new_attempt_id() -> str:
    return f"datm-{uuid.uuid4().hex[:16]}"


class DelegationAttemptRepository:
    """Own request/attempt rows. Caller holds the Store lock for writes."""

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

    def lookup_locked(
        self,
        *,
        root_frame_id: str,
        parent_action_group_id: str,
        native_call_id: str,
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM delegation_requests WHERE root_frame_id=? "
            "AND parent_action_group_id=? AND native_call_id=?",
            (root_frame_id, parent_action_group_id, native_call_id),
        ).fetchone()
        return self._request_from(row) if row is not None else None

    def request_by_id_locked(self, request_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM delegation_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        return self._request_from(row) if row is not None else None

    def request_for_child_locked(
        self, *, root_frame_id: str, child_id: str
    ) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT r.* FROM delegation_requests r "
            "WHERE r.root_frame_id=? AND r.child_id=? "
            "UNION "
            "SELECT r.* FROM delegation_requests r "
            "JOIN delegation_attempts a ON a.request_id=r.request_id "
            "WHERE r.root_frame_id=? AND a.child_id=? "
            "LIMIT 1",
            (root_frame_id, child_id, root_frame_id, child_id),
        ).fetchone()
        return self._request_from(row) if row is not None else None

    def insert_request_locked(
        self,
        *,
        root_frame_id: str,
        parent_action_group_id: str,
        native_call_id: str,
        request_sha256: str,
        child_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = new_request_id()
        now_ms = self._clock_ms()
        self._connection.execute(
            "INSERT INTO delegation_requests("
            "request_id,root_frame_id,parent_action_group_id,native_call_id,"
            "request_sha256,child_id,created_at,payload_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                request_id,
                root_frame_id,
                parent_action_group_id,
                native_call_id,
                request_sha256,
                child_id,
                now_ms,
                encode_payload(payload or {}),
            ),
        )
        return {
            "request_id": request_id,
            "root_frame_id": root_frame_id,
            "parent_action_group_id": parent_action_group_id,
            "native_call_id": native_call_id,
            "request_sha256": request_sha256,
            "child_id": child_id,
            "created_at": now_ms,
            "payload_json": encode_payload(payload or {}),
        }

    def insert_attempt_locked(
        self,
        *,
        request_id: str,
        child_id: str,
        previous_attempt_id: str | None = None,
        state: str = "pending",
    ) -> dict[str, Any]:
        if state not in _ATTEMPT_STATES:
            raise ValueError(f"unknown delegation attempt state: {state!r}")
        latest = self.latest_attempt_locked(request_id)
        attempt_no = (int(latest["attempt_no"]) + 1) if latest else 1
        attempt_id = new_attempt_id()
        now_ms = self._clock_ms()
        previous = previous_attempt_id or (latest["attempt_id"] if latest else None)
        self._connection.execute(
            "INSERT INTO delegation_attempts("
            "attempt_id,request_id,attempt_no,previous_attempt_id,child_id,"
            "state,created_at,updated_at,artifact_refs_json) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                attempt_id,
                request_id,
                attempt_no,
                previous,
                child_id,
                state,
                now_ms,
                now_ms,
                None,
            ),
        )
        return {
            "attempt_id": attempt_id,
            "request_id": request_id,
            "attempt_no": attempt_no,
            "previous_attempt_id": previous,
            "child_id": child_id,
            "state": state,
            "created_at": now_ms,
            "updated_at": now_ms,
            "artifact_refs": [],
        }

    def latest_attempt_locked(self, request_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM delegation_attempts WHERE request_id=? "
            "ORDER BY attempt_no DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        return self._attempt_from(row) if row is not None else None

    def attempt_for_child_locked(self, child_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM delegation_attempts WHERE child_id=? "
            "ORDER BY attempt_no DESC LIMIT 1",
            (child_id,),
        ).fetchone()
        return self._attempt_from(row) if row is not None else None

    def update_attempt_state_locked(
        self,
        child_id: str,
        state: str,
        *,
        artifact_refs: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        if state not in _ATTEMPT_STATES:
            return
        refs = encode_artifact_refs(artifact_refs)
        if refs is not None:
            self._connection.execute(
                "UPDATE delegation_attempts SET state=?,updated_at=?,"
                "artifact_refs_json=? WHERE child_id=? AND state IN "
                "('pending','running')",
                (state, self._clock_ms(), refs, child_id),
            )
            # Terminal reuse / late persist of refs on an already-terminal row.
            if state in {"done", "failed", "stopped"}:
                self._connection.execute(
                    "UPDATE delegation_attempts SET artifact_refs_json=?,"
                    "updated_at=? WHERE child_id=? AND artifact_refs_json IS NULL",
                    (refs, self._clock_ms(), child_id),
                )
            return
        self._connection.execute(
            "UPDATE delegation_attempts SET state=?,updated_at=? "
            "WHERE child_id=? AND state IN ('pending','running')",
            (state, self._clock_ms(), child_id),
        )

    def stop_live_attempts_locked(self, child_ids: Sequence[str]) -> None:
        ids = [str(item) for item in child_ids if item]
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        self._connection.execute(
            f"UPDATE delegation_attempts SET state='stopped',updated_at=? "
            f"WHERE child_id IN ({marks}) AND state IN ('pending','running')",
            (self._clock_ms(), *ids),
        )

    def project_for_child_locked(
        self, *, root_frame_id: str, child_id: str
    ) -> dict[str, Any]:
        request = self.request_for_child_locked(
            root_frame_id=root_frame_id, child_id=child_id
        )
        attempt = self.attempt_for_child_locked(child_id)
        return {
            "request_id": request["request_id"] if request else None,
            "attempt_id": attempt["attempt_id"] if attempt else None,
            "artifact_refs": (attempt or {}).get("artifact_refs") or [],
        }

    @staticmethod
    def _request_from(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "request_id": row["request_id"],
            "root_frame_id": row["root_frame_id"],
            "parent_action_group_id": row["parent_action_group_id"],
            "native_call_id": row["native_call_id"],
            "request_sha256": row["request_sha256"],
            "child_id": row["child_id"],
            "created_at": row["created_at"],
            "payload_json": row["payload_json"],
            "payload": decode_payload(row["payload_json"]),
        }

    @staticmethod
    def _attempt_from(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "attempt_id": row["attempt_id"],
            "request_id": row["request_id"],
            "attempt_no": int(row["attempt_no"]),
            "previous_attempt_id": row["previous_attempt_id"],
            "child_id": row["child_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "artifact_refs": decode_artifact_refs(row["artifact_refs_json"]),
        }


__all__ = [
    "DELEGATION_REQUEST_SCHEMA",
    "DelegationAttemptRepository",
    "DelegationRequestConflict",
    "canonical_request_payload",
    "create_delegation_request_schema",
    "decode_payload",
    "delegation_identity",
    "encode_payload",
    "request_sha256",
]

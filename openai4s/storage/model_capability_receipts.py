"""Durable model-capability receipts from an explicit probe.

A receipt is evidence about one exact configuration: profile revision,
normalized endpoint, model, wire, and probe version.  It is not a guess from
the adapter catalogue.  Three-state fields use the strings ``true`` /
``false`` / ``unknown``; ``true`` is stored only when a probe observed a
schema-valid native tool call or a fully terminated stream.  Timeout, auth
failure, 5xx, and an uncooperative model are ``unknown``.  ``false`` is
reserved for stable, protocol-level unsupported evidence.

The constructor is passive.  DDL is applied by the numbered Store migration.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any, Callable, Mapping

from openai4s.storage.migrations import apply_ddl_script

EVIDENCE_TRUE = "true"
EVIDENCE_FALSE = "false"
EVIDENCE_UNKNOWN = "unknown"
EVIDENCE_STATES = frozenset({EVIDENCE_TRUE, EVIDENCE_FALSE, EVIDENCE_UNKNOWN})

RECEIPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_capability_receipts (
    receipt_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    endpoint_sha256 TEXT NOT NULL,
    model TEXT NOT NULL,
    wire TEXT NOT NULL,
    probe_version INTEGER NOT NULL,
    reachable INTEGER NOT NULL CHECK(reachable IN (0, 1)),
    native_tool_call TEXT NOT NULL CHECK(native_tool_call IN ('true','false','unknown')),
    streaming TEXT NOT NULL CHECK(streaming IN ('true','false','unknown')),
    context_window_tokens INTEGER,
    max_output_tokens INTEGER,
    observed_at INTEGER NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    UNIQUE(profile_id, revision, endpoint_sha256, model, wire, probe_version)
);
CREATE INDEX IF NOT EXISTS ix_mcr_profile
    ON model_capability_receipts(profile_id, revision);
"""


def create_model_capability_receipts_schema(conn: sqlite3.Connection) -> None:
    """Idempotent DDL, called from the numbered Store migration."""
    apply_ddl_script(conn, RECEIPT_SCHEMA)


def _evidence(value: Any, *, default: str = EVIDENCE_UNKNOWN) -> str:
    text = str(value or default).strip().lower()
    if text in {"1", "yes"}:
        text = EVIDENCE_TRUE
    if text in {"0", "no"}:
        text = EVIDENCE_FALSE
    if text not in EVIDENCE_STATES:
        return default
    return text


def receipt_digest(fields: Mapping[str, Any]) -> str:
    """Canonical SHA-256 of the evidence identity, excluding observed_at."""
    payload = {
        "profile_id": str(fields.get("profile_id") or ""),
        "revision": int(fields.get("revision") or 0),
        "endpoint_sha256": str(fields.get("endpoint_sha256") or ""),
        "model": str(fields.get("model") or ""),
        "wire": str(fields.get("wire") or ""),
        "probe_version": int(fields.get("probe_version") or 0),
        "reachable": int(bool(fields.get("reachable"))),
        "native_tool_call": _evidence(fields.get("native_tool_call")),
        "streaming": _evidence(fields.get("streaming")),
        "context_window_tokens": fields.get("context_window_tokens"),
        "max_output_tokens": fields.get("max_output_tokens"),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def public_receipt(
    row: Mapping[str, Any] | None, *, stale: bool, probe_version: int
) -> dict[str, Any] | None:
    """Secret-free projection.  Keys never belong on a receipt."""
    if not row:
        return None
    native = _evidence(row.get("native_tool_call"))
    streaming = _evidence(row.get("streaming"))
    current_probe = int(row.get("probe_version") or 0) == int(probe_version)
    is_stale = bool(stale) or not current_probe
    return {
        "receipt_id": str(row.get("receipt_id") or ""),
        "profile_id": str(row.get("profile_id") or ""),
        "revision": int(row.get("revision") or 0),
        "endpoint_sha256": str(row.get("endpoint_sha256") or ""),
        "model": str(row.get("model") or ""),
        "wire": str(row.get("wire") or ""),
        "probe_version": int(row.get("probe_version") or 0),
        "reachable": bool(row.get("reachable")),
        "native_tool_call": native,
        "streaming": streaming,
        "context_window_tokens": row.get("context_window_tokens"),
        "max_output_tokens": row.get("max_output_tokens"),
        "observed_at": int(row.get("observed_at") or 0),
        "receipt_sha256": str(row.get("receipt_sha256") or ""),
        "stale": is_stale,
        # Native completion is enabled only by positive, current evidence.
        "native_completion": (not is_stale) and native == EVIDENCE_TRUE,
    }


class ModelCapabilityReceiptRepository:
    """Exact receipts over the Store's connection.  No network."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._lock = lock
        self._clock_ms = clock_ms
        self._id_factory = id_factory or (lambda: "mcr-" + uuid.uuid4().hex)

    def put(
        self,
        *,
        profile_id: str,
        revision: int,
        endpoint_sha256: str,
        model: str,
        wire: str,
        probe_version: int,
        reachable: bool,
        native_tool_call: str,
        streaming: str,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        observed_at: int | None = None,
    ) -> dict[str, Any]:
        profile_id = str(profile_id or "").strip()
        model = str(model or "").strip()
        wire = str(wire or "").strip().lower()
        endpoint_sha256 = str(endpoint_sha256 or "").strip().lower()
        if not profile_id or not model or not wire or not endpoint_sha256:
            raise ValueError("receipt identity fields are required")
        native = _evidence(native_tool_call)
        stream = _evidence(streaming)
        now = int(observed_at if observed_at is not None else self._clock_ms())
        fields = {
            "profile_id": profile_id,
            "revision": int(revision),
            "endpoint_sha256": endpoint_sha256,
            "model": model,
            "wire": wire,
            "probe_version": int(probe_version),
            "reachable": 1 if reachable else 0,
            "native_tool_call": native,
            "streaming": stream,
            "context_window_tokens": context_window_tokens,
            "max_output_tokens": max_output_tokens,
        }
        digest = receipt_digest(fields)
        receipt_id = self._id_factory()
        with self._lock:
            existing = self._conn.execute(
                "SELECT receipt_id FROM model_capability_receipts "
                "WHERE profile_id=? AND revision=? AND endpoint_sha256=? "
                "AND model=? AND wire=? AND probe_version=?",
                (
                    profile_id,
                    int(revision),
                    endpoint_sha256,
                    model,
                    wire,
                    int(probe_version),
                ),
            ).fetchone()
            if existing:
                receipt_id = str(existing[0])
                self._conn.execute(
                    "UPDATE model_capability_receipts SET reachable=?,"
                    "native_tool_call=?, streaming=?, context_window_tokens=?,"
                    "max_output_tokens=?, observed_at=?, receipt_sha256=? "
                    "WHERE receipt_id=?",
                    (
                        fields["reachable"],
                        native,
                        stream,
                        context_window_tokens,
                        max_output_tokens,
                        now,
                        digest,
                        receipt_id,
                    ),
                )
            else:
                self._conn.execute(
                    "INSERT INTO model_capability_receipts("
                    "receipt_id, profile_id, revision, endpoint_sha256, model,"
                    "wire, probe_version, reachable, native_tool_call,"
                    "streaming, context_window_tokens, max_output_tokens,"
                    "observed_at, receipt_sha256) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        receipt_id,
                        profile_id,
                        int(revision),
                        endpoint_sha256,
                        model,
                        wire,
                        int(probe_version),
                        fields["reachable"],
                        native,
                        stream,
                        context_window_tokens,
                        max_output_tokens,
                        now,
                        digest,
                    ),
                )
            self._conn.commit()
        return self.get(receipt_id) or {
            "receipt_id": receipt_id,
            **fields,
            "observed_at": now,
            "receipt_sha256": digest,
        }

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_id, profile_id, revision, endpoint_sha256, model,"
                "wire, probe_version, reachable, native_tool_call, streaming,"
                "context_window_tokens, max_output_tokens, observed_at,"
                "receipt_sha256 FROM model_capability_receipts "
                "WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
        return self._row(row)

    def get_exact(
        self,
        *,
        profile_id: str,
        revision: int,
        endpoint_sha256: str,
        model: str,
        wire: str,
        probe_version: int,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_id, profile_id, revision, endpoint_sha256, model,"
                "wire, probe_version, reachable, native_tool_call, streaming,"
                "context_window_tokens, max_output_tokens, observed_at,"
                "receipt_sha256 FROM model_capability_receipts "
                "WHERE profile_id=? AND revision=? AND endpoint_sha256=? "
                "AND model=? AND wire=? AND probe_version=?",
                (
                    str(profile_id or ""),
                    int(revision),
                    str(endpoint_sha256 or "").lower(),
                    str(model or ""),
                    str(wire or "").lower(),
                    int(probe_version),
                ),
            ).fetchone()
        return self._row(row)

    def latest_for_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT receipt_id, profile_id, revision, endpoint_sha256, model,"
                "wire, probe_version, reachable, native_tool_call, streaming,"
                "context_window_tokens, max_output_tokens, observed_at,"
                "receipt_sha256 FROM model_capability_receipts "
                "WHERE profile_id=? ORDER BY observed_at DESC, revision DESC "
                "LIMIT 1",
                (str(profile_id or ""),),
            ).fetchone()
        return self._row(row)

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT receipt_id, profile_id, revision, endpoint_sha256, model,"
                "wire, probe_version, reachable, native_tool_call, streaming,"
                "context_window_tokens, max_output_tokens, observed_at,"
                "receipt_sha256 FROM model_capability_receipts "
                "ORDER BY observed_at DESC"
            ).fetchall()
        return [item for item in (self._row(row) for row in rows) if item is not None]

    @staticmethod
    def _row(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "receipt_id": row[0],
            "profile_id": row[1],
            "revision": int(row[2] or 0),
            "endpoint_sha256": row[3],
            "model": row[4],
            "wire": row[5],
            "probe_version": int(row[6] or 0),
            "reachable": bool(row[7]),
            "native_tool_call": row[8],
            "streaming": row[9],
            "context_window_tokens": row[10],
            "max_output_tokens": row[11],
            "observed_at": int(row[12] or 0),
            "receipt_sha256": row[13],
        }


__all__ = [
    "EVIDENCE_FALSE",
    "EVIDENCE_STATES",
    "EVIDENCE_TRUE",
    "EVIDENCE_UNKNOWN",
    "ModelCapabilityReceiptRepository",
    "create_model_capability_receipts_schema",
    "public_receipt",
    "receipt_digest",
]

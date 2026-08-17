"""Durable observations of Artifact captures that reuse versioned bytes.

An Artifact version answers "which bytes?".  A capture observation answers the
separate question "which Cell produced or observed those bytes this time?".
Keeping those facts in separate rows lets identical bytes reuse the current
version without rewriting its original producer, environment, or source.

Schema installation belongs to the Store's numbered migration.  The helper in
this module deliberately does not commit so the migration remains atomic.
Repository writes likewise join the caller's artifact-recording transaction.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from openai4s.storage.migrations import apply_ddl_script

CAPTURE_KIND_VERSION_CREATED = "version_created"
CAPTURE_KIND_SAME_CELL_MERGE = "same_cell_merge"
CAPTURE_KIND_HEAD_CHECKSUM_REUSED = "head_checksum_reused"
CAPTURE_KINDS = frozenset(
    {
        CAPTURE_KIND_VERSION_CREATED,
        CAPTURE_KIND_SAME_CELL_MERGE,
        CAPTURE_KIND_HEAD_CHECKSUM_REUSED,
    }
)

# One turn may legitimately save a large result set.  Reads are still bounded
# so a corrupted/hostile producer cannot make finalization allocate without
# limit; the repository asks for one extra row and fails closed rather than
# silently omitting observations beyond this boundary.
MAX_DELIVERY_OBSERVATIONS = 10_000


ARTIFACT_OBSERVATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_capture_observations (
    ordinal               INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id        TEXT NOT NULL UNIQUE,
    artifact_id           TEXT NOT NULL,
    version_id            TEXT NOT NULL,
    producing_cell_id     TEXT NOT NULL,
    frame_id              TEXT,
    capture_kind          TEXT NOT NULL CHECK (
        capture_kind IN (
            'version_created',
            'same_cell_merge',
            'head_checksum_reused'
        )
    ),
    filename              TEXT NOT NULL,
    content_type          TEXT,
    size_bytes            INTEGER,
    checksum              TEXT,
    path                  TEXT NOT NULL,
    snapshot_path         TEXT,
    env_snapshot_id       TEXT,
    source                TEXT,
    input_version_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
        ON DELETE CASCADE,
    FOREIGN KEY (version_id) REFERENCES artifact_versions(version_id)
        ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_artifact_capture_observation_producer
ON artifact_capture_observations(
    version_id,
    producing_cell_id
);
CREATE INDEX IF NOT EXISTS ix_artifact_capture_observations_artifact
ON artifact_capture_observations(artifact_id, ordinal);
CREATE INDEX IF NOT EXISTS ix_artifact_capture_observations_version
ON artifact_capture_observations(version_id, ordinal);
"""


def create_artifact_observations_schema(conn: sqlite3.Connection) -> None:
    """Install the additive observation tables inside the caller's transaction."""
    apply_ddl_script(conn, ARTIFACT_OBSERVATIONS_SCHEMA)


def _decode_input_ids(raw: Any) -> list[str]:
    try:
        decoded = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [value for value in decoded if isinstance(value, str) and value]


def _merge_input_ids(current: Any, incoming: list[Any]) -> str:
    """Return a canonical, order-preserving union.

    Values are intentionally not coerced to strings.  A malformed lineage id
    must fail the encompassing record transaction rather than become a
    plausible-looking identifier in the audit trail.
    """
    merged: list[Any] = list(_decode_input_ids(current))
    seen: set[Any] = set(merged)
    for value in incoming:
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))


class ArtifactObservationRepository:
    """Write and read per-Cell capture observations on a shared connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def upsert(
        self,
        *,
        artifact_id: str,
        version_id: str,
        producing_cell_id: str,
        frame_id: str | None,
        capture_kind: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        checksum: str | None,
        path: str,
        snapshot_path: str | None,
        env_snapshot_id: str | None,
        source: str | None,
        input_version_ids: list[Any],
        now: int,
    ) -> dict:
        """Create or merge one producer/version observation without committing."""
        if capture_kind not in CAPTURE_KINDS:
            raise ValueError(f"unknown artifact capture kind: {capture_kind!r}")
        existing = self._connection.execute(
            "SELECT * FROM artifact_capture_observations "
            "WHERE version_id=? AND producing_cell_id=?",
            (version_id, producing_cell_id),
        ).fetchone()
        if existing is None:
            observation_id = f"aco-{uuid.uuid4().hex[:16]}"
            input_json = _merge_input_ids(None, input_version_ids)
            cursor = self._connection.execute(
                "INSERT INTO artifact_capture_observations("
                "observation_id,artifact_id,version_id,producing_cell_id,"
                "frame_id,capture_kind,filename,content_type,size_bytes,"
                "checksum,path,snapshot_path,env_snapshot_id,source,"
                "input_version_ids_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    observation_id,
                    artifact_id,
                    version_id,
                    producing_cell_id,
                    frame_id,
                    capture_kind,
                    filename,
                    content_type,
                    size_bytes,
                    checksum,
                    path,
                    snapshot_path,
                    env_snapshot_id,
                    source,
                    input_json,
                    now,
                    now,
                ),
            )
            ordinal = int(cursor.lastrowid)
        else:
            observation_id = str(existing["observation_id"])
            ordinal = int(existing["ordinal"])
            input_json = _merge_input_ids(
                existing["input_version_ids_json"], input_version_ids
            )
            self._connection.execute(
                "UPDATE artifact_capture_observations SET capture_kind=?,"
                "filename=?,content_type=COALESCE(?,content_type),size_bytes=?,"
                "checksum=?,path=?,snapshot_path=COALESCE(snapshot_path,?),"
                "env_snapshot_id=COALESCE(env_snapshot_id,?),"
                "source=COALESCE(source,?),input_version_ids_json=?,updated_at=? "
                "WHERE observation_id=?",
                (
                    capture_kind,
                    filename,
                    content_type,
                    size_bytes,
                    checksum,
                    path,
                    snapshot_path,
                    env_snapshot_id,
                    source,
                    input_json,
                    now,
                    observation_id,
                ),
            )
        return {
            "observation_id": observation_id,
            "ordinal": ordinal,
        }

    def list(
        self,
        *,
        artifact_id: str | None = None,
        version_id: str | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[str] = []
        if artifact_id is not None:
            clauses.append("artifact_id=?")
            params.append(artifact_id)
        if version_id is not None:
            clauses.append("version_id=?")
            params.append(version_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT * FROM artifact_capture_observations" + where + " ORDER BY ordinal",
            tuple(params),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            item["input_version_ids"] = _decode_input_ids(
                item.pop("input_version_ids_json", None)
            )
            result.append(item)
        return result

    def cursor(
        self,
        *,
        root_frame_id: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Return the latest visible ordinal, optionally constrained to a scope."""
        clauses: list[str] = []
        params: list[Any] = []
        if root_frame_id is not None:
            clauses.append("a.root_frame_id=?")
            params.append(root_frame_id)
        if project_id is not None:
            clauses.append("a.project_id=?")
            params.append(project_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        row = self._connection.execute(
            "SELECT COALESCE(MAX(o.ordinal),0) AS cursor "
            "FROM artifact_capture_observations o "
            "JOIN artifacts a ON a.artifact_id=o.artifact_id" + where,
            tuple(params),
        ).fetchone()
        return int(row["cursor"] if row is not None else 0)

    def since(
        self,
        cursor: int,
        *,
        root_frame_id: str | None,
        project_id: str,
        limit: int = 1000,
    ) -> list[dict]:
        """Return exact-version captures after ``cursor`` inside one scope.

        Unlike :meth:`cursor`, this read intentionally has no unscoped form:
        it is used to build a turn's delivery delta, and a global fallback
        would turn a missing context value into a cross-session disclosure.
        """
        if not project_id:
            raise ValueError("project_id is required for capture observation reads")
        bounded_limit = int(limit)
        if bounded_limit < 1:
            raise ValueError("capture observation limit must be positive")
        bounded_limit = min(bounded_limit, MAX_DELIVERY_OBSERVATIONS + 1)
        rows = self._connection.execute(
            "SELECT o.observation_id,o.ordinal AS observation_ordinal,"
            "o.capture_kind,o.artifact_id,o.version_id,o.producing_cell_id,"
            "o.frame_id,o.path AS capture_path,"
            "o.snapshot_path AS capture_snapshot_path,"
            "o.env_snapshot_id AS capture_env_snapshot_id,"
            "o.source AS capture_source,o.input_version_ids_json,"
            "o.created_at AS observation_created_at,"
            "o.updated_at AS observation_updated_at,"
            "v.filename,v.content_type,v.size_bytes,v.checksum,v.path,"
            "v.snapshot_path,v.env_snapshot_id,v.source,"
            "v.created_at AS version_created_at "
            "FROM artifact_capture_observations o "
            "JOIN artifacts a ON a.artifact_id=o.artifact_id "
            "JOIN artifact_versions v ON v.version_id=o.version_id "
            "AND v.artifact_id=o.artifact_id "
            "WHERE o.ordinal>? AND a.root_frame_id IS ? AND a.project_id=? "
            "ORDER BY o.ordinal LIMIT ?",
            (int(cursor), root_frame_id, project_id, bounded_limit),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            item["input_version_ids"] = _decode_input_ids(
                item.pop("input_version_ids_json", None)
            )
            item["version_created"] = (
                item["capture_kind"] == CAPTURE_KIND_VERSION_CREATED
            )
            result.append(item)
        return result


__all__ = [
    "ARTIFACT_OBSERVATIONS_SCHEMA",
    "ArtifactObservationRepository",
    "CAPTURE_KIND_HEAD_CHECKSUM_REUSED",
    "CAPTURE_KIND_SAME_CELL_MERGE",
    "CAPTURE_KIND_VERSION_CREATED",
    "MAX_DELIVERY_OBSERVATIONS",
    "create_artifact_observations_schema",
]

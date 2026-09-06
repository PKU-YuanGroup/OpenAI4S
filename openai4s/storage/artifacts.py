"""Artifact, version, environment, and lineage persistence.

The repository shares its owning ``Store`` connection and re-entrant lock.  A
few callbacks are deliberately late-bound by the Store facade: the legacy
methods called ``self.get_artifact()``, ``self.get_frame()``, ``self._exec()``,
and related helpers dynamically, so wiring lambdas preserves monkeypatch and
subclass behavior instead of freezing bound methods during construction.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from typing import Any, Callable

# The restore refusal type, imported rather than duplicated: these three
# raises are author-written refusals on the same restore transaction the
# service owns, and the caller distinguishes them from an OS-layer failure
# by type. `artifact_restore` imports nothing from storage, so this is a
# leaf dependency rather than a cycle.
from openai4s.artifact_restore import ArtifactRestoreRefused
from openai4s.storage.artifact_observations import (
    CAPTURE_KIND_HEAD_CHECKSUM_REUSED,
    CAPTURE_KIND_SAME_CELL_MERGE,
    CAPTURE_KIND_VERSION_CREATED,
    MAX_DELIVERY_OBSERVATIONS,
    ArtifactObservationRepository,
)
from openai4s.storage.frames import visible_session_clause
from openai4s.storage.like import like_contains

Clock = Callable[[], int]
Execute = Callable[[str, tuple], None]
GetFrame = Callable[[str], dict | None]
ResolveFrameScope = Callable[..., dict]
ResolveArtifactWriteScope = Callable[..., tuple[bool, str | None, str]]
GetArtifact = Callable[[str], dict | None]
GetEnvironmentSnapshot = Callable[[str], dict | None]
FileIdentity = Callable[[str], str | None]
SameFilePath = Callable[[str, str], bool]
DeleteArtifactRelated = Callable[[str], None]
PublishUpload = Callable[[str, str], str]


class ArtifactDeliveryReferenceError(RuntimeError):
    """A durable completion message still addresses this Artifact's bytes."""


def file_identity(path: str) -> str | None:
    """Best-effort physical identity for legacy or aliased artifact paths."""
    try:
        raw = os.fsdecode(os.fspath(path))
        return os.path.normcase(os.path.realpath(raw))
    except (TypeError, ValueError, OSError):
        return None


def same_file_path(left: str, right: str) -> bool:
    """Return whether two stored paths identify the same physical file."""
    if left == right:
        return True
    left_identity = file_identity(left)
    right_identity = file_identity(right)
    return (
        left_identity is not None
        and right_identity is not None
        and left_identity == right_identity
    )


def env_snapshot_id(
    *,
    kind: Any,
    python_version: Any,
    implementation: Any,
    platform: Any,
    interpreter: Any,
    environment_name: Any,
    generation_id: Any,
    packages_json: str,
    remote_json: str,
) -> str:
    """The content address of one environment observation.

    The interpreter and environment name are part of the identity, not
    decoration: without them an R kernel and a Python one in a conda env
    collapse onto the same row whenever their package lists happen to match --
    and which environment produced a result is precisely what provenance is
    for.

    So is the **generation**. ``upsert_env_snapshot`` never updates an existing
    row, so with the generation left out of the basis, a kernel restarted into
    an unchanged environment produced the same id — and the row already on disk
    kept naming the *first* generation. Every artifact from generation 2 then
    pointed at a snapshot recorded as generation 1, and nothing else on the
    artifact carries a generation, so there was no second source to catch it:
    the record was silently, confidently wrong about which kernel lifetime
    produced the file. Including it costs one row per kernel restart and makes
    ``generation_id`` mean what it says.

    Shared with the numbered migration that repairs legacy rows, so the two
    cannot drift.
    """
    basis = "|".join(
        [
            str(kind or ""),
            str(python_version or ""),
            str(implementation or ""),
            str(platform or ""),
            str(interpreter or ""),
            str(environment_name or ""),
            str(generation_id or ""),
            packages_json,
            remote_json,
        ]
    )
    return "env-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _encode_source(source: Any) -> str | None:
    """Store a retrieval envelope as canonical JSON, or nothing at all.

    Canonical so two versions derived from the same retrieval compare equal as
    text -- "these came from the same data" should be checkable rather than a
    matter of key ordering.
    """
    if source in (None, "", {}, []):
        return None
    if isinstance(source, str):
        return source
    try:
        return json.dumps(
            source, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        return None


class ArtifactRepository:
    """Own artifacts, versions, environment snapshots, and lineage edges."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Clock,
        get_frame: GetFrame,
        resolve_frame_scope: ResolveFrameScope,
        resolve_artifact_write_scope: ResolveArtifactWriteScope | None = None,
        execute: Execute | None = None,
        get_artifact: GetArtifact | None = None,
        get_env_snapshot: GetEnvironmentSnapshot | None = None,
        identify_file: FileIdentity | None = None,
        paths_match: SameFilePath | None = None,
        delete_related: DeleteArtifactRelated | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._get_frame = get_frame
        self._resolve_frame_scope = resolve_frame_scope
        self._resolve_artifact_write_scope = (
            resolve_artifact_write_scope or self.artifact_write_scope
        )
        self._execute_callback = execute
        self._get_artifact = get_artifact or self.get_artifact
        self._get_env_snapshot = get_env_snapshot or self.get_env_snapshot
        self._identify_file = identify_file or file_identity
        self._paths_match = paths_match or same_file_path
        self._delete_related = delete_related
        self._observations = ArtifactObservationRepository(connection)

    def get_artifact(self, artifact_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT a.*, v.size_bytes, v.checksum, v.path "
                "FROM artifacts a LEFT JOIN artifact_versions v "
                "ON a.latest_version_id=v.version_id WHERE a.artifact_id=?",
                (artifact_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_artifact(self, artifact_id: str) -> list[str]:
        """Remove an artifact and return paths no surviving version references."""
        with self._lock:
            delivered = self._connection.execute(
                "SELECT 1 FROM completion_delivery_artifacts "
                "WHERE artifact_id=? LIMIT 1",
                (artifact_id,),
            ).fetchone()
            if delivered is not None:
                raise ArtifactDeliveryReferenceError(
                    "Artifact is referenced by a durable completion message"
                )
            rows = self._connection.execute(
                "SELECT version_id,path,snapshot_path,env_snapshot_id "
                "FROM artifact_versions WHERE artifact_id=?",
                (artifact_id,),
            ).fetchall()
            observation_env_rows = self._connection.execute(
                "SELECT env_snapshot_id FROM artifact_capture_observations "
                "WHERE artifact_id=? AND env_snapshot_id IS NOT NULL",
                (artifact_id,),
            ).fetchall()
            version_ids = tuple(str(row["version_id"]) for row in rows)
            env_snapshot_ids = tuple(
                dict.fromkeys(
                    str(row["env_snapshot_id"])
                    for row in (*rows, *observation_env_rows)
                    if row["env_snapshot_id"]
                )
            )
            paths = {
                path
                for row in rows
                for path in (row["path"], row["snapshot_path"])
                if path
            }
            try:
                self._connection.execute("SAVEPOINT artifact_delete")
                if self._delete_related is not None:
                    self._delete_related(artifact_id)
                self._connection.execute(
                    "DELETE FROM artifact_capture_observations WHERE artifact_id=?",
                    (artifact_id,),
                )
                if version_ids:
                    marks = "(" + ",".join("?" for _ in version_ids) + ")"
                    self._connection.execute(
                        "DELETE FROM lineage_edges WHERE input_version_id IN "
                        f"{marks} OR output_version_id IN {marks}",
                        version_ids + version_ids,
                    )
                self._connection.execute(
                    "DELETE FROM artifact_versions WHERE artifact_id=?", (artifact_id,)
                )
                self._connection.execute(
                    "DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,)
                )
                self._connection.execute(
                    "DELETE FROM annotations WHERE artifact_id=?", (artifact_id,)
                )
                self._connection.execute(
                    "UPDATE plans SET artifact_id=NULL WHERE artifact_id=?",
                    (artifact_id,),
                )
                if env_snapshot_ids:
                    marks = "(" + ",".join("?" for _ in env_snapshot_ids) + ")"
                    self._connection.execute(
                        "DELETE FROM env_snapshots WHERE snapshot_id IN "
                        f"{marks} AND NOT EXISTS (SELECT 1 FROM artifact_versions "
                        "WHERE artifact_versions.env_snapshot_id="
                        "env_snapshots.snapshot_id) AND NOT EXISTS (SELECT 1 FROM "
                        "artifact_capture_observations WHERE "
                        "artifact_capture_observations.env_snapshot_id="
                        "env_snapshots.snapshot_id)",
                        env_snapshot_ids,
                    )
                self._connection.execute("RELEASE SAVEPOINT artifact_delete")
                self._connection.commit()
            except Exception:
                self._connection.execute("ROLLBACK TO SAVEPOINT artifact_delete")
                self._connection.execute("RELEASE SAVEPOINT artifact_delete")
                raise
            surviving_rows = self._connection.execute(
                "SELECT path,snapshot_path FROM artifact_versions"
            ).fetchall()
            surviving_paths = tuple(
                value
                for row in surviving_rows
                for value in (row["path"], row["snapshot_path"])
                if value
            )
            keep = {
                path
                for path in paths
                if any(self._paths_match(path, other) for other in surviving_paths)
            }
        return [path for path in paths if path not in keep]

    def rename_artifact(self, artifact_id: str, filename: str) -> None:
        now = self._clock_ms()
        with self._lock:
            self._connection.execute(
                "UPDATE artifacts SET filename=?, updated_at=? WHERE artifact_id=?",
                (filename, now, artifact_id),
            )
            self._connection.execute(
                "UPDATE artifact_versions SET filename=? WHERE artifact_id=?",
                (filename, artifact_id),
            )
            self._connection.commit()

    def artifact_by_filename(
        self,
        filename: str,
        root_frame_id: str | None = None,
        *,
        strict: bool = False,
    ) -> dict | None:
        with self._lock:
            if root_frame_id:
                row = self._connection.execute(
                    "SELECT artifact_id FROM artifacts WHERE filename=? AND "
                    "root_frame_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                    (filename, root_frame_id),
                ).fetchone()
                if row:
                    return self._get_artifact(row["artifact_id"])
                if strict:
                    return None
            row = self._connection.execute(
                "SELECT artifact_id FROM artifacts WHERE filename=? "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (filename,),
            ).fetchone()
        return self._get_artifact(row["artifact_id"]) if row else None

    def artifact_by_unique_filename(self, filename: str) -> dict | None:
        """Resolve a filename only when exactly one artifact carries it.

        The unscoped lookup above answers "the most recently created artifact
        with this name, anywhere" -- so `GET /artifacts/report.pdf` served
        whichever *project* last happened to make a `report.pdf`. A caller
        asking by name got an arbitrary cross-project match, with the right
        content-type and no indication anything was chosen.

        For a tool whose artifacts are research data, quietly serving the wrong
        file is worse than serving none. Two matches is an ambiguous question,
        and the honest answer to an ambiguous question is not one of the
        candidates.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT artifact_id FROM artifacts WHERE filename=? LIMIT 2",
                (filename,),
            ).fetchall()
        if len(rows) != 1:
            return None
        return self._get_artifact(rows[0]["artifact_id"])

    def artifact_by_scope_filename(
        self,
        filename: str,
        *,
        root_frame_id: str | None,
        project_id: str,
    ) -> dict | None:
        """Resolve an upload target in one exact nullable-root scope."""

        root_clause = (
            "root_frame_id=?" if root_frame_id is not None else "root_frame_id IS NULL"
        )
        root_args = (root_frame_id,) if root_frame_id is not None else ()
        with self._lock:
            row = self._connection.execute(
                "SELECT artifact_id FROM artifacts WHERE filename=? AND project_id=? "
                f"AND {root_clause} ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (filename, project_id, *root_args),
            ).fetchone()
        return self._get_artifact(row["artifact_id"]) if row else None

    def artifact_write_scope(
        self,
        *,
        frame_id: str | None,
        root_frame_id: str | None,
        project_id: str | None,
    ) -> tuple[bool, str | None, str]:
        """Resolve and validate producer, root, and project ownership."""
        explicit_scope = any(
            value is not None for value in (frame_id, root_frame_id, project_id)
        )
        actor = self._get_frame(frame_id) if frame_id else None
        scope_source = frame_id if actor else (root_frame_id or frame_id)
        scope = self._resolve_frame_scope(
            scope_source,
            fallback_project=project_id or "default",
        )
        if actor:
            if root_frame_id is not None and root_frame_id != scope["root_frame_id"]:
                raise ValueError("root_frame_id conflicts with producer frame")
            if project_id is not None and project_id != scope["project_id"]:
                raise ValueError("project_id conflicts with producer frame")
            resolved_root = scope["root_frame_id"]
        else:
            resolved_root = root_frame_id or scope["root_frame_id"] or frame_id
        return explicit_scope, resolved_root, scope["project_id"]

    def save_artifact(
        self,
        *,
        path: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        checksum: str | None,
        producing_cell_id: str | None = None,
        frame_id: str | None = None,
        root_frame_id: str | None = None,
        project_id: str | None = None,
        artifact_id: str | None = None,
        is_user_upload: bool = False,
        priority: int = 0,
        env_snapshot_id: str | None = None,
        snapshot_path: str | None = None,
        source: Any = None,
    ) -> dict:
        (
            explicit_scope,
            resolved_root,
            resolved_project,
        ) = self._resolve_artifact_write_scope(
            frame_id=frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )
        now = self._clock_ms()
        version_id = f"v-{uuid.uuid4().hex[:12]}"
        new_artifact = artifact_id is None
        if new_artifact:
            artifact_id = f"a-{uuid.uuid4().hex[:12]}"
        with self._lock:
            if not new_artifact:
                current = self._connection.execute(
                    "SELECT project_id,root_frame_id FROM artifacts "
                    "WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(f"no such artifact {artifact_id!r}")
                if not explicit_scope:
                    resolved_root = current["root_frame_id"]
                    resolved_project = current["project_id"]
                if (
                    current["root_frame_id"] is not None
                    and resolved_root is not None
                    and current["root_frame_id"] != resolved_root
                ):
                    raise ValueError("artifact belongs to a different root frame")
                if (
                    current["root_frame_id"] is not None
                    and current["project_id"] != resolved_project
                ):
                    raise ValueError("artifact belongs to a different project")
            self._connection.execute(
                "INSERT INTO artifact_versions(version_id,artifact_id,filename,"
                "content_type,size_bytes,checksum,path,snapshot_path,"
                "producing_cell_id,frame_id,created_at,env_snapshot_id,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    artifact_id,
                    filename,
                    content_type,
                    size_bytes,
                    checksum,
                    path,
                    snapshot_path,
                    producing_cell_id,
                    frame_id,
                    now,
                    env_snapshot_id,
                    _encode_source(source),
                ),
            )
            if new_artifact:
                self._connection.execute(
                    "INSERT INTO artifacts(artifact_id,project_id,root_frame_id,"
                    "filename,content_type,is_user_upload,priority,"
                    "latest_version_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        artifact_id,
                        resolved_project,
                        resolved_root,
                        filename,
                        content_type,
                        1 if is_user_upload else 0,
                        priority,
                        version_id,
                        now,
                        now,
                    ),
                )
            else:
                self._connection.execute(
                    "UPDATE artifacts SET latest_version_id=?,updated_at=? "
                    "WHERE artifact_id=?",
                    (version_id, now, artifact_id),
                )
            self._connection.commit()
        return {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "filename": filename,
            "path": path,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "checksum": checksum,
            "created_at": now,
        }

    def commit_artifact_upload(
        self,
        *,
        path: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        checksum: str,
        frame_id: str | None,
        project_id: str | None,
        artifact_id: str | None,
        expected_previous_version_id: str | None,
        expected_previous_updated_at: int | None,
        publish: PublishUpload,
    ) -> dict:
        """Commit one upload only after its snapshot and live bytes publish.

        ``publish`` performs the filesystem half while the new version and head
        are still protected by this SQLite savepoint.  If it raises, neither
        row is visible.  The caller owns a durable filesystem journal and can
        therefore restore the previous live file if the process exits after a
        rename but before SQLite commits.
        """

        explicit_scope, resolved_root, resolved_project = (
            self._resolve_artifact_write_scope(
                frame_id=frame_id,
                root_frame_id=None,
                project_id=project_id,
            )
        )
        now = self._clock_ms()
        version_id = f"v-{uuid.uuid4().hex[:12]}"
        new_artifact = artifact_id is None
        if new_artifact:
            artifact_id = f"a-{uuid.uuid4().hex[:12]}"
        assert artifact_id is not None

        with self._lock:
            try:
                self._connection.execute("SAVEPOINT artifact_upload")
                if not new_artifact:
                    current = self._connection.execute(
                        "SELECT project_id,root_frame_id,latest_version_id,updated_at "
                        "FROM artifacts "
                        "WHERE artifact_id=?",
                        (artifact_id,),
                    ).fetchone()
                    if current is None:
                        raise KeyError(f"no such artifact {artifact_id!r}")
                    if not explicit_scope:
                        resolved_root = current["root_frame_id"]
                        resolved_project = current["project_id"]
                    if (
                        current["root_frame_id"] is not None
                        and resolved_root is not None
                        and current["root_frame_id"] != resolved_root
                    ):
                        raise ValueError("artifact belongs to a different root frame")
                    if (
                        current["root_frame_id"] is not None
                        and current["project_id"] != resolved_project
                    ):
                        raise ValueError("artifact belongs to a different project")
                    if (
                        current["latest_version_id"] != expected_previous_version_id
                        or current["updated_at"] != expected_previous_updated_at
                    ):
                        raise RuntimeError("artifact changed before upload publication")
                else:
                    root_clause = (
                        "root_frame_id=?"
                        if resolved_root is not None
                        else "root_frame_id IS NULL"
                    )
                    root_args = (resolved_root,) if resolved_root is not None else ()
                    raced = self._connection.execute(
                        "SELECT 1 FROM artifacts WHERE filename=? AND project_id=? "
                        f"AND {root_clause} LIMIT 1",
                        (filename, resolved_project, *root_args),
                    ).fetchone()
                    if raced is not None:
                        raise RuntimeError(
                            "artifact appeared before upload publication"
                        )

                self._connection.execute(
                    "INSERT INTO artifact_versions(version_id,artifact_id,filename,"
                    "content_type,size_bytes,checksum,path,snapshot_path,"
                    "producing_cell_id,frame_id,created_at,env_snapshot_id,source) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        artifact_id,
                        filename,
                        content_type,
                        size_bytes,
                        checksum,
                        path,
                        None,
                        None,
                        frame_id,
                        now,
                        None,
                        None,
                    ),
                )
                if new_artifact:
                    self._connection.execute(
                        "INSERT INTO artifacts(artifact_id,project_id,root_frame_id,"
                        "filename,content_type,is_user_upload,priority,"
                        "latest_version_id,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            artifact_id,
                            resolved_project,
                            resolved_root,
                            filename,
                            content_type,
                            1,
                            0,
                            version_id,
                            now,
                            now,
                        ),
                    )
                else:
                    self._connection.execute(
                        "UPDATE artifacts SET latest_version_id=?,updated_at=? "
                        "WHERE artifact_id=?",
                        (version_id, now, artifact_id),
                    )

                snapshot_path = publish(version_id, artifact_id)
                self._connection.execute(
                    "UPDATE artifact_versions SET snapshot_path=? "
                    "WHERE version_id=?",
                    (snapshot_path, version_id),
                )
                self._connection.execute("RELEASE SAVEPOINT artifact_upload")
                self._connection.commit()
            except BaseException:
                try:
                    self._connection.execute("ROLLBACK TO SAVEPOINT artifact_upload")
                    self._connection.execute("RELEASE SAVEPOINT artifact_upload")
                except sqlite3.Error:
                    self._connection.rollback()
                raise
        return {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "filename": filename,
            "path": path,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "checksum": checksum,
            "created_at": now,
        }

    def rollback_artifact_upload(
        self,
        *,
        artifact_id: str,
        version_id: str,
        previous_version_id: str | None,
        previous_updated_at: int | None,
        previous_filename: str | None,
        previous_content_type: str | None,
    ) -> bool:
        """Remove exactly one incomplete upload during startup recovery."""

        with self._lock:
            try:
                self._connection.execute("SAVEPOINT artifact_upload_rollback")
                artifact = self._connection.execute(
                    "SELECT latest_version_id FROM artifacts WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                version = self._connection.execute(
                    "SELECT 1 FROM artifact_versions WHERE version_id=? "
                    "AND artifact_id=?",
                    (version_id, artifact_id),
                ).fetchone()
                if artifact is None or version is None:
                    self._connection.execute(
                        "RELEASE SAVEPOINT artifact_upload_rollback"
                    )
                    self._connection.commit()
                    return False
                if artifact["latest_version_id"] != version_id:
                    raise RuntimeError(
                        "incomplete upload is no longer the Artifact head"
                    )
                self._connection.execute(
                    "DELETE FROM lineage_edges WHERE input_version_id=? "
                    "OR output_version_id=?",
                    (version_id, version_id),
                )
                self._connection.execute(
                    "DELETE FROM artifact_capture_observations WHERE version_id=?",
                    (version_id,),
                )
                self._connection.execute(
                    "DELETE FROM artifact_versions WHERE version_id=?",
                    (version_id,),
                )
                if previous_version_id is None:
                    self._connection.execute(
                        "DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,)
                    )
                else:
                    if not previous_filename:
                        raise RuntimeError("previous upload metadata is unavailable")
                    previous = self._connection.execute(
                        "SELECT 1 FROM artifact_versions WHERE version_id=? "
                        "AND artifact_id=?",
                        (previous_version_id, artifact_id),
                    ).fetchone()
                    if previous is None:
                        raise RuntimeError("previous upload head is unavailable")
                    self._connection.execute(
                        "UPDATE artifacts SET filename=?,content_type=?,"
                        "latest_version_id=?,updated_at=? "
                        "WHERE artifact_id=?",
                        (
                            previous_filename,
                            previous_content_type,
                            previous_version_id,
                            previous_updated_at,
                            artifact_id,
                        ),
                    )
                self._connection.execute("RELEASE SAVEPOINT artifact_upload_rollback")
                self._connection.commit()
            except BaseException:
                try:
                    self._connection.execute(
                        "ROLLBACK TO SAVEPOINT artifact_upload_rollback"
                    )
                    self._connection.execute(
                        "RELEASE SAVEPOINT artifact_upload_rollback"
                    )
                except sqlite3.Error:
                    self._connection.rollback()
                raise
        return True

    def record_cell_artifact(
        self,
        *,
        path: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        checksum: str | None,
        producing_cell_id: str | None,
        frame_id: str | None,
        root_frame_id: str | None = None,
        project_id: str | None = None,
        env_snapshot_id: str | None = None,
        snapshot_path: str | None = None,
        input_version_ids: list[str] | tuple[str, ...] | None = None,
        source: Any = None,
        preserve_filename: bool = False,
        preserve_content_type: bool = False,
        reuse_policy: str = "any",
        reuse_matching_head: bool = False,
    ) -> dict:
        """Atomically record or finalize one cell's physical file write.

        A version is a byte identity, while an observation is a producer
        identity.  With ``reuse_matching_head`` enabled, the current head may
        therefore be reused when a new Cell producer and an equal checksum are
        explicit.  The current head may have originated outside a Cell (for
        example, an upload or a native writing tool); its absent producer stays
        absent while the new Cell gets a durable observation.  We never search
        historical versions for this optimization and never rewrite the reused
        version's original provenance.  The default keeps the pre-rollout
        versioning behavior, including its return shape and lack of observation
        writes. Trusted callers opt into both the reuse rule and its required
        durable capture audit with the same flag.
        """
        if reuse_policy not in {"any", "provisional"}:
            raise ValueError(f"unknown cell artifact reuse policy: {reuse_policy!r}")
        _explicit, resolved_root, resolved_project = self._resolve_artifact_write_scope(
            frame_id=frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )
        now = self._clock_ms()
        version_id: str
        artifact_id: str
        created_at = now
        stored_version: sqlite3.Row
        capture_kind = CAPTURE_KIND_VERSION_CREATED
        version_created = True
        observation: dict | None = None
        producer_frame_id = frame_id
        with self._lock:
            try:
                self._connection.execute("SAVEPOINT artifact_record_cell")
                artifact = None
                candidate = None
                root_clause = (
                    "a.root_frame_id=?"
                    if resolved_root is not None
                    else "a.root_frame_id IS NULL"
                )
                root_args = (resolved_root,) if resolved_root is not None else ()
                validated_input_ids: list[str] = []
                for input_version_id in input_version_ids or ():
                    if not input_version_id:
                        continue
                    if not isinstance(input_version_id, str):
                        raise TypeError("input version ids must be strings")
                    if input_version_id in validated_input_ids:
                        continue
                    available = self._connection.execute(
                        "SELECT 1 FROM artifact_versions v JOIN artifacts a "
                        "ON a.artifact_id=v.artifact_id WHERE v.version_id=? "
                        "AND a.project_id=? AND " + root_clause + " LIMIT 1",
                        (input_version_id, resolved_project, *root_args),
                    ).fetchone()
                    if available is None:
                        # Missing and foreign share one refusal.  This check is
                        # inside the output savepoint so a deleted/re-scoped
                        # input cannot race a successful lineage insert.
                        raise KeyError(
                            f"no artifact version {input_version_id!r} "
                            "in the current session"
                        )
                    validated_input_ids.append(input_version_id)

                if producing_cell_id and checksum is not None:
                    exact_rows = self._connection.execute(
                        "SELECT v.*,"
                        "CASE WHEN a.filename=? THEN 0 ELSE 1 END AS filename_rank "
                        "FROM artifact_versions v JOIN artifacts a "
                        "ON a.artifact_id=v.artifact_id WHERE a.project_id=? AND "
                        + root_clause
                        + " AND v.version_id=a.latest_version_id "
                        "AND v.producing_cell_id=? AND v.checksum=? "
                        "ORDER BY filename_rank,v.created_at DESC,v.rowid DESC",
                        (
                            filename,
                            resolved_project,
                            *root_args,
                            producing_cell_id,
                            checksum,
                        ),
                    ).fetchall()
                    for row in exact_rows:
                        if self._paths_match(row["path"], path):
                            candidate = row
                            break

                same_cell_reuse = candidate is not None and (
                    reuse_policy == "any" or not candidate["snapshot_path"]
                )

                if same_cell_reuse:
                    artifact = self._connection.execute(
                        "SELECT rowid AS artifact_rowid,* FROM artifacts "
                        "WHERE artifact_id=?",
                        (candidate["artifact_id"],),
                    ).fetchone()
                else:
                    artifact = self._connection.execute(
                        "SELECT a.rowid AS artifact_rowid,a.*,"
                        "v.version_id AS head_version_id,"
                        "v.filename AS head_filename,"
                        "v.content_type AS head_content_type,"
                        "v.checksum AS head_checksum,"
                        "v.producing_cell_id AS head_producing_cell_id,"
                        "v.created_at AS head_created_at "
                        "FROM artifacts a LEFT JOIN artifact_versions v "
                        "ON v.version_id=a.latest_version_id "
                        "WHERE a.filename=? AND a.project_id=? AND "
                        + root_clause
                        + " ORDER BY a.created_at DESC,a.rowid DESC LIMIT 1",
                        (filename, resolved_project, *root_args),
                    ).fetchone()

                cross_cell_head_reuse = bool(
                    reuse_matching_head
                    and not same_cell_reuse
                    and artifact is not None
                    and producing_cell_id
                    and checksum is not None
                    and artifact["head_version_id"]
                    and artifact["head_checksum"] == checksum
                    and artifact["head_producing_cell_id"] != producing_cell_id
                )

                if same_cell_reuse:
                    artifact_id = candidate["artifact_id"]
                    version_id = candidate["version_id"]
                    created_at = candidate["created_at"]
                    capture_kind = CAPTURE_KIND_SAME_CELL_MERGE
                    version_created = False
                    producer_frame_id = candidate["frame_id"]
                    stored_filename = (
                        (candidate["filename"] or artifact["filename"])
                        if preserve_filename
                        else filename
                    )
                    stored_content_type = (
                        candidate["content_type"]
                        if preserve_content_type and candidate["content_type"]
                        else content_type
                    )
                    self._connection.execute(
                        "UPDATE artifact_versions SET filename=?,"
                        "content_type=COALESCE(?,content_type),size_bytes=?,"
                        "checksum=?,path=?,snapshot_path=COALESCE(snapshot_path,?),"
                        "env_snapshot_id=COALESCE(env_snapshot_id,?),"
                        "source=COALESCE(source,?) "
                        "WHERE version_id=?",
                        (
                            stored_filename,
                            stored_content_type,
                            size_bytes,
                            checksum,
                            path,
                            snapshot_path,
                            env_snapshot_id,
                            _encode_source(source),
                            version_id,
                        ),
                    )
                elif cross_cell_head_reuse:
                    # The version still belongs to its original Cell.  In
                    # particular, do not fill a missing env/source from this
                    # later producer: that would turn deduplication into false
                    # provenance.  The observation below owns the new facts.
                    artifact_id = artifact["artifact_id"]
                    version_id = artifact["head_version_id"]
                    created_at = artifact["head_created_at"]
                    capture_kind = CAPTURE_KIND_HEAD_CHECKSUM_REUSED
                    version_created = False
                    stored_filename = artifact["head_filename"] or artifact["filename"]
                    stored_content_type = (
                        artifact["head_content_type"] or artifact["content_type"]
                    )
                    if snapshot_path:
                        self._connection.execute(
                            "UPDATE artifact_versions SET "
                            "snapshot_path=COALESCE(snapshot_path,?) "
                            "WHERE version_id=?",
                            (snapshot_path, version_id),
                        )
                else:
                    stored_filename = filename
                    stored_content_type = content_type
                    version_id = f"v-{uuid.uuid4().hex[:12]}"
                    artifact_id = (
                        artifact["artifact_id"]
                        if artifact is not None
                        else f"a-{uuid.uuid4().hex[:12]}"
                    )
                    self._connection.execute(
                        "INSERT INTO artifact_versions(version_id,artifact_id,"
                        "filename,content_type,size_bytes,checksum,path,"
                        "snapshot_path,producing_cell_id,frame_id,created_at,"
                        "env_snapshot_id,source) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            version_id,
                            artifact_id,
                            filename,
                            content_type,
                            size_bytes,
                            checksum,
                            path,
                            snapshot_path,
                            producing_cell_id,
                            frame_id,
                            now,
                            env_snapshot_id,
                            _encode_source(source),
                        ),
                    )
                    if artifact is None:
                        self._connection.execute(
                            "INSERT INTO artifacts(artifact_id,project_id,"
                            "root_frame_id,filename,content_type,is_user_upload,"
                            "priority,latest_version_id,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (
                                artifact_id,
                                resolved_project,
                                resolved_root,
                                filename,
                                stored_content_type,
                                0,
                                0,
                                version_id,
                                now,
                                now,
                            ),
                        )

                if cross_cell_head_reuse:
                    self._connection.execute(
                        "UPDATE artifacts SET updated_at=? WHERE artifact_id=?",
                        (now, artifact_id),
                    )
                else:
                    self._connection.execute(
                        "UPDATE artifacts SET filename=?,"
                        "content_type=COALESCE(?,content_type),latest_version_id=?,"
                        "updated_at=? WHERE artifact_id=?",
                        (
                            stored_filename,
                            stored_content_type,
                            version_id,
                            now,
                            artifact_id,
                        ),
                    )
                seen_inputs: set[str] = set()
                recorded_inputs: list[Any] = []
                for input_version_id in validated_input_ids:
                    if (
                        not input_version_id
                        or input_version_id == version_id
                        or input_version_id in seen_inputs
                    ):
                        continue
                    seen_inputs.add(input_version_id)
                    recorded_inputs.append(input_version_id)
                    exists = self._connection.execute(
                        "SELECT 1 FROM lineage_edges WHERE input_version_id=? "
                        "AND output_version_id=? AND producing_cell_id IS ? "
                        "LIMIT 1",
                        (
                            input_version_id,
                            version_id,
                            producing_cell_id,
                        ),
                    ).fetchone()
                    if exists:
                        continue
                    self._connection.execute(
                        "INSERT INTO lineage_edges(edge_id,input_version_id,"
                        "output_version_id,producing_cell_id,frame_id,created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (
                            f"e-{uuid.uuid4().hex[:12]}",
                            input_version_id,
                            version_id,
                            producing_cell_id,
                            producer_frame_id,
                            now,
                        ),
                    )
                stored_version = self._connection.execute(
                    "SELECT * FROM artifact_versions WHERE version_id=?",
                    (version_id,),
                ).fetchone()
                if producing_cell_id and reuse_matching_head:
                    observation = self._observations.upsert(
                        artifact_id=artifact_id,
                        version_id=version_id,
                        producing_cell_id=producing_cell_id,
                        frame_id=producer_frame_id,
                        capture_kind=capture_kind,
                        filename=filename,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        checksum=checksum,
                        path=path,
                        # Point at the snapshot retained by the exact version,
                        # not a later caller's pre-freeze candidate that the
                        # Artifact manager is allowed to clean up.
                        snapshot_path=stored_version["snapshot_path"],
                        env_snapshot_id=env_snapshot_id,
                        source=_encode_source(source),
                        input_version_ids=recorded_inputs,
                        now=now,
                    )
                self._connection.execute("RELEASE SAVEPOINT artifact_record_cell")
                self._connection.commit()
            except Exception:
                try:
                    self._connection.execute(
                        "ROLLBACK TO SAVEPOINT artifact_record_cell"
                    )
                    self._connection.execute("RELEASE SAVEPOINT artifact_record_cell")
                except sqlite3.Error:
                    self._connection.rollback()
                raise
        result = {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "filename": stored_version["filename"],
            "path": stored_version["path"],
            "content_type": stored_version["content_type"],
            "size_bytes": stored_version["size_bytes"],
            "checksum": stored_version["checksum"],
            "created_at": created_at,
        }
        if reuse_matching_head:
            result.update(
                observation_id=(
                    observation["observation_id"] if observation is not None else None
                ),
                observation_ordinal=(
                    observation["ordinal"] if observation is not None else None
                ),
                ordinal=(observation["ordinal"] if observation is not None else None),
                version_created=version_created,
                capture_kind=capture_kind,
            )
        return result

    def materialise_artifact_version(
        self,
        *,
        source_version_id: str,
        artifact_id: str,
        version_id: str,
        filename: str,
        path: str,
        snapshot_path: str | None,
        frame_id: str | None,
        root_frame_id: str,
        project_id: str,
        producing_cell_id: str | None = None,
        publish: PublishUpload | None = None,
    ) -> dict:
        """Copy another session's artifact version *into* this one, atomically.

        The third write beside `record_cell_artifact` and
        `record_artifact_restore`, and it exists so that nothing ever reads
        another session's file in place. A cross-session read leaves the
        borrowing session with an analysis whose input has no version in its own
        history: delete or revert the other session and the provenance of this
        one silently becomes unresolvable. Materialising gives the target its
        own Artifact and version, with a lineage edge back to the source, so
        "where did this come from" keeps an answer that does not depend on the
        other session still existing.

        Scope is enforced here rather than by the caller. Same project only, and
        a source in another project raises the same `KeyError` as a source that
        does not exist -- a distinct "forbidden" would confirm the object is
        there, which is the one bit a caller outside the project should not be
        able to read.

        Byte movement is owned by the caller's durable publish callback. It
        writes the exact upload journal before changing either final pathname;
        this transaction commits only after publication succeeds, and startup
        recovery can therefore distinguish an interrupted commit from a
        committed write awaiting cleanup.
        """
        if not version_id or version_id == source_version_id:
            raise ValueError("materialisation requires a fresh version id")
        now = self._clock_ms()
        with self._lock:
            try:
                self._connection.execute("SAVEPOINT artifact_materialise")
                source = self._connection.execute(
                    "SELECT v.*, a.project_id AS src_project, "
                    "a.root_frame_id AS src_root FROM artifact_versions v "
                    "JOIN artifacts a ON a.artifact_id=v.artifact_id "
                    "WHERE v.version_id=?",
                    (source_version_id,),
                ).fetchone()
                # One message for "absent" and for "another project's", and
                # deliberately the SAME message the Host service raises. The
                # two checks are independent on purpose, but if they worded the
                # refusal differently then removing the outer one would turn
                # the inner one into the disclosure channel both exist to
                # close: a caller could tell "another project's" from "absent"
                # by which sentence came back.
                if source is None or source["src_project"] != project_id:
                    raise KeyError(
                        f"no artifact version {source_version_id!r} available"
                    )
                if source["src_root"] == root_frame_id:
                    raise ValueError("artifact version already belongs to this session")

                existing = self._connection.execute(
                    "SELECT * FROM artifacts WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                if existing is None:
                    self._connection.execute(
                        "INSERT INTO artifacts(artifact_id,filename,content_type,"
                        "latest_version_id,root_frame_id,project_id,created_at,"
                        "updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            artifact_id,
                            filename,
                            source["content_type"],
                            version_id,
                            root_frame_id,
                            project_id,
                            now,
                            now,
                        ),
                    )
                elif (
                    existing["root_frame_id"] != root_frame_id
                    or existing["project_id"] != project_id
                ):
                    raise ValueError("target artifact belongs to a different scope")

                source_envelope = None
                try:
                    source_envelope = source["source"]
                except (IndexError, KeyError):
                    source_envelope = None
                self._connection.execute(
                    "INSERT INTO artifact_versions(version_id,artifact_id,"
                    "filename,content_type,size_bytes,checksum,path,"
                    "snapshot_path,producing_cell_id,frame_id,created_at,"
                    "env_snapshot_id,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        artifact_id,
                        filename,
                        source["content_type"],
                        source["size_bytes"],
                        source["checksum"],
                        path,
                        snapshot_path,
                        producing_cell_id,
                        frame_id,
                        now,
                        source["env_snapshot_id"],
                        source_envelope,
                    ),
                )
                self._connection.execute(
                    "UPDATE artifacts SET latest_version_id=?,updated_at=? "
                    "WHERE artifact_id=?",
                    (version_id, now, artifact_id),
                )
                # The edge is what makes this materialisation rather than a
                # copy: the target version knows which version it came from,
                # and the lineage walk crosses the session boundary even though
                # no read ever does.
                self._connection.execute(
                    "INSERT INTO lineage_edges(edge_id,input_version_id,"
                    "output_version_id,producing_cell_id,frame_id,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        f"e-{uuid.uuid4().hex[:12]}",
                        source_version_id,
                        version_id,
                        producing_cell_id,
                        frame_id,
                        now,
                    ),
                )
                if publish is not None:
                    snapshot_path = publish(version_id, artifact_id)
                    self._connection.execute(
                        "UPDATE artifact_versions SET snapshot_path=? "
                        "WHERE version_id=?",
                        (snapshot_path, version_id),
                    )
                self._connection.execute("RELEASE SAVEPOINT artifact_materialise")
                self._connection.commit()
            except BaseException:
                try:
                    self._connection.execute(
                        "ROLLBACK TO SAVEPOINT artifact_materialise"
                    )
                    self._connection.execute("RELEASE SAVEPOINT artifact_materialise")
                except sqlite3.Error:
                    self._connection.rollback()
                raise
        return {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "filename": filename,
            "path": path,
            "content_type": source["content_type"],
            "size_bytes": source["size_bytes"],
            "checksum": source["checksum"],
            "created_at": now,
            "materialised_from_version_id": source_version_id,
        }

    def record_artifact_restore(
        self,
        *,
        artifact_id: str,
        source_version_id: str,
        expected_latest_version_id: str,
        version_id: str,
        path: str,
        snapshot_path: str | None,
        size_bytes: int,
        checksum: str,
        frame_id: str | None,
        root_frame_id: str | None = None,
        project_id: str | None = None,
        publish: PublishUpload | None = None,
    ) -> dict:
        """Append one restored version and its lineage edge atomically.

        Restoring never makes the historical row current again.  The source
        version is read inside the same transaction, a fresh immutable version
        is inserted, and only that new identity becomes the Artifact head.
        Filesystem confinement and byte verification belong to the Host data
        service; this repository owns the exact persistence transaction.
        """
        if not version_id or version_id == source_version_id:
            raise ValueError("restore requires a fresh version id")
        _explicit, resolved_root, resolved_project = self._resolve_artifact_write_scope(
            frame_id=frame_id,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )
        now = self._clock_ms()
        with self._lock:
            try:
                self._connection.execute("SAVEPOINT artifact_restore")
                artifact = self._connection.execute(
                    "SELECT * FROM artifacts WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone()
                source = self._connection.execute(
                    "SELECT * FROM artifact_versions WHERE version_id=? "
                    "AND artifact_id=?",
                    (source_version_id, artifact_id),
                ).fetchone()
                if artifact is None or source is None:
                    raise ArtifactRestoreRefused("artifact restore source not found")
                if artifact["latest_version_id"] != expected_latest_version_id:
                    raise ArtifactRestoreRefused(
                        "artifact changed concurrently during restore"
                    )
                if artifact["latest_version_id"] == source_version_id:
                    raise ArtifactRestoreRefused(
                        "restore source is already the latest version"
                    )
                if source["checksum"] != checksum or (
                    source["size_bytes"] is not None
                    and int(source["size_bytes"]) != int(size_bytes)
                ):
                    raise RuntimeError("restore bytes no longer match source metadata")
                if (
                    artifact["root_frame_id"] is not None
                    and resolved_root != artifact["root_frame_id"]
                ):
                    raise ValueError("artifact belongs to a different root frame")
                if artifact["project_id"] != resolved_project:
                    raise ValueError("artifact belongs to a different project")

                filename = source["filename"] or artifact["filename"]
                content_type = source["content_type"] or artifact["content_type"]
                # Carry the retrieval-provenance envelope forward too. A normal
                # version write persists `source`; the restore insert copied
                # only `env_snapshot_id`, so a restored retrieval-backed version
                # lost its request URL, timestamp and response hash in
                # latest-version exports and evidence checks — the restored bytes
                # would look unsourced even though the historical row was not.
                source_envelope = None
                try:
                    source_envelope = source["source"]
                except (IndexError, KeyError):
                    source_envelope = None
                self._connection.execute(
                    "INSERT INTO artifact_versions(version_id,artifact_id,"
                    "filename,content_type,size_bytes,checksum,path,"
                    "snapshot_path,producing_cell_id,frame_id,created_at,"
                    "env_snapshot_id,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        artifact_id,
                        filename,
                        content_type,
                        int(size_bytes),
                        checksum,
                        path,
                        snapshot_path,
                        None,
                        frame_id,
                        now,
                        source["env_snapshot_id"],
                        source_envelope,
                    ),
                )
                self._connection.execute(
                    "UPDATE artifacts SET filename=?,content_type=COALESCE(?,"
                    "content_type),latest_version_id=?,updated_at=? "
                    "WHERE artifact_id=?",
                    (filename, content_type, version_id, now, artifact_id),
                )
                self._connection.execute(
                    "INSERT INTO lineage_edges(edge_id,input_version_id,"
                    "output_version_id,producing_cell_id,frame_id,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        f"e-{uuid.uuid4().hex[:12]}",
                        source_version_id,
                        version_id,
                        None,
                        frame_id,
                        now,
                    ),
                )
                if publish is not None:
                    snapshot_path = publish(version_id, artifact_id)
                    self._connection.execute(
                        "UPDATE artifact_versions SET snapshot_path=? "
                        "WHERE version_id=?",
                        (snapshot_path, version_id),
                    )
                self._connection.execute("RELEASE SAVEPOINT artifact_restore")
                self._connection.commit()
            except BaseException:
                try:
                    self._connection.execute("ROLLBACK TO SAVEPOINT artifact_restore")
                    self._connection.execute("RELEASE SAVEPOINT artifact_restore")
                except sqlite3.Error:
                    self._connection.rollback()
                raise
        return {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "filename": filename,
            "path": path,
            "content_type": content_type,
            "size_bytes": int(size_bytes),
            "checksum": checksum,
            "created_at": now,
            "restored_from_version_id": source_version_id,
        }

    def upsert_env_snapshot(self, snapshot: dict) -> str:
        packages = snapshot.get("packages") or []
        packages_json = json.dumps(packages, separators=(",", ":"))
        remote = snapshot.get("remote") or []
        remote_json = json.dumps(remote, separators=(",", ":"), sort_keys=True)
        snapshot_id = env_snapshot_id(
            kind=snapshot.get("kind"),
            python_version=snapshot.get("python_version"),
            implementation=snapshot.get("implementation"),
            platform=snapshot.get("platform"),
            interpreter=snapshot.get("interpreter"),
            environment_name=snapshot.get("environment_name"),
            generation_id=snapshot.get("generation_id"),
            packages_json=packages_json,
            remote_json=remote_json,
        )
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM env_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
            if not exists:
                self._connection.execute(
                    "INSERT INTO env_snapshots(snapshot_id,created_at,kind,"
                    "python_version,implementation,platform,package_count,"
                    "packages_json,remote_json,interpreter,environment_name,"
                    "generation_id,generation_confidence,packages_unavailable,"
                    "provenance) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        snapshot_id,
                        self._clock_ms(),
                        snapshot.get("kind"),
                        snapshot.get("python_version"),
                        snapshot.get("implementation"),
                        snapshot.get("platform"),
                        int(snapshot.get("package_count") or len(packages)),
                        packages_json,
                        remote_json if remote else None,
                        snapshot.get("interpreter"),
                        snapshot.get("environment_name"),
                        snapshot.get("generation_id"),
                        # Local capture defaults to verified when its address
                        # includes a generation. Importers may explicitly
                        # lower that claim: a remapped local identity prevents
                        # cross-session references, but does not prove that
                        # package-authored environment metadata was measured
                        # by this installation.
                        (
                            snapshot.get("generation_confidence")
                            if "generation_confidence" in snapshot
                            else ("verified" if snapshot.get("generation_id") else None)
                        ),
                        snapshot.get("packages_unavailable"),
                        # Measured from a kernel generation, or assumed from
                        # this process? The fallback path has always said so
                        # and the INSERT dropped it.
                        snapshot.get("provenance"),
                    ),
                )
                self._connection.commit()
        return snapshot_id

    def delete_env_snapshots_if_unreferenced(self, snapshot_ids) -> int:
        """Delete only named snapshots that no Artifact version still uses."""

        identifiers = tuple(
            dict.fromkeys(str(value) for value in snapshot_ids if value)
        )
        if not identifiers:
            return 0
        marks = "(" + ",".join("?" for _ in identifiers) + ")"
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM env_snapshots WHERE snapshot_id IN "
                f"{marks} AND NOT EXISTS (SELECT 1 FROM artifact_versions "
                "WHERE artifact_versions.env_snapshot_id="
                "env_snapshots.snapshot_id) AND NOT EXISTS (SELECT 1 FROM "
                "artifact_capture_observations WHERE "
                "artifact_capture_observations.env_snapshot_id="
                "env_snapshots.snapshot_id)",
                identifiers,
            )
            self._connection.commit()
        return max(0, int(cursor.rowcount or 0))

    def get_env_snapshot(self, snapshot_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM env_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["packages"] = json.loads(result.pop("packages_json") or "[]")
        except (ValueError, TypeError):
            result.pop("packages_json", None)
            result["packages"] = []
        try:
            result["remote"] = json.loads(result.pop("remote_json") or "[]")
        except (ValueError, TypeError):
            result.pop("remote_json", None)
            result["remote"] = []
        return result

    def env_snapshot_for_artifact(
        self, artifact_id: str, version_id: str | None = None
    ) -> dict | None:
        with self._lock:
            if version_id:
                row = self._connection.execute(
                    "SELECT env_snapshot_id FROM artifact_versions "
                    "WHERE version_id=? AND artifact_id=?",
                    (version_id, artifact_id),
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT v.env_snapshot_id FROM artifacts a "
                    "JOIN artifact_versions v ON a.latest_version_id=v.version_id "
                    "WHERE a.artifact_id=?",
                    (artifact_id,),
                ).fetchone()
        snapshot_id = row["env_snapshot_id"] if row else None
        return self._get_env_snapshot(snapshot_id) if snapshot_id else None

    def list_artifacts(self, filters: dict | None = None) -> list[dict]:
        filters = filters or {}
        sql = (
            "SELECT a.artifact_id,a.filename,a.content_type,a.is_user_upload,"
            "a.priority,a.latest_version_id,a.root_frame_id,a.project_id,"
            "a.created_at,v.size_bytes,v.checksum "
            "FROM artifacts a LEFT JOIN artifact_versions v "
            "ON a.latest_version_id=v.version_id"
        )
        clauses, params = [], []
        for key in (
            "project_id",
            "content_type",
            "filename",
            "artifact_id",
            "root_frame_id",
        ):
            if key in filters:
                clauses.append(f"a.{key}=?")
                params.append(filters[key])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY a.created_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def browse_artifacts(
        self,
        *,
        project_id: str,
        filename_query: str | None = None,
        content_type: str | None = None,
        origin: str | None = None,
        before: tuple[int, str] | None = None,
        limit: int = 50,
        visible_to_user_id: str | None = None,
    ) -> list[dict]:
        """Newest-first keyset page of one project's artifacts.

        ``before`` is the ``(created_at, artifact_id)`` of the last row of
        the previous page, not an offset. ``created_at`` is a millisecond
        clock and two captures in the same millisecond are ordinary (a
        fixture, a batch harvest), so the ``artifact_id`` tiebreaker is
        what makes the cursor sound: ordering by timestamp alone leaves
        the rest of a tie undefined and a cursor can drop it.

        Team visibility is a WHERE conjunct, not a post-filter. Keyset
        ``has_more`` is observed from row counts, so filtering after
        ``LIMIT`` turns a page of hidden rows into a phantom end-of-list.

        ``filename_query`` is an escaped substring match on ``filename``
        only — never path, checksum, or content type. ``origin`` is
        derived from ``is_user_upload`` (``uploaded`` / ``generated``).
        """
        clauses: list[str] = ["a.project_id=?"]
        params: list[Any] = [project_id]
        query = (filename_query or "").strip()
        if query:
            clauses.append("a.filename LIKE ? ESCAPE '\\'")
            # An unescaped ``%`` in a filename query would match every filename.
            params.append(like_contains(query))
        if content_type:
            clauses.append("a.content_type=?")
            params.append(content_type)
        if origin == "uploaded":
            clauses.append("a.is_user_upload=1")
        elif origin == "generated":
            clauses.append("a.is_user_upload=0")
        elif origin:
            raise ValueError("origin must be uploaded or generated")
        if visible_to_user_id is not None:
            clause, clause_params = visible_session_clause(
                visible_to_user_id,
                table="a",
                session_expr="a.root_frame_id",
            )
            clauses.append(clause)
            params.extend(clause_params)
        if before is not None:
            before_created, before_id = before
            clauses.append(
                "(a.created_at < ? OR (a.created_at = ? AND a.artifact_id < ?))"
            )
            params.extend([before_created, before_created, before_id])
        page_size = max(1, int(limit))
        sql = (
            "SELECT a.artifact_id,a.filename,a.content_type,a.is_user_upload,"
            "a.priority,a.latest_version_id,a.root_frame_id,a.project_id,"
            "a.created_at,v.size_bytes,v.checksum "
            "FROM artifacts a LEFT JOIN artifact_versions v "
            "ON a.latest_version_id=v.version_id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY a.created_at DESC, a.artifact_id DESC LIMIT ?"
        )
        with self._lock:
            rows = self._connection.execute(sql, (*params, page_size)).fetchall()
        return [dict(row) for row in rows]

    def list_artifact_names(self) -> list[dict]:
        """Store-wide ``(filename, artifact_id, latest_version_id)`` rows.

        The submission-evidence gatherer needs exactly these three columns
        per artifact. ``list_artifacts`` joins versions and sorts by
        recency for the UI's benefit — work that path discards, on a call
        that runs while the kernel worker blocks on the host-call lock.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT filename,artifact_id,latest_version_id FROM artifacts"
            ).fetchall()
        return [dict(row) for row in rows]

    def artifact_names_for_frame(self, frame_id: str) -> list[str]:
        """Distinct artifact filenames whose captures this frame produced.

        The delegation envelope's ``artifacts`` field and the
        ``require_artifacts`` boundary check read this instead of trusting the
        child's claims.  A newly created version carries its producer on
        ``artifact_versions``.  A later Cell that emits byte-identical output
        intentionally reuses that version and carries its producer on
        ``artifact_capture_observations`` instead; both are durable capture
        evidence and neither may be omitted from the boundary check.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT a.filename AS filename FROM artifact_versions v "
                "JOIN artifacts a ON v.artifact_id=a.artifact_id "
                "WHERE v.frame_id=? "
                "UNION "
                "SELECT o.filename AS filename "
                "FROM artifact_capture_observations o WHERE o.frame_id=? "
                "ORDER BY filename",
                (frame_id, frame_id),
            ).fetchall()
        return [row["filename"] for row in rows]

    def resolve_artifact_path(self, ident: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT COALESCE(snapshot_path, path) AS p FROM artifact_versions "
                "WHERE version_id=?",
                (ident,),
            ).fetchone()
            if row:
                return row["p"]
            row = self._connection.execute(
                "SELECT COALESCE(v.snapshot_path, v.path) AS p FROM artifacts a "
                "JOIN artifact_versions v ON a.latest_version_id=v.version_id "
                "WHERE a.artifact_id=?",
                (ident,),
            ).fetchone()
        return row["p"] if row else None

    def version_for_path(
        self, path: str, *, root_frame_id: str | None, project_id: str
    ) -> str | None:
        """The version a path belongs to, within one session.

        Scope is keyword-only and required, so an unscoped call is
        unrepresentable rather than defaulted. It has to be: `artifact_versions`
        carries no project column, this was the one artifact read keyed on a
        filesystem path rather than an id, and the identity fallback below scans
        *every* row in the database. An agent could hand it any absolute path
        and learn whether another project held a version for it -- and the id it
        got back then passed unvalidated into `prov_record`, whose lineage read
        returns the input version's filename and path. An existence oracle that
        escalates to disclosure.

        The predicates go into both SELECTs rather than filtering afterwards.
        Post-filtering is wrong and not merely slower: this returns one best
        candidate, so discarding a foreign winner would answer None even when an
        in-scope version for the same path exists further down the list.
        """
        root_clause = (
            "a.root_frame_id=?"
            if root_frame_id is not None
            else "a.root_frame_id IS NULL"
        )
        root_args: tuple = (root_frame_id,) if root_frame_id is not None else ()
        scope_args = (project_id, *root_args)
        with self._lock:
            exact = self._connection.execute(
                "SELECT v.version_id,v.created_at,v.rowid AS version_rowid "
                "FROM artifact_versions v "
                "JOIN artifacts a ON a.artifact_id=v.artifact_id "
                f"WHERE a.project_id=? AND {root_clause} AND v.path=? "
                "ORDER BY v.created_at DESC, v.rowid DESC LIMIT 1",
                (*scope_args, str(path)),
            ).fetchone()
            identity = self._identify_file(path)
            if identity is None:
                return exact["version_id"] if exact else None
            if exact:
                candidates = self._connection.execute(
                    "SELECT v.version_id,v.path FROM artifact_versions v "
                    "JOIN artifacts a ON a.artifact_id=v.artifact_id "
                    f"WHERE a.project_id=? AND {root_clause} AND "
                    "(v.created_at>? OR (v.created_at=? AND v.rowid>?)) "
                    "ORDER BY v.created_at DESC, v.rowid DESC",
                    (
                        *scope_args,
                        exact["created_at"],
                        exact["created_at"],
                        exact["version_rowid"],
                    ),
                ).fetchall()
            else:
                candidates = self._connection.execute(
                    "SELECT v.version_id,v.path FROM artifact_versions v "
                    "JOIN artifacts a ON a.artifact_id=v.artifact_id "
                    f"WHERE a.project_id=? AND {root_clause} "
                    "ORDER BY v.created_at DESC, v.rowid DESC",
                    scope_args,
                ).fetchall()
        for candidate in candidates:
            if self._identify_file(candidate["path"]) == identity:
                return candidate["version_id"]
        return exact["version_id"] if exact else None

    def version_meta(self, version_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM artifact_versions WHERE version_id=?", (version_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_version_source(self, version_id: str, source: Any) -> None:
        """Bind harvest/retrieval provenance onto an existing version."""

        encoded = _encode_source(source)
        with self._lock:
            self._connection.execute(
                "UPDATE artifact_versions SET source=? WHERE version_id=?",
                (encoded, version_id),
            )
            self._connection.commit()

    def list_capture_observations(
        self,
        *,
        artifact_id: str | None = None,
        version_id: str | None = None,
    ) -> list[dict]:
        """Return capture audit rows in their durable global order."""
        with self._lock:
            return self._observations.list(
                artifact_id=artifact_id,
                version_id=version_id,
            )

    def capture_observation_cursor(
        self,
        *,
        root_frame_id: str | None = None,
        project_id: str | None = None,
    ) -> int:
        with self._lock:
            return self._observations.cursor(
                root_frame_id=root_frame_id,
                project_id=project_id,
            )

    def capture_observations_since(
        self,
        cursor: int,
        *,
        root_frame_id: str | None,
        project_id: str,
        limit: int = MAX_DELIVERY_OBSERVATIONS,
    ) -> list[dict]:
        with self._lock:
            bounded = max(1, min(int(limit), MAX_DELIVERY_OBSERVATIONS))
            rows = self._observations.since(
                cursor,
                root_frame_id=root_frame_id,
                project_id=project_id,
                limit=bounded + 1,
            )
            if len(rows) > bounded:
                raise RuntimeError(
                    "Artifact capture observation delta exceeds the safe limit"
                )
            return rows

    def list_versions(self, artifact_id: str) -> list[dict]:
        with self._lock:
            latest = self._connection.execute(
                "SELECT latest_version_id FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            rows = self._connection.execute(
                # `source` is the retrieval provenance envelope. It was
                # written on every retrieved version and selected by nothing,
                # so a figure built on a live API fetch was indistinguishable
                # from one computed out of thin air. The gateway projects it
                # through an allowlist before any of it reaches a client.
                "SELECT version_id,filename,content_type,size_bytes,checksum,"
                "producing_cell_id,frame_id,created_at,source FROM "
                "artifact_versions WHERE artifact_id=? "
                "ORDER BY created_at DESC, rowid DESC",
                (artifact_id,),
            ).fetchall()
        latest_version_id = latest["latest_version_id"] if latest else None
        result = []
        for index, row in enumerate(rows):
            item = dict(row)
            item["is_latest"] = row["version_id"] == latest_version_id
            item["ordinal"] = len(rows) - index
            result.append(item)
        return result

    def update_version_path(
        self,
        version_id: str,
        path: str,
        size_bytes: int | None = None,
        checksum: str | None = None,
    ) -> None:
        sets = ["path=?"]
        params: list = [path]
        if size_bytes is not None:
            sets.append("size_bytes=?")
            params.append(size_bytes)
        if checksum is not None:
            sets.append("checksum=?")
            params.append(checksum)
        params.append(version_id)
        self._execute(
            f"UPDATE artifact_versions SET {','.join(sets)} WHERE version_id=?",
            tuple(params),
        )

    def set_version_snapshot(self, version_id: str, snapshot_path: str) -> None:
        self._execute(
            "UPDATE artifact_versions SET snapshot_path=? WHERE version_id=?",
            (snapshot_path, version_id),
        )

    def set_priority(self, artifact_id: str, priority: int) -> dict | None:
        self._execute(
            "UPDATE artifacts SET priority=?,updated_at=? WHERE artifact_id=?",
            (int(priority), self._clock_ms(), artifact_id),
        )
        return self._get_artifact(artifact_id)

    def set_latest_version(self, artifact_id: str, version_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT version_id FROM artifact_versions WHERE version_id=? "
                "AND artifact_id=?",
                (version_id, artifact_id),
            ).fetchone()
        if not row:
            return None
        self._execute(
            "UPDATE artifacts SET latest_version_id=?,updated_at=? "
            "WHERE artifact_id=?",
            (version_id, self._clock_ms(), artifact_id),
        )
        return self._get_artifact(artifact_id)

    def add_lineage_edge(
        self,
        *,
        input_version_id: str,
        output_version_id: str,
        producing_cell_id: str | None = None,
        frame_id: str | None = None,
    ) -> None:
        self._execute(
            "INSERT INTO lineage_edges(edge_id,input_version_id,"
            "output_version_id,producing_cell_id,frame_id,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                f"e-{uuid.uuid4().hex[:12]}",
                input_version_id,
                output_version_id,
                producing_cell_id,
                frame_id,
                self._clock_ms(),
            ),
        )

    def lineage_inputs(
        self,
        version_id: str,
        *,
        producing_cell_id: str | None = None,
    ) -> list[dict]:
        producer_clause = (
            " AND le.producing_cell_id IS ?" if producing_cell_id is not None else ""
        )
        params: tuple[Any, ...] = (
            (version_id, producing_cell_id)
            if producing_cell_id is not None
            else (version_id,)
        )
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT le.input_version_id, av.filename, av.path "
                "FROM lineage_edges le LEFT JOIN artifact_versions av "
                "ON le.input_version_id=av.version_id "
                "WHERE le.output_version_id=?" + producer_clause,
                params,
            ).fetchall()
        return [
            {
                "version_id": row["input_version_id"],
                "filename": row["filename"],
                "path": row["path"],
            }
            for row in rows
        ]

    def lineage_edges_for(self, version_id: str, direction: str) -> list[dict]:
        column_from = "output_version_id" if direction == "up" else "input_version_id"
        column_to = "input_version_id" if direction == "up" else "output_version_id"
        with self._lock:
            rows = self._connection.execute(
                f"SELECT DISTINCT {column_to} AS nxt FROM lineage_edges "
                f"WHERE {column_from}=?",
                (version_id,),
            ).fetchall()
        return [row["nxt"] for row in rows]

    def producing_cell_for_version(self, version_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT el.code, el.frame_id, el.producing_cell_id "
                "FROM artifact_versions av "
                "LEFT JOIN execution_log el "
                "ON av.producing_cell_id=el.producing_cell_id "
                "WHERE av.version_id=?",
                (version_id,),
            ).fetchone()
        return dict(row) if row and row["code"] is not None else None

    def _execute(self, sql: str, params: tuple = ()) -> None:
        if self._execute_callback is not None:
            self._execute_callback(sql, params)
            return
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = [
    "ArtifactRepository",
    "env_snapshot_id",
    "file_identity",
    "same_file_path",
]

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

Clock = Callable[[], int]
Execute = Callable[[str, tuple], None]
GetFrame = Callable[[str], dict | None]
ResolveFrameScope = Callable[..., dict]
ResolveArtifactWriteScope = Callable[..., tuple[bool, str | None, str]]
GetArtifact = Callable[[str], dict | None]
GetEnvironmentSnapshot = Callable[[str], dict | None]
FileIdentity = Callable[[str], str | None]
SameFilePath = Callable[[str, str], bool]


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
            rows = self._connection.execute(
                "SELECT version_id,path,snapshot_path,env_snapshot_id "
                "FROM artifact_versions WHERE artifact_id=?",
                (artifact_id,),
            ).fetchall()
            version_ids = tuple(str(row["version_id"]) for row in rows)
            env_snapshot_ids = tuple(
                dict.fromkeys(
                    str(row["env_snapshot_id"])
                    for row in rows
                    if row["env_snapshot_id"]
                )
            )
            paths = {
                path
                for row in rows
                for path in (row["path"], row["snapshot_path"])
                if path
            }
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
                "UPDATE plans SET artifact_id=NULL WHERE artifact_id=?", (artifact_id,)
            )
            if env_snapshot_ids:
                marks = "(" + ",".join("?" for _ in env_snapshot_ids) + ")"
                self._connection.execute(
                    "DELETE FROM env_snapshots WHERE snapshot_id IN "
                    f"{marks} AND NOT EXISTS (SELECT 1 FROM artifact_versions "
                    "WHERE artifact_versions.env_snapshot_id="
                    "env_snapshots.snapshot_id)",
                    env_snapshot_ids,
                )
            self._connection.commit()
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
    ) -> dict:
        """Atomically record or finalize one cell's physical file write."""
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
        with self._lock:
            try:
                artifact = None
                candidate = None
                root_clause = (
                    "a.root_frame_id=?"
                    if resolved_root is not None
                    else "a.root_frame_id IS NULL"
                )
                root_args = (resolved_root,) if resolved_root is not None else ()

                if producing_cell_id and checksum is not None:
                    exact_rows = self._connection.execute(
                        "SELECT v.*,a.latest_version_id AS artifact_latest_version_id,"
                        "CASE WHEN a.filename=? THEN 0 ELSE 1 END AS filename_rank "
                        "FROM artifact_versions v JOIN artifacts a "
                        "ON a.artifact_id=v.artifact_id WHERE a.project_id=? AND "
                        + root_clause
                        + " AND v.producing_cell_id=? AND v.checksum=? "
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
                        if row["artifact_latest_version_id"] == row[
                            "version_id"
                        ] and self._paths_match(row["path"], path):
                            candidate = row
                            break

                reuse = candidate is not None and (
                    reuse_policy == "any" or not candidate["snapshot_path"]
                )

                if reuse:
                    artifact = self._connection.execute(
                        "SELECT rowid AS artifact_rowid,* FROM artifacts "
                        "WHERE artifact_id=?",
                        (candidate["artifact_id"],),
                    ).fetchone()
                else:
                    artifact = self._connection.execute(
                        "SELECT rowid AS artifact_rowid,* FROM artifacts a "
                        "WHERE a.filename=? AND a.project_id=? AND "
                        + root_clause
                        + " ORDER BY a.created_at DESC,a.rowid DESC LIMIT 1",
                        (filename, resolved_project, *root_args),
                    ).fetchone()

                if reuse:
                    artifact_id = candidate["artifact_id"]
                    version_id = candidate["version_id"]
                    created_at = candidate["created_at"]
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
                for input_version_id in input_version_ids or ():
                    if (
                        not input_version_id
                        or input_version_id == version_id
                        or input_version_id in seen_inputs
                    ):
                        continue
                    seen_inputs.add(input_version_id)
                    exists = self._connection.execute(
                        "SELECT 1 FROM lineage_edges WHERE input_version_id=? "
                        "AND output_version_id=? LIMIT 1",
                        (input_version_id, version_id),
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
                            frame_id,
                            now,
                        ),
                    )
                stored_version = self._connection.execute(
                    "SELECT * FROM artifact_versions WHERE version_id=?",
                    (version_id,),
                ).fetchone()
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "filename": stored_version["filename"],
            "path": stored_version["path"],
            "content_type": stored_version["content_type"],
            "size_bytes": stored_version["size_bytes"],
            "checksum": stored_version["checksum"],
            "created_at": created_at,
        }

    def record_artifact_restore(
        self,
        *,
        artifact_id: str,
        source_version_id: str,
        expected_latest_version_id: str,
        version_id: str,
        path: str,
        snapshot_path: str,
        size_bytes: int,
        checksum: str,
        frame_id: str | None,
        root_frame_id: str | None = None,
        project_id: str | None = None,
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
                    raise KeyError("artifact restore source not found")
                if artifact["latest_version_id"] != expected_latest_version_id:
                    raise RuntimeError("artifact changed concurrently during restore")
                if artifact["latest_version_id"] == source_version_id:
                    raise ValueError("restore source is already the latest version")
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
                self._connection.execute(
                    "INSERT INTO artifact_versions(version_id,artifact_id,"
                    "filename,content_type,size_bytes,checksum,path,"
                    "snapshot_path,producing_cell_id,frame_id,created_at,"
                    "env_snapshot_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
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
                self._connection.commit()
            except Exception:
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
        # The interpreter and environment name are part of the identity, not
        # decoration: without them an R kernel and a Python one in a conda env
        # collapse onto the same row whenever their package lists happen to
        # match -- and which environment produced a result is precisely what
        # provenance is for.
        basis = "|".join(
            [
                snapshot.get("kind") or "",
                snapshot.get("python_version") or "",
                snapshot.get("implementation") or "",
                snapshot.get("platform") or "",
                str(snapshot.get("interpreter") or ""),
                str(snapshot.get("environment_name") or ""),
                packages_json,
                remote_json,
            ]
        )
        snapshot_id = "env-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM env_snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
            if not exists:
                self._connection.execute(
                    "INSERT INTO env_snapshots(snapshot_id,created_at,kind,"
                    "python_version,implementation,platform,package_count,"
                    "packages_json,remote_json,interpreter,environment_name,"
                    "generation_id,packages_unavailable) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        snapshot.get("packages_unavailable"),
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

    def version_for_path(self, path: str) -> str | None:
        with self._lock:
            exact = self._connection.execute(
                "SELECT version_id,created_at,rowid AS version_rowid "
                "FROM artifact_versions WHERE path=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (str(path),),
            ).fetchone()
            identity = self._identify_file(path)
            if identity is None:
                return exact["version_id"] if exact else None
            if exact:
                candidates = self._connection.execute(
                    "SELECT version_id,path FROM artifact_versions WHERE "
                    "created_at>? OR (created_at=? AND rowid>?) "
                    "ORDER BY created_at DESC, rowid DESC",
                    (
                        exact["created_at"],
                        exact["created_at"],
                        exact["version_rowid"],
                    ),
                ).fetchall()
            else:
                candidates = self._connection.execute(
                    "SELECT version_id,path FROM artifact_versions "
                    "ORDER BY created_at DESC, rowid DESC"
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

    def list_versions(self, artifact_id: str) -> list[dict]:
        with self._lock:
            latest = self._connection.execute(
                "SELECT latest_version_id FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            rows = self._connection.execute(
                "SELECT version_id,filename,content_type,size_bytes,checksum,"
                "producing_cell_id,frame_id,created_at FROM artifact_versions "
                "WHERE artifact_id=? ORDER BY created_at DESC, rowid DESC",
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

    def lineage_inputs(self, version_id: str) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT le.input_version_id, av.filename, av.path "
                "FROM lineage_edges le LEFT JOIN artifact_versions av "
                "ON le.input_version_id=av.version_id "
                "WHERE le.output_version_id=?",
                (version_id,),
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
                f"SELECT {column_to} AS nxt FROM lineage_edges "
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


__all__ = ["ArtifactRepository", "file_identity", "same_file_path"]

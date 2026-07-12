"""Immutable, content-addressed Skill packages and activation history.

The filesystem remains the runtime import surface, but it is never the only
copy of a user-authored Skill.  Every install/upgrade/publish captures a
bounded package in SQLite before an installation pointer is changed.  Package
versions and blobs are immutable; installation events are append-only.

This repository deliberately knows nothing about prompt construction or
Python imports.  :mod:`openai4s.skills_loader.versions` owns safe package
validation and materialization while this module owns the atomic database
pointer and optimistic-concurrency boundary.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import unicodedata
import uuid
from typing import Any, Callable, Mapping

SKILL_VERSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_blobs (
    sha256       TEXT PRIMARY KEY,
    size_bytes   INTEGER NOT NULL,
    content      BLOB NOT NULL,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_versions (
    version_id       TEXT PRIMARY KEY,
    manifest_sha256  TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    normalized_name  TEXT NOT NULL,
    slug             TEXT NOT NULL,
    origin           TEXT NOT NULL,
    document_sha256  TEXT NOT NULL,
    sidecar_sha256   TEXT,
    manifest         TEXT NOT NULL,
    created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_skill_versions_name
    ON skill_versions(normalized_name, created_at);

CREATE TABLE IF NOT EXISTS skill_version_files (
    version_id   TEXT NOT NULL,
    path         TEXT NOT NULL,
    blob_sha256  TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    PRIMARY KEY(version_id, path),
    FOREIGN KEY(version_id) REFERENCES skill_versions(version_id),
    FOREIGN KEY(blob_sha256) REFERENCES skill_blobs(sha256)
);

CREATE TABLE IF NOT EXISTS skill_installations (
    installation_id       TEXT PRIMARY KEY,
    scope                 TEXT NOT NULL,
    scope_id              TEXT NOT NULL DEFAULT '',
    name                  TEXT NOT NULL,
    normalized_name       TEXT NOT NULL,
    slug                  TEXT NOT NULL,
    active_version_id     TEXT,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    UNIQUE(scope, scope_id, normalized_name),
    FOREIGN KEY(active_version_id) REFERENCES skill_versions(version_id)
);
CREATE INDEX IF NOT EXISTS ix_skill_installations_active
    ON skill_installations(scope, scope_id, active_version_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_installations_active_slug
    ON skill_installations(scope, scope_id, slug)
    WHERE active_version_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS skill_installation_events (
    event_id          TEXT PRIMARY KEY,
    installation_id   TEXT NOT NULL,
    event             TEXT NOT NULL,
    from_version_id   TEXT,
    to_version_id     TEXT,
    metadata          TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    FOREIGN KEY(installation_id) REFERENCES skill_installations(installation_id)
);
CREATE INDEX IF NOT EXISTS ix_skill_installation_events
    ON skill_installation_events(installation_id, created_at);
"""

_ACTIVATION_EVENTS = frozenset({"installed", "upgraded", "published", "rolled_back"})


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(normalized.split()).casefold()


def _validated_scope(scope: str, scope_id: str | None) -> tuple[str, str]:
    value = str(scope or "").strip().lower()
    identity = str(scope_id or "").strip()
    if value not in {"personal", "project"}:
        raise ValueError("skill scope must be 'personal' or 'project'")
    if value == "personal" and identity:
        raise ValueError("personal skill scope_id must be empty")
    if value == "project" and not identity:
        raise ValueError("project skill scope requires project_id")
    if len(identity) > 512:
        raise ValueError("skill scope_id is too long")
    return value, identity


def _installation_id(scope: str, scope_id: str, normalized_name: str) -> str:
    digest = hashlib.sha256(
        f"{scope}\0{scope_id}\0{normalized_name}".encode("utf-8")
    ).hexdigest()
    return f"skill-install-{digest[:32]}"


class SkillVersionRepository:
    """Store immutable Skill bytes and atomically switch active versions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: threading.RLock,
        *,
        clock_ms: Callable[[], int],
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        with self._lock:
            self._connection.executescript(SKILL_VERSION_SCHEMA)
            self._connection.commit()

    @staticmethod
    def _version_record(row: sqlite3.Row | Mapping[str, Any]) -> dict:
        item = dict(row)
        raw = item.get("manifest")
        item["manifest"] = json.loads(raw) if isinstance(raw, str) else raw
        return item

    @staticmethod
    def _installation_record(row: sqlite3.Row | Mapping[str, Any]) -> dict:
        return dict(row)

    def put_version(self, manifest: dict, files: Mapping[str, bytes]) -> dict:
        """Persist one immutable package, de-duplicating identical blobs.

        The caller has already performed package/path/size validation.  This
        method independently verifies every declared digest so a buggy caller
        cannot create a manifest that points at different bytes.
        """

        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ValueError("unsupported skill manifest schema")
        name = str(manifest.get("name") or "").strip()
        slug = str(manifest.get("slug") or "").strip()
        origin = str(manifest.get("origin") or "").strip()
        if not name or not slug or not origin:
            raise ValueError("skill manifest name, slug, and origin are required")
        declared = manifest.get("files")
        if not isinstance(declared, list) or not declared:
            raise ValueError("skill manifest files are required")
        declared_by_path: dict[str, dict] = {}
        for entry in declared:
            if not isinstance(entry, dict):
                raise ValueError("invalid skill manifest file entry")
            path = str(entry.get("path") or "")
            if not path or path in declared_by_path:
                raise ValueError("duplicate or empty skill manifest path")
            declared_by_path[path] = entry
        if set(declared_by_path) != set(files):
            raise ValueError("skill manifest does not match supplied files")
        if "SKILL.md" not in declared_by_path:
            raise ValueError("skill manifest must contain SKILL.md")
        if manifest.get("document_sha256") != declared_by_path["SKILL.md"].get(
            "sha256"
        ):
            raise ValueError("skill manifest document digest mismatch")
        sidecar = manifest.get("sidecar") or {}
        sidecar_present = "kernel.py" in declared_by_path
        if bool(sidecar.get("present")) != sidecar_present:
            raise ValueError("skill manifest sidecar presence mismatch")
        if sidecar_present and (
            sidecar.get("sha256") != declared_by_path["kernel.py"].get("sha256")
            or sidecar.get("size") != declared_by_path["kernel.py"].get("size")
        ):
            raise ValueError("skill manifest sidecar digest mismatch")
        normalized_files: dict[str, bytes] = {}
        for path, value in files.items():
            if not isinstance(value, (bytes, bytearray, memoryview)):
                raise TypeError("skill file values must be bytes")
            content = bytes(value)
            digest = hashlib.sha256(content).hexdigest()
            entry = declared_by_path[path]
            if digest != entry.get("sha256") or len(content) != entry.get("size"):
                raise ValueError(f"skill file digest mismatch: {path}")
            normalized_files[path] = content

        encoded_manifest = _canonical_json(manifest)
        manifest_sha256 = hashlib.sha256(encoded_manifest.encode("utf-8")).hexdigest()
        version_id = f"skillv-{manifest_sha256}"
        now = self._clock_ms()
        document_sha256 = str(manifest.get("document_sha256") or "")
        sidecar_sha256 = sidecar.get("sha256") if sidecar.get("present") else None

        with self._lock, self._connection:
            for path in sorted(normalized_files):
                content = normalized_files[path]
                digest = hashlib.sha256(content).hexdigest()
                existing = self._connection.execute(
                    "SELECT size_bytes,content FROM skill_blobs WHERE sha256=?",
                    (digest,),
                ).fetchone()
                if existing is not None:
                    if (
                        int(existing["size_bytes"]) != len(content)
                        or bytes(existing["content"]) != content
                    ):
                        raise ValueError("content-addressed Skill blob collision")
                else:
                    self._connection.execute(
                        "INSERT INTO skill_blobs(sha256,size_bytes,content,created_at) "
                        "VALUES(?,?,?,?)",
                        (digest, len(content), sqlite3.Binary(content), now),
                    )

            existing_version = self._connection.execute(
                "SELECT manifest FROM skill_versions WHERE version_id=?",
                (version_id,),
            ).fetchone()
            if existing_version is not None:
                if existing_version["manifest"] != encoded_manifest:
                    raise ValueError("content-addressed Skill manifest collision")
            else:
                self._connection.execute(
                    "INSERT INTO skill_versions(version_id,manifest_sha256,name,"
                    "normalized_name,slug,origin,document_sha256,sidecar_sha256,"
                    "manifest,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id,
                        manifest_sha256,
                        name,
                        _canonical_name(name),
                        slug,
                        origin,
                        document_sha256,
                        sidecar_sha256,
                        encoded_manifest,
                        now,
                    ),
                )
                self._connection.executemany(
                    "INSERT INTO skill_version_files(version_id,path,blob_sha256,"
                    "size_bytes) VALUES(?,?,?,?)",
                    [
                        (
                            version_id,
                            path,
                            declared_by_path[path]["sha256"],
                            declared_by_path[path]["size"],
                        )
                        for path in sorted(declared_by_path)
                    ],
                )
        return self.get_version(version_id, include_files=False)

    def get_version(self, version_id: str, *, include_files: bool = False) -> dict:
        with self._lock:
            row = self._connection.execute(
                "SELECT version_id,manifest_sha256,name,normalized_name,slug,origin,"
                "document_sha256,sidecar_sha256,manifest,created_at "
                "FROM skill_versions WHERE version_id=?",
                (str(version_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown Skill version: {version_id!r}")
            result = self._version_record(row)
            if include_files:
                file_rows = self._connection.execute(
                    "SELECT f.path,f.blob_sha256,f.size_bytes,b.content "
                    "FROM skill_version_files AS f JOIN skill_blobs AS b "
                    "ON b.sha256=f.blob_sha256 WHERE f.version_id=? "
                    "ORDER BY f.path",
                    (str(version_id),),
                ).fetchall()
        if include_files:
            files: dict[str, bytes] = {}
            for file_row in file_rows:
                content = bytes(file_row["content"])
                if len(content) != int(file_row["size_bytes"]):
                    raise ValueError("stored Skill blob size mismatch")
                digest = hashlib.sha256(content).hexdigest()
                if digest != file_row["blob_sha256"]:
                    raise ValueError("stored Skill blob digest mismatch")
                files[str(file_row["path"])] = content
            result["files"] = files
        return result

    def get_installation(
        self,
        name: str,
        *,
        scope: str = "personal",
        scope_id: str | None = None,
    ) -> dict | None:
        scope, scope_id = _validated_scope(scope, scope_id)
        normalized_name = _canonical_name(name)
        with self._lock:
            row = self._connection.execute(
                "SELECT installation_id,scope,scope_id,name,normalized_name,slug,"
                "active_version_id,created_at,updated_at FROM skill_installations "
                "WHERE scope=? AND scope_id=? AND normalized_name=?",
                (scope, scope_id, normalized_name),
            ).fetchone()
        return self._installation_record(row) if row is not None else None

    def get_active_by_slug(
        self,
        slug: str,
        *,
        scope: str = "personal",
        scope_id: str | None = None,
    ) -> dict | None:
        scope, scope_id = _validated_scope(scope, scope_id)
        with self._lock:
            row = self._connection.execute(
                "SELECT installation_id,scope,scope_id,name,normalized_name,slug,"
                "active_version_id,created_at,updated_at FROM skill_installations "
                "WHERE scope=? AND scope_id=? AND slug=? "
                "AND active_version_id IS NOT NULL",
                (scope, scope_id, str(slug or "")),
            ).fetchone()
        return self._installation_record(row) if row is not None else None

    def activate(
        self,
        name: str,
        slug: str,
        version_id: str,
        *,
        scope: str = "personal",
        scope_id: str | None = None,
        event: str = "installed",
        expected_active_version_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """CAS-switch an installation pointer and append its lifecycle event."""

        scope, scope_id = _validated_scope(scope, scope_id)
        display_name = str(name or "").strip()
        normalized_name = _canonical_name(display_name)
        slug = str(slug or "").strip()
        event = str(event or "").strip().lower()
        if not display_name or not normalized_name or not slug or not event:
            raise ValueError("skill activation identity and event are required")
        if event not in _ACTIVATION_EVENTS:
            raise ValueError(f"unsupported Skill activation event: {event!r}")
        version = self.get_version(version_id, include_files=False)
        if version["normalized_name"] != normalized_name or version["slug"] != slug:
            raise ValueError("Skill version identity does not match installation")
        now = self._clock_ms()
        installation_id = _installation_id(scope, scope_id, normalized_name)
        event_id = f"skill-event-{uuid.uuid4().hex}"
        encoded_metadata = _canonical_json(metadata or {})
        with self._lock, self._connection:
            current = self._connection.execute(
                "SELECT active_version_id FROM skill_installations "
                "WHERE scope=? AND scope_id=? AND normalized_name=?",
                (scope, scope_id, normalized_name),
            ).fetchone()
            actual = current["active_version_id"] if current is not None else None
            if actual != expected_active_version_id:
                raise RuntimeError(
                    "Skill changed concurrently: expected active version "
                    f"{expected_active_version_id!r}, found {actual!r}"
                )
            if current is None:
                self._connection.execute(
                    "INSERT INTO skill_installations(installation_id,scope,scope_id,"
                    "name,normalized_name,slug,active_version_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        installation_id,
                        scope,
                        scope_id,
                        display_name,
                        normalized_name,
                        slug,
                        version_id,
                        now,
                        now,
                    ),
                )
            else:
                self._connection.execute(
                    "UPDATE skill_installations SET name=?,slug=?,active_version_id=?,"
                    "updated_at=? WHERE installation_id=?",
                    (display_name, slug, version_id, now, installation_id),
                )
            self._connection.execute(
                "INSERT INTO skill_installation_events(event_id,installation_id,"
                "event,from_version_id,to_version_id,metadata,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    event_id,
                    installation_id,
                    event,
                    actual,
                    version_id,
                    encoded_metadata,
                    now,
                ),
            )
        result = self.get_installation(
            display_name,
            scope=scope,
            scope_id=scope_id,
        )
        assert result is not None
        return {**result, "event_id": event_id, "event": event}

    def deactivate(
        self,
        name: str,
        *,
        scope: str = "personal",
        scope_id: str | None = None,
        expected_active_version_id: str | None,
        metadata: dict | None = None,
    ) -> dict:
        scope, scope_id = _validated_scope(scope, scope_id)
        installation = self.get_installation(name, scope=scope, scope_id=scope_id)
        if installation is None:
            raise KeyError(f"no installed Skill: {name!r}")
        actual = installation["active_version_id"]
        if actual != expected_active_version_id:
            raise RuntimeError(
                "Skill changed concurrently: expected active version "
                f"{expected_active_version_id!r}, found {actual!r}"
            )
        now = self._clock_ms()
        event_id = f"skill-event-{uuid.uuid4().hex}"
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE skill_installations SET active_version_id=NULL,updated_at=? "
                "WHERE installation_id=? AND active_version_id=?",
                (now, installation["installation_id"], actual),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Skill changed concurrently during delete")
            self._connection.execute(
                "INSERT INTO skill_installation_events(event_id,installation_id,"
                "event,from_version_id,to_version_id,metadata,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    event_id,
                    installation["installation_id"],
                    "deleted",
                    actual,
                    None,
                    _canonical_json(metadata or {}),
                    now,
                ),
            )
        return {"ok": True, "event_id": event_id, "deleted": installation["name"]}

    def history(
        self,
        name: str,
        *,
        scope: str = "personal",
        scope_id: str | None = None,
        limit: int = 200,
    ) -> dict:
        scope, scope_id = _validated_scope(scope, scope_id)
        installation = self.get_installation(name, scope=scope, scope_id=scope_id)
        if installation is None:
            raise KeyError(f"no installed Skill: {name!r}")
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_id,event,from_version_id,to_version_id,metadata,"
                "created_at FROM skill_installation_events WHERE installation_id=? "
                "ORDER BY created_at DESC,rowid DESC LIMIT ?",
                (installation["installation_id"], max(1, min(int(limit), 2000))),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.get("metadata") or "{}")
            events.append(item)
        return {"installation": installation, "events": events}

    def version_belongs_to(
        self,
        name: str,
        version_id: str,
        *,
        scope: str = "personal",
        scope_id: str | None = None,
    ) -> bool:
        installation = self.get_installation(name, scope=scope, scope_id=scope_id)
        if installation is None:
            return False
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM skill_installation_events WHERE installation_id=? "
                "AND (from_version_id=? OR to_version_id=?) LIMIT 1",
                (installation["installation_id"], version_id, version_id),
            ).fetchone()
        return row is not None

    def list_active(
        self,
        *,
        scope: str,
        scope_id: str | None = None,
    ) -> list[dict]:
        scope, scope_id = _validated_scope(scope, scope_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT installation_id,scope,scope_id,name,normalized_name,slug,"
                "active_version_id,created_at,updated_at FROM skill_installations "
                "WHERE scope=? AND scope_id=? AND active_version_id IS NOT NULL "
                "ORDER BY normalized_name",
                (scope, scope_id),
            ).fetchall()
        return [self._installation_record(row) for row in rows]


__all__ = ["SKILL_VERSION_SCHEMA", "SkillVersionRepository"]

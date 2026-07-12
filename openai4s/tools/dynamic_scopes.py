"""Immutable scope manifests and append-only Dynamic Tool activations.

Model-authored implementation source remains data in this store.  This module
never compiles or imports it: :mod:`openai4s.tools.dynamic` validates records
and the one-shot sandbox worker is still the only execution boundary.

Project/global versions are content-addressed immutable JSON documents.  The
currently active version is reduced from immutable activation events instead
of a mutable pointer, so promotion, explicit activation, and rollback retain a
small durable audit trail.  Scope identifiers are hashed for directory names
and are also checked inside every event; a project id can therefore never be
used as a path or confused with another project's records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

_MANIFEST_ID = re.compile(r"^dyn-[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^dya-[0-9a-f]{64}$")
_OPERATIONS = frozenset({"promote", "activate", "rollback"})
_SCOPES = frozenset({"project", "global"})
_MAX_RECORD_BYTES = 250_000

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _scope_key(scope: str, scope_id: str) -> str:
    return hashlib.sha256(f"{scope}\0{scope_id}".encode("utf-8")).hexdigest()


class DynamicScopeStore:
    """File-backed immutable version and activation history store."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.manifests_dir = self.root / "manifests"
        self.events_dir = self.root / "events"
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.clock_ns = clock_ns
        self._lock = _lock_for(self.root)

    def write_manifest(self, record: Mapping[str, Any]) -> Path:
        """Atomically persist one already-validated content-addressed record."""

        manifest_id = str(record.get("manifest_id") or "")
        if not _MANIFEST_ID.fullmatch(manifest_id):
            raise ValueError("invalid dynamic manifest id")
        destination = self.manifests_dir / f"{manifest_id}.json"
        encoded = _canonical_json(dict(record))
        if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise ValueError("dynamic manifest record is too large")
        with self._lock:
            if destination.exists():
                existing = json.loads(destination.read_text("utf-8"))
                comparable_existing = (
                    dict(existing) if isinstance(existing, dict) else {}
                )
                comparable_new = dict(record)
                for key in ("created_at", "expires_at"):
                    comparable_existing.pop(key, None)
                    comparable_new.pop(key, None)
                if comparable_existing != comparable_new:
                    raise ValueError("dynamic manifest id collision")
                return destination
            self._atomic_write(destination, encoded)
        return destination

    def manifest_records(self) -> tuple[list[dict[str, Any]], list[str]]:
        records: list[dict[str, Any]] = []
        errors: list[str] = []
        with self._lock:
            paths = sorted(self.manifests_dir.glob("dyn-*.json"))
            for path in paths:
                try:
                    records.append(self._read_record(path, _MANIFEST_ID, "manifest"))
                except Exception as error:  # noqa: BLE001 - corrupt data stays inert
                    errors.append(f"{path.name}: {error}")
        return records, errors

    def append_activation(
        self,
        *,
        operation: str,
        scope: str,
        scope_id: str,
        name: str,
        manifest_id: str,
        actor_root_frame_id: str,
        actor_project_id: str,
    ) -> dict[str, Any]:
        if operation not in _OPERATIONS:
            raise ValueError("unknown dynamic activation operation")
        self._validate_scope(scope, scope_id)
        if not _MANIFEST_ID.fullmatch(manifest_id):
            raise ValueError("invalid activated manifest id")
        if not name or not actor_root_frame_id or not actor_project_id:
            raise ValueError(
                "dynamic activation requires bound actor and tool identity"
            )
        # The previous pointer and ordering timestamp are derived while holding
        # the shared root lock. A caller's earlier snapshot may already be stale
        # because another session can promote the same project/global tool.
        with self._lock:
            history, _errors = self.events(
                scope=scope,
                scope_id=scope_id,
                name=name,
            )
            previous = str(history[-1]["manifest_id"]) if history else None
            return self._append_event_locked(
                operation=operation,
                scope=scope,
                scope_id=scope_id,
                name=name,
                manifest_id=manifest_id,
                previous_manifest_id=previous,
                actor_root_frame_id=actor_root_frame_id,
                actor_project_id=actor_project_id,
                history=history,
            )

    def append_rollback(
        self,
        *,
        scope: str,
        scope_id: str,
        name: str,
        available_manifest_ids: set[str],
        actor_root_frame_id: str,
        actor_project_id: str,
    ) -> dict[str, Any]:
        """Atomically select and activate the prior distinct valid version."""

        self._validate_scope(scope, scope_id)
        if not name or not actor_root_frame_id or not actor_project_id:
            raise ValueError("dynamic rollback requires bound actor and tool identity")
        with self._lock:
            history, _errors = self.events(
                scope=scope,
                scope_id=scope_id,
                name=name,
            )
            if not history:
                raise ValueError(f"dynamic tool {name!r} has no active {scope} version")
            current = str(history[-1]["manifest_id"])
            target = next(
                (
                    str(event["manifest_id"])
                    for event in reversed(history[:-1])
                    if event.get("manifest_id") != current
                    and event.get("manifest_id") in available_manifest_ids
                ),
                None,
            )
            if target is None:
                raise ValueError(
                    f"dynamic tool {name!r} has no previous {scope} version"
                )
            return self._append_event_locked(
                operation="rollback",
                scope=scope,
                scope_id=scope_id,
                name=name,
                manifest_id=target,
                previous_manifest_id=current,
                actor_root_frame_id=actor_root_frame_id,
                actor_project_id=actor_project_id,
                history=history,
            )

    def _append_event_locked(
        self,
        *,
        operation: str,
        scope: str,
        scope_id: str,
        name: str,
        manifest_id: str,
        previous_manifest_id: str | None,
        actor_root_frame_id: str,
        actor_project_id: str,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        observed_ns = int(self.clock_ns())
        previous_ns = max(
            (int(event.get("created_at_ns") or 0) for event in history),
            default=0,
        )
        core = {
            "version": 1,
            "operation": operation,
            "scope": scope,
            "scope_id": scope_id,
            "name": name,
            "manifest_id": manifest_id,
            "previous_manifest_id": previous_manifest_id,
            "actor_root_frame_id": actor_root_frame_id,
            "actor_project_id": actor_project_id,
            "created_at": float(self.clock()),
            "created_at_ns": max(observed_ns, previous_ns + 1),
            "nonce": uuid.uuid4().hex,
        }
        event = {**core, "event_id": "dya-" + _digest(core)}
        directory = self.events_dir / scope / _scope_key(scope, scope_id)
        destination = directory / f"{event['event_id']}.json"
        self._atomic_write(destination, _canonical_json(event))
        return self.public_event(event)

    def events(
        self,
        *,
        scope: str,
        scope_id: str,
        name: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        self._validate_scope(scope, scope_id)
        directory = self.events_dir / scope / _scope_key(scope, scope_id)
        events: list[dict[str, Any]] = []
        errors: list[str] = []
        with self._lock:
            for path in sorted(directory.glob("dya-*.json")):
                try:
                    event = self._read_record(path, _EVENT_ID, "event")
                    event_id = str(event.pop("event_id", ""))
                    if event_id != "dya-" + _digest(event):
                        raise ValueError("activation event content hash mismatch")
                    event["event_id"] = event_id
                    if event.get("version") != 1:
                        raise ValueError("unsupported activation event version")
                    if event.get("scope") != scope or event.get("scope_id") != scope_id:
                        raise ValueError("activation event scope mismatch")
                    if event.get("operation") not in _OPERATIONS:
                        raise ValueError("activation event operation is invalid")
                    if not _MANIFEST_ID.fullmatch(str(event.get("manifest_id") or "")):
                        raise ValueError("activation event manifest id is invalid")
                    previous = event.get("previous_manifest_id")
                    if previous is not None and not _MANIFEST_ID.fullmatch(
                        str(previous)
                    ):
                        raise ValueError(
                            "activation event previous manifest id is invalid"
                        )
                    if not event.get("name") or not event.get("actor_root_frame_id"):
                        raise ValueError("activation event actor/name is invalid")
                    actor_project = str(event.get("actor_project_id") or "")
                    if not actor_project:
                        raise ValueError("activation event project actor is invalid")
                    if scope == "project" and actor_project != scope_id:
                        raise ValueError("activation event actor/scope mismatch")
                    if name is None or event.get("name") == name:
                        events.append(event)
                except Exception as error:  # noqa: BLE001 - corrupt data stays inert
                    errors.append(f"{path.name}: {error}")
        events.sort(
            key=lambda item: (int(item.get("created_at_ns") or 0), item["event_id"])
        )
        return events, errors

    def active_manifest_id(
        self,
        *,
        scope: str,
        scope_id: str,
        name: str,
    ) -> tuple[str | None, list[str]]:
        events, errors = self.events(scope=scope, scope_id=scope_id, name=name)
        return (str(events[-1]["manifest_id"]) if events else None), errors

    def delete_project_scope(self, project_id: str) -> dict[str, int]:
        """Remove one deleted project's activation history and manifests.

        Project identity is part of a scoped manifest's content hash, so a
        project manifest cannot be shared with another project or global
        scope. Corrupt records fail closed and remain for manual inspection.
        """

        self._validate_scope("project", project_id)
        directory = self.events_dir / "project" / _scope_key("project", project_id)
        removed_events = 0
        removed_manifests = 0
        with self._lock:
            if directory.is_dir() and not directory.is_symlink():
                removed_events = sum(
                    1 for path in directory.iterdir() if path.is_file()
                )
                try:
                    shutil.rmtree(directory)
                except OSError:
                    removed_events = 0
            elif directory.is_symlink():
                # Never follow a corrupted on-disk scope link.
                try:
                    directory.unlink()
                except OSError:
                    pass

            scope_removed = not directory.exists() and not directory.is_symlink()
            for path in (
                sorted(self.manifests_dir.glob("dyn-*.json")) if scope_removed else ()
            ):
                try:
                    record = self._read_record(path, _MANIFEST_ID, "manifest")
                except Exception:  # noqa: BLE001 - corrupt data stays inert
                    continue
                if (
                    record.get("scope") == "project"
                    and record.get("scope_id") == project_id
                ):
                    try:
                        path.unlink()
                        removed_manifests += 1
                    except OSError:
                        pass
            try:
                directory.parent.rmdir()
            except OSError:
                pass
        return {
            "events": removed_events,
            "manifests": removed_manifests,
        }

    @staticmethod
    def public_event(event: Mapping[str, Any]) -> dict[str, Any]:
        """Return the audit fields; never expose implementation source."""

        return {
            key: event.get(key)
            for key in (
                "event_id",
                "operation",
                "scope",
                "scope_id",
                "name",
                "manifest_id",
                "previous_manifest_id",
                "actor_root_frame_id",
                "actor_project_id",
                "created_at",
                "created_at_ns",
            )
        }

    @staticmethod
    def _validate_scope(scope: str, scope_id: str) -> None:
        if scope not in _SCOPES:
            raise ValueError("dynamic activation scope must be project or global")
        if scope == "project" and not scope_id:
            raise ValueError("project dynamic activation requires project_id")
        if scope == "global" and scope_id:
            raise ValueError("global dynamic activation scope_id must be empty")

    @staticmethod
    def _read_record(
        path: Path,
        identifier_pattern: re.Pattern[str],
        kind: str,
    ) -> dict[str, Any]:
        if path.stat().st_size > _MAX_RECORD_BYTES:
            raise ValueError(f"dynamic {kind} record is too large")
        value = json.loads(path.read_text("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"dynamic {kind} record must be an object")
        identifier = str(value.get(f"{kind}_id") or "")
        if not identifier_pattern.fullmatch(identifier) or path.stem != identifier:
            raise ValueError(f"dynamic {kind} filename/id mismatch")
        return value

    @staticmethod
    def _atomic_write(destination: Path, encoded: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


__all__ = ["DynamicScopeStore"]

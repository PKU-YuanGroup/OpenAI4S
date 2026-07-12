"""Content-addressed workspace snapshots and append-only session checkpoints.

The workspace is deliberately *not* implemented with the user's Git checkout:
capturing or restoring a session must never alter an index, branch, or working
tree owned by the researcher.  :class:`WorkspaceCAS` stores immutable blobs and
tree manifests below OpenAI4S' data directory using only the standard library.

``SessionSnapshotRepository`` stores the small, structured checkpoint envelope
in SQLite.  It does not pickle a Python/R namespace.  A checkpoint records the
facts needed by the recovery pipeline (ledger/cell cursors, artifact versions,
environment pins, generation references, capabilities, permissions, and a
safe recovery recipe); the kernel recovery service decides what can actually
be replayed and validated.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, TypedDict

SNAPSHOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_branches (
    branch_id             TEXT PRIMARY KEY,
    root_frame_id         TEXT NOT NULL,
    parent_branch_id      TEXT,
    base_checkpoint_id    TEXT,
    head_checkpoint_id    TEXT,
    name                  TEXT,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_session_branch_root
    ON session_branches(root_frame_id, created_at);

CREATE TABLE IF NOT EXISTS session_checkpoints (
    checkpoint_id         TEXT PRIMARY KEY,
    root_frame_id         TEXT NOT NULL,
    branch_id             TEXT NOT NULL,
    parent_checkpoint_id  TEXT,
    source_kind           TEXT,
    source_id             TEXT,
    internal              INTEGER NOT NULL DEFAULT 0,
    reason                TEXT NOT NULL,
    action_cursor         INTEGER,
    message_cursor        INTEGER,
    cell_cursor           INTEGER,
    workspace_tree_id     TEXT,
    artifact_versions     TEXT NOT NULL,
    environment_pins      TEXT NOT NULL,
    generation_refs       TEXT NOT NULL,
    capability_state      TEXT NOT NULL,
    permission_state      TEXT NOT NULL,
    recovery_recipe       TEXT NOT NULL,
    metadata              TEXT NOT NULL,
    created_at            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_session_checkpoint_branch
    ON session_checkpoints(root_frame_id, branch_id, created_at);

CREATE TABLE IF NOT EXISTS snapshot_operations (
    operation_id          TEXT PRIMARY KEY,
    root_frame_id         TEXT NOT NULL,
    branch_id             TEXT NOT NULL,
    kind                  TEXT NOT NULL,
    source_checkpoint_id  TEXT,
    target_checkpoint_id  TEXT,
    status                TEXT NOT NULL,
    preview               TEXT NOT NULL,
    error                 TEXT,
    created_at            INTEGER NOT NULL,
    finished_at           INTEGER
);
CREATE INDEX IF NOT EXISTS ix_snapshot_operation_branch
    ON snapshot_operations(root_frame_id, branch_id, created_at);
"""


_DEFAULT_EXCLUDED_DIRS = frozenset(
    {".git", ".openai4s", ".venv", "__pycache__", "node_modules"}
)
_SECRET_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})


class TreeEntry(TypedDict):
    path: str
    blob: str
    size: int
    mode: int


class WorkspaceTree(TypedDict):
    version: int
    tree_id: str
    entries: list[TreeEntry]
    skipped: list[dict[str, str]]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value: str) -> str:
    """Return a normalized portable relative path or raise.

    Tree manifests are data that may later be imported.  They are therefore
    treated as untrusted even when loaded from the local CAS.
    """

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("snapshot path must be a non-empty string")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe snapshot path: {value!r}")
    return path.as_posix()


def _is_secret(relative: str) -> bool:
    path = PurePosixPath(relative)
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if path.suffix.lower() in _SECRET_SUFFIXES:
        return True
    return name in {
        "credentials",
        "credentials.json",
        "service-account.json",
        "service_account.json",
    }


class WorkspaceCAS:
    """Immutable blob/tree storage with conflict-aware materialization."""

    def __init__(self, root: str | Path, *, max_file_bytes: int = 64 << 20) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        self.root = Path(root).expanduser().resolve()
        self.blobs_dir = self.root / "blobs"
        self.trees_dir = self.root / "trees"
        self.max_file_bytes = int(max_file_bytes)
        self._lock = threading.RLock()
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.trees_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        """Hold the CAS lifecycle lock across a file + metadata transaction.

        Checkpoint creation uses this boundary from ``capture()`` through the
        SQLite checkpoint insert.  Garbage collection takes the same lock, so
        it can never remove a content-addressed tree in the small interval
        between writing its manifest and publishing its durable reference.
        """

        with self._lock:
            yield self

    def capture(
        self,
        workspace: str | Path,
        *,
        exclude: Iterable[str] = (),
    ) -> WorkspaceTree:
        with self._lock:
            return self._capture_locked(workspace, exclude=exclude)

    def _capture_locked(
        self,
        workspace: str | Path,
        *,
        exclude: Iterable[str] = (),
    ) -> WorkspaceTree:
        """Capture regular non-secret files without following symlinks."""

        base = Path(workspace).expanduser().resolve()
        if not base.is_dir():
            raise ValueError(f"workspace is not a directory: {base}")
        extra = {_safe_relative(item).rstrip("/") for item in exclude}
        entries: list[TreeEntry] = []
        skipped: list[dict[str, str]] = []
        for directory, dirnames, filenames in os.walk(base, followlinks=False):
            current = Path(directory)
            # Mutate in place so os.walk never enters excluded or symlink dirs.
            kept_dirs: list[str] = []
            for name in sorted(dirnames):
                candidate = current / name
                rel = candidate.relative_to(base).as_posix()
                if (
                    name in _DEFAULT_EXCLUDED_DIRS
                    or rel in extra
                    or any(rel.startswith(item + "/") for item in extra)
                    or candidate.is_symlink()
                ):
                    skipped.append({"path": rel, "reason": "excluded"})
                else:
                    kept_dirs.append(name)
            dirnames[:] = kept_dirs
            for name in sorted(filenames):
                path = current / name
                relative = path.relative_to(base).as_posix()
                if (
                    relative in extra
                    or any(relative.startswith(item + "/") for item in extra)
                    or _is_secret(relative)
                ):
                    skipped.append({"path": relative, "reason": "secret_or_excluded"})
                    continue
                try:
                    info = path.lstat()
                except OSError as error:
                    skipped.append({"path": relative, "reason": f"stat: {error}"})
                    continue
                if not stat.S_ISREG(info.st_mode):
                    skipped.append({"path": relative, "reason": "not_regular_file"})
                    continue
                if info.st_size > self.max_file_bytes:
                    skipped.append({"path": relative, "reason": "too_large"})
                    continue
                try:
                    data = path.read_bytes()
                except OSError as error:
                    skipped.append({"path": relative, "reason": f"read: {error}"})
                    continue
                # A file that grew past the bound between stat and read is not
                # silently admitted.
                if len(data) > self.max_file_bytes:
                    skipped.append({"path": relative, "reason": "too_large"})
                    continue
                blob = self.put_blob(data)
                entries.append(
                    {
                        "path": _safe_relative(relative),
                        "blob": blob,
                        "size": len(data),
                        "mode": stat.S_IMODE(info.st_mode),
                    }
                )
        entries.sort(key=lambda item: item["path"])
        skipped.sort(key=lambda item: (item["path"], item["reason"]))
        return self.put_tree(entries, skipped=skipped)

    def release_trees(
        self,
        tree_ids: Iterable[str],
        *,
        retained_tree_ids: Iterable[str] = (),
        retained_tree_ids_provider: Callable[[], Iterable[str]] | None = None,
    ) -> dict[str, int]:
        """Release deleted checkpoint trees and only their unshared blobs.

        Every remaining manifest is scanned after candidate manifests are
        removed.  This preserves blobs shared by another session as well as a
        manifest produced immediately before its checkpoint row is committed.
        """

        candidates = set(dict.fromkeys(str(value) for value in tree_ids if value))
        retained = set(
            dict.fromkeys(str(value) for value in retained_tree_ids if value)
        )
        removed_trees = 0
        removed_blobs = 0
        candidate_blobs: set[str] = set()
        with self._lock:
            if retained_tree_ids_provider is not None:
                retained.update(
                    str(value) for value in retained_tree_ids_provider() if value
                )
            for tree_id in sorted(candidates - retained):
                try:
                    tree_path = self._tree_path(tree_id)
                    raw = json.loads(tree_path.read_text(encoding="utf-8"))
                    blobs = self._manifest_blob_ids(raw)
                    if blobs is None:
                        continue
                except (KeyError, OSError, TypeError, ValueError):
                    continue
                candidate_blobs.update(blobs)
                try:
                    tree_path.unlink()
                    removed_trees += 1
                except FileNotFoundError:
                    pass

            referenced_blobs: set[str] = set()
            for tree_path in self.trees_dir.glob("*.json"):
                try:
                    raw = json.loads(tree_path.read_text(encoding="utf-8"))
                except (OSError, TypeError, ValueError):
                    # A malformed/in-flight manifest fails closed: do not use
                    # it as authority to delete any additional blob.
                    return {
                        "trees": removed_trees,
                        "blobs": 0,
                        "shared_trees": len(candidates & retained),
                    }
                blobs = self._manifest_blob_ids(raw)
                if blobs is None:
                    return {
                        "trees": removed_trees,
                        "blobs": 0,
                        "shared_trees": len(candidates & retained),
                    }
                referenced_blobs.update(blobs)
            for blob_id in sorted(candidate_blobs - referenced_blobs):
                path = self._blob_path(blob_id)
                try:
                    path.unlink()
                    removed_blobs += 1
                except FileNotFoundError:
                    continue
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        return {
            "trees": removed_trees,
            "blobs": removed_blobs,
            "shared_trees": len(candidates & retained),
        }

    def put_blob(self, data: bytes) -> str:
        with self._lock:
            return self._put_blob_locked(data)

    def _put_blob_locked(self, data: bytes) -> str:
        if not isinstance(data, bytes):
            raise TypeError("blob data must be bytes")
        blob_id = _digest(data)
        destination = self._blob_path(blob_id)
        if not destination.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(destination, data, mode=0o600)
        return blob_id

    def get_blob(self, blob_id: str) -> bytes:
        with self._lock:
            return self._get_blob_locked(blob_id)

    def _get_blob_locked(self, blob_id: str) -> bytes:
        path = self._blob_path(blob_id)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise KeyError(f"snapshot blob not found: {blob_id}") from error
        if _digest(data) != blob_id:
            raise ValueError(f"snapshot blob checksum mismatch: {blob_id}")
        return data

    def put_tree(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        skipped: Iterable[Mapping[str, Any]] = (),
    ) -> WorkspaceTree:
        with self._lock:
            return self._put_tree_locked(entries, skipped=skipped)

    def _put_tree_locked(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        skipped: Iterable[Mapping[str, Any]] = (),
    ) -> WorkspaceTree:
        normalized: list[TreeEntry] = []
        seen: set[str] = set()
        for raw in entries:
            path = _safe_relative(str(raw.get("path") or ""))
            if path in seen:
                raise ValueError(f"duplicate snapshot path: {path}")
            seen.add(path)
            blob = str(raw.get("blob") or "")
            if len(blob) != 64 or any(ch not in "0123456789abcdef" for ch in blob):
                raise ValueError(f"invalid blob id for {path}")
            if not self._blob_path(blob).is_file():
                raise ValueError(f"snapshot blob does not exist for {path}")
            size = int(raw.get("size") or 0)
            mode = int(raw.get("mode") or 0o600) & 0o777
            if size < 0:
                raise ValueError(f"invalid size for {path}")
            normalized.append({"path": path, "blob": blob, "size": size, "mode": mode})
        normalized.sort(key=lambda item: item["path"])
        safe_skipped = [
            {
                "path": str(item.get("path") or ""),
                "reason": str(item.get("reason") or ""),
            }
            for item in skipped
        ]
        safe_skipped.sort(key=lambda item: (item["path"], item["reason"]))
        body = {"version": 1, "entries": normalized, "skipped": safe_skipped}
        tree_id = _digest(_canonical_json(body))
        manifest: WorkspaceTree = {
            **body,
            "tree_id": tree_id,
        }  # type: ignore[typeddict-item]
        path = self._tree_path(tree_id)
        if not path.is_file():
            self._atomic_write(path, _canonical_json(manifest), mode=0o600)
        return manifest

    def get_tree(self, tree_id: str) -> WorkspaceTree:
        with self._lock:
            return self._get_tree_locked(tree_id)

    def _get_tree_locked(self, tree_id: str) -> WorkspaceTree:
        path = self._tree_path(tree_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise KeyError(f"snapshot tree not found: {tree_id}") from error
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid snapshot tree: {tree_id}") from error
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError(f"unsupported snapshot tree: {tree_id}")
        rebuilt = self.put_tree(
            raw.get("entries") or [], skipped=raw.get("skipped") or []
        )
        if rebuilt["tree_id"] != tree_id:
            raise ValueError(f"snapshot tree checksum mismatch: {tree_id}")
        return rebuilt

    def preview_restore(
        self,
        target_tree_id: str,
        workspace: str | Path,
        *,
        baseline_tree_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._preview_restore_locked(
                target_tree_id,
                workspace,
                baseline_tree_id=baseline_tree_id,
            )

    def _preview_restore_locked(
        self,
        target_tree_id: str,
        workspace: str | Path,
        *,
        baseline_tree_id: str | None = None,
    ) -> dict[str, Any]:
        """Describe a restore and surface post-checkpoint external edits.

        New untracked files are preserved.  A managed file is deleted only if
        it existed in the baseline and the target intentionally omits it.
        """

        target = self._entry_map(self.get_tree(target_tree_id))
        baseline = (
            self._entry_map(self.get_tree(baseline_tree_id))
            if baseline_tree_id is not None
            else {}
        )
        current_tree = self.capture(workspace)
        current = self._entry_map(current_tree)
        writes: list[dict[str, Any]] = []
        deletes: list[str] = []
        conflicts: list[dict[str, Any]] = []
        unchanged: list[str] = []

        for path in sorted(set(target) | set(baseline)):
            desired = target.get(path)
            before = baseline.get(path)
            actual = current.get(path)
            externally_changed = baseline_tree_id is not None and not self._same_entry(
                actual, before
            )
            if desired is None:
                if before is None or actual is None:
                    continue
                if externally_changed:
                    conflicts.append(self._conflict(path, before, actual, desired))
                else:
                    deletes.append(path)
                continue
            if self._same_entry(actual, desired):
                unchanged.append(path)
                continue
            if externally_changed and not self._same_entry(actual, desired):
                conflicts.append(self._conflict(path, before, actual, desired))
                continue
            writes.append(
                {
                    "path": path,
                    "operation": "create" if actual is None else "replace",
                    "from_blob": actual["blob"] if actual else None,
                    "to_blob": desired["blob"],
                    "size": desired["size"],
                }
            )
        return {
            "target_tree_id": target_tree_id,
            "baseline_tree_id": baseline_tree_id,
            "observed_tree_id": current_tree["tree_id"],
            "writes": writes,
            "deletes": deletes,
            "conflicts": conflicts,
            "unchanged": unchanged,
            "preserved_untracked": sorted(set(current) - set(target) - set(baseline)),
        }

    def restore(
        self,
        target_tree_id: str,
        workspace: str | Path,
        *,
        baseline_tree_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._restore_locked(
                target_tree_id,
                workspace,
                baseline_tree_id=baseline_tree_id,
            )

    def _restore_locked(
        self,
        target_tree_id: str,
        workspace: str | Path,
        *,
        baseline_tree_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically replace individual files after a conflict-free preview."""

        base = Path(workspace).expanduser().resolve()
        preview = self.preview_restore(
            target_tree_id,
            base,
            baseline_tree_id=baseline_tree_id,
        )
        if preview["conflicts"]:
            return {**preview, "applied": False, "reason": "conflicts"}
        target = self._entry_map(self.get_tree(target_tree_id))
        for change in preview["writes"]:
            relative = _safe_relative(change["path"])
            destination = (base / relative).resolve()
            if base not in destination.parents:
                raise ValueError(f"restore escaped workspace: {relative}")
            entry = target[relative]
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(
                destination,
                self.get_blob(entry["blob"]),
                mode=entry["mode"],
            )
        for relative in preview["deletes"]:
            destination = (base / _safe_relative(relative)).resolve()
            if base not in destination.parents:
                raise ValueError(f"restore escaped workspace: {relative}")
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        return {**preview, "applied": True}

    def _blob_path(self, blob_id: str) -> Path:
        if len(blob_id) != 64 or any(ch not in "0123456789abcdef" for ch in blob_id):
            raise ValueError("invalid snapshot blob id")
        return self.blobs_dir / blob_id[:2] / blob_id

    def _tree_path(self, tree_id: str) -> Path:
        if len(tree_id) != 64 or any(ch not in "0123456789abcdef" for ch in tree_id):
            raise ValueError("invalid snapshot tree id")
        return self.trees_dir / f"{tree_id}.json"

    @staticmethod
    def _manifest_blob_ids(raw: Any) -> set[str] | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
            return None
        blobs: set[str] = set()
        for entry in raw["entries"]:
            if not isinstance(entry, dict):
                return None
            blob_id = entry.get("blob")
            if (
                not isinstance(blob_id, str)
                or len(blob_id) != 64
                or any(character not in "0123456789abcdef" for character in blob_id)
            ):
                return None
            blobs.add(blob_id)
        return blobs

    @staticmethod
    def _entry_map(tree: WorkspaceTree) -> dict[str, TreeEntry]:
        return {entry["path"]: entry for entry in tree["entries"]}

    @staticmethod
    def _same_entry(left: TreeEntry | None, right: TreeEntry | None) -> bool:
        if left is None or right is None:
            return left is right
        return left["blob"] == right["blob"] and left["mode"] == right["mode"]

    @staticmethod
    def _conflict(
        path: str,
        baseline: TreeEntry | None,
        actual: TreeEntry | None,
        target: TreeEntry | None,
    ) -> dict[str, Any]:
        return {
            "path": path,
            "baseline_blob": baseline["blob"] if baseline else None,
            "observed_blob": actual["blob"] if actual else None,
            "target_blob": target["blob"] if target else None,
        }

    @staticmethod
    def _atomic_write(path: Path, data: bytes, *, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode & 0o777)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class SessionSnapshotRepository:
    """Store immutable checkpoints, branch heads, and restore operations."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
        checkpoint_state: Any | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._checkpoint_state = checkpoint_state
        with self._lock:
            self._connection.executescript(SNAPSHOT_SCHEMA)
            self._migrate_cursor_checkpoints()
            self._connection.commit()

    def _migrate_cursor_checkpoints(self) -> None:
        """Add exact Cell/message cursor bindings to existing databases.

        ``CREATE TABLE IF NOT EXISTS`` does not add columns to an existing
        installation.  Keep this migration local to the owning repository so
        a checkpoint can never be advertised as forkable based on metadata or
        a guessed cursor alone.
        """

        columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(session_checkpoints)"
            ).fetchall()
        }
        for name, declaration in (
            ("source_kind", "TEXT"),
            ("source_id", "TEXT"),
            ("internal", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                self._connection.execute(
                    f"ALTER TABLE session_checkpoints ADD COLUMN {name} {declaration}"
                )
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_session_checkpoint_source "
            "ON session_checkpoints(root_frame_id,source_kind,source_id) "
            "WHERE source_kind IS NOT NULL AND source_id IS NOT NULL"
        )

    def ensure_branch(
        self,
        *,
        root_frame_id: str,
        branch_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        root_frame_id = self._text("root_frame_id", root_frame_id)
        branch_id = self._text("branch_id", branch_id or root_frame_id)
        now = self._clock_ms()
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO session_branches("
                "branch_id,root_frame_id,name,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                (branch_id, root_frame_id, name, now, now),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM session_branches WHERE branch_id=?", (branch_id,)
            ).fetchone()
        if row is None or row["root_frame_id"] != root_frame_id:
            raise ValueError(f"branch {branch_id!r} belongs to another session")
        return dict(row)

    def create_checkpoint(
        self,
        *,
        root_frame_id: str,
        branch_id: str | None = None,
        reason: str,
        workspace_tree_id: str | None,
        action_cursor: int | None = None,
        message_cursor: int | None = None,
        cell_cursor: int | None = None,
        artifact_versions: Any = None,
        environment_pins: Any = None,
        generation_refs: Any = None,
        capability_state: Any = None,
        permission_state: Any = None,
        recovery_recipe: Any = None,
        metadata: Any = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        internal: bool = False,
        parent_checkpoint_id: str | None = None,
        checkpoint_id: str | None = None,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        root_frame_id = self._text("root_frame_id", root_frame_id)
        branch_id = self._text("branch_id", branch_id or root_frame_id)
        reason = self._text("reason", reason)
        checkpoint_id = self._text(
            "checkpoint_id", checkpoint_id or f"cp-{uuid.uuid4().hex[:16]}"
        )
        if workspace_tree_id is not None:
            self._sha256("workspace_tree_id", workspace_tree_id)
        if (source_kind is None) != (source_id is None):
            raise ValueError("source_kind and source_id must be provided together")
        if source_kind is not None:
            source_kind = self._text("source_kind", source_kind)
            if source_kind not in {"cell", "message"}:
                raise ValueError("source_kind must be cell or message")
            source_id = self._text("source_id", source_id or "")
            existing = self.get_checkpoint_for_source(
                root_frame_id,
                source_kind=source_kind,
                source_id=source_id,
            )
            if existing is not None:
                return existing
        self.ensure_branch(root_frame_id=root_frame_id, branch_id=branch_id)
        now = self._clock_ms()
        with self._lock:
            branch = self._connection.execute(
                "SELECT * FROM session_branches WHERE branch_id=?", (branch_id,)
            ).fetchone()
            if branch is None:
                raise KeyError(branch_id)
            current_head = branch["head_checkpoint_id"]
            if expected_head is not None and current_head != expected_head:
                raise RuntimeError(
                    "branch head changed: "
                    f"expected {expected_head!r}, got {current_head!r}"
                )
            if parent_checkpoint_id is None:
                parent_checkpoint_id = current_head
            elif parent_checkpoint_id != current_head:
                raise ValueError("parent checkpoint must be the branch's current head")
            try:
                self._connection.execute(
                    "INSERT INTO session_checkpoints("
                    "checkpoint_id,root_frame_id,branch_id,parent_checkpoint_id,"
                    "source_kind,source_id,internal,reason,action_cursor,message_cursor,"
                    "cell_cursor,workspace_tree_id,"
                    "artifact_versions,environment_pins,generation_refs,capability_state,"
                    "permission_state,recovery_recipe,metadata,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        checkpoint_id,
                        root_frame_id,
                        branch_id,
                        parent_checkpoint_id,
                        source_kind,
                        source_id,
                        1 if internal else 0,
                        reason,
                        self._cursor(action_cursor),
                        self._cursor(message_cursor),
                        self._cursor(cell_cursor),
                        workspace_tree_id,
                        self._json(artifact_versions or []),
                        self._json(environment_pins or {}),
                        self._json(generation_refs or {}),
                        self._json(capability_state or {}),
                        self._json(permission_state or {}),
                        self._json(recovery_recipe or {}),
                        self._json(metadata or {}),
                        now,
                    ),
                )
                if self._checkpoint_state is not None:
                    safe_metadata = metadata if isinstance(metadata, Mapping) else {}
                    # Imported packages are intentionally quarantined and add
                    # plans/reviews/memories only after their checkpoint graph
                    # is rebuilt.  Do not manufacture an empty "exact" state
                    # snapshot for them.  Missing state remains an explicit
                    # backward-compatible Partial projection.
                    if not safe_metadata.get("imported"):
                        source_checkpoint_id = safe_metadata.get("reverted_to")
                        self._checkpoint_state.capture_checkpoint(
                            checkpoint_id=checkpoint_id,
                            root_frame_id=root_frame_id,
                            branch_id=branch_id,
                            source_checkpoint_id=(
                                str(source_checkpoint_id)
                                if source_checkpoint_id
                                else None
                            ),
                            commit=False,
                        )
                self._connection.execute(
                    "UPDATE session_branches SET head_checkpoint_id=?,updated_at=? "
                    "WHERE branch_id=? AND head_checkpoint_id IS ?",
                    (checkpoint_id, now, branch_id, current_head),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError("branch head changed while creating checkpoint")
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
        result = self.get_checkpoint(checkpoint_id)
        if result is None:
            raise RuntimeError("checkpoint insert did not persist")
        return result

    def fork_branch(
        self,
        *,
        root_frame_id: str,
        from_checkpoint_id: str,
        branch_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        root_frame_id = self._text("root_frame_id", root_frame_id)
        checkpoint = self.get_checkpoint(from_checkpoint_id)
        if checkpoint is None or checkpoint["root_frame_id"] != root_frame_id:
            raise KeyError(from_checkpoint_id)
        branch_id = self._text("branch_id", branch_id or f"br-{uuid.uuid4().hex[:16]}")
        now = self._clock_ms()
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO session_branches("
                    "branch_id,root_frame_id,parent_branch_id,base_checkpoint_id,"
                    "head_checkpoint_id,name,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (
                        branch_id,
                        root_frame_id,
                        checkpoint["branch_id"],
                        from_checkpoint_id,
                        from_checkpoint_id,
                        name,
                        now,
                        now,
                    ),
                )
            except Exception:
                self._connection.rollback()
                raise
            self._connection.commit()
        result = self.get_branch(branch_id)
        if result is None:
            raise RuntimeError("branch insert did not persist")
        return result

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM session_checkpoints WHERE checkpoint_id=?",
                (checkpoint_id,),
            ).fetchone()
        return self._decode_checkpoint(row) if row else None

    def get_checkpoint_for_source(
        self,
        root_frame_id: str,
        *,
        source_kind: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        root_frame_id = self._text("root_frame_id", root_frame_id)
        source_kind = self._text("source_kind", source_kind)
        source_id = self._text("source_id", source_id)
        if source_kind not in {"cell", "message"}:
            raise ValueError("source_kind must be cell or message")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM session_checkpoints WHERE root_frame_id=? "
                "AND source_kind=? AND source_id=?",
                (root_frame_id, source_kind, source_id),
            ).fetchone()
        return self._decode_checkpoint(row) if row else None

    def checkpoint_source_map(
        self,
        root_frame_id: str,
        *,
        source_kind: str,
    ) -> dict[str, str]:
        root_frame_id = self._text("root_frame_id", root_frame_id)
        source_kind = self._text("source_kind", source_kind)
        if source_kind not in {"cell", "message"}:
            raise ValueError("source_kind must be cell or message")
        with self._lock:
            rows = self._connection.execute(
                "SELECT source_id,checkpoint_id FROM session_checkpoints WHERE "
                "root_frame_id=? AND source_kind=? AND source_id IS NOT NULL",
                (root_frame_id, source_kind),
            ).fetchall()
        return {str(row["source_id"]): str(row["checkpoint_id"]) for row in rows}

    def list_checkpoints(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        sql = "SELECT * FROM session_checkpoints WHERE root_frame_id=?"
        params: list[Any] = [root_frame_id]
        if branch_id is not None:
            sql += " AND branch_id=?"
            params.append(branch_id)
        sql += " ORDER BY created_at DESC, checkpoint_id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._decode_checkpoint(row) for row in rows]

    def retained_tree_ids(self) -> tuple[str, ...]:
        """Return every workspace tree referenced by a durable checkpoint."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT workspace_tree_id FROM session_checkpoints "
                "WHERE workspace_tree_id IS NOT NULL"
            ).fetchall()
        return tuple(str(row["workspace_tree_id"]) for row in rows)

    def get_branch(self, branch_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM session_branches WHERE branch_id=?", (branch_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_branches(self, root_frame_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM session_branches WHERE root_frame_id=? "
                "ORDER BY created_at,branch_id",
                (root_frame_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_operation(
        self,
        *,
        root_frame_id: str,
        branch_id: str,
        kind: str,
        status: str,
        preview: Any,
        source_checkpoint_id: str | None = None,
        target_checkpoint_id: str | None = None,
        error: str | None = None,
        finished: bool = False,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        operation_id = self._text(
            "operation_id", operation_id or f"so-{uuid.uuid4().hex[:16]}"
        )
        now = self._clock_ms()
        with self._lock:
            branch = self._connection.execute(
                "SELECT root_frame_id FROM session_branches WHERE branch_id=?",
                (branch_id,),
            ).fetchone()
            if branch is None or branch["root_frame_id"] != root_frame_id:
                raise ValueError("snapshot operation branch/session mismatch")
            for checkpoint_id in (source_checkpoint_id, target_checkpoint_id):
                if checkpoint_id is None:
                    continue
                checkpoint = self._connection.execute(
                    "SELECT root_frame_id FROM session_checkpoints "
                    "WHERE checkpoint_id=?",
                    (checkpoint_id,),
                ).fetchone()
                if checkpoint is None or checkpoint["root_frame_id"] != root_frame_id:
                    raise ValueError("snapshot operation checkpoint mismatch")
            self._connection.execute(
                "INSERT INTO snapshot_operations("
                "operation_id,root_frame_id,branch_id,kind,source_checkpoint_id,"
                "target_checkpoint_id,status,preview,error,created_at,finished_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    operation_id,
                    root_frame_id,
                    branch_id,
                    self._text("kind", kind),
                    source_checkpoint_id,
                    target_checkpoint_id,
                    self._text("status", status),
                    self._json(preview or {}),
                    error,
                    now,
                    now if finished else None,
                ),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM snapshot_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        result = dict(row)
        result["preview"] = self._load(result["preview"], {})
        return result

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM snapshot_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._decode_operation(row) if row else None

    def list_operations(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        root_frame_id = self._text("root_frame_id", root_frame_id)
        clauses = ["root_frame_id=?"]
        params: list[Any] = [root_frame_id]
        for column, value in (
            ("branch_id", branch_id),
            ("kind", kind),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(self._text(column, value))
        params.append(max(1, min(int(limit), 1000)))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM snapshot_operations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC,operation_id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._decode_operation(row) for row in rows]

    @staticmethod
    def _text(name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _cursor(value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("checkpoint cursors must be non-negative integers")
        return value

    @staticmethod
    def _sha256(name: str, value: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        return value

    @staticmethod
    def _json(value: Any) -> str:
        return _canonical_json(value).decode("utf-8")

    @staticmethod
    def _load(value: str | None, default: Any) -> Any:
        try:
            return json.loads(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    @classmethod
    def _decode_checkpoint(cls, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["internal"] = bool(result.get("internal"))
        for key, default in (
            ("artifact_versions", []),
            ("environment_pins", {}),
            ("generation_refs", {}),
            ("capability_state", {}),
            ("permission_state", {}),
            ("recovery_recipe", {}),
            ("metadata", {}),
        ):
            result[key] = cls._load(result.get(key), default)
        return result

    @classmethod
    def _decode_operation(cls, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["preview"] = cls._load(result.get("preview"), {})
        return result


__all__ = [
    "SNAPSHOT_SCHEMA",
    "SessionSnapshotRepository",
    "TreeEntry",
    "WorkspaceCAS",
    "WorkspaceTree",
]

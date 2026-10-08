"""Portable, inert project history under the explicitly selected .openai4s dir.

SQLite remains the live authority. This archive is an append-only human-readable
copy, never an execution restore or a source of permissions. File objects are
content-addressed and revisions remain after the live project/session is deleted.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from openai4s.artifact_restore import trusted_snapshot_roots
from openai4s.host.files import is_secret_path
from openai4s.project_folders import ProjectFolderError, project_folder_path
from openai4s.server.session_package import _is_secret_path, _safe_text, _sanitize
from openai4s.storage.branch_projection import project_branch_records

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_SESSION_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILES = 1000
MAX_RECORDS = 5000
MAX_REVISIONS = 1000
MAX_SCAN_ENTRIES = 20_000
_LOCK = threading.RLock()
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ProjectHistoryError(ValueError):
    """A safe, user-visible archive refusal."""

    def __init__(
        self, message: str, status: int = 409, code: str = "project_history_integrity"
    ):
        super().__init__(message)
        self.status, self.code = status, code


def _json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(value: str, *, digest: bool = False) -> str:
    if not isinstance(value, str) or not (_DIGEST if digest else _ID).fullmatch(value):
        raise ProjectHistoryError(
            "invalid archive identity", 400, "invalid_history_identity"
        )
    return value


def _parts(value: str) -> tuple[str, ...]:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or "\x00" in value
    ):
        raise ProjectHistoryError("invalid archive path", 400, "invalid_history_path")
    return path.parts


def _flags(directory: bool = False) -> int:
    if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise ProjectHistoryError(
            "secure project history is unavailable on this platform"
        )
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | (os.O_DIRECTORY if directory else 0)
    )


class _Tree:
    """All metadata IO is fd-relative and every directory hop is no-follow."""

    def __init__(self, root: Path):
        self.root = root
        descriptor = os.open(root.anchor, _flags(True))
        try:
            for part in root.parts[1:]:
                child = os.open(part, _flags(True), dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        except BaseException:
            os.close(descriptor)
            raise
        self.fd = descriptor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        os.close(self.fd)

    def directory(self, parts: tuple[str, ...], *, create: bool = False) -> int:
        descriptor = os.dup(self.fd)
        try:
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, _flags(True), dir_fd=descriptor)
                if create:
                    os.fchmod(child, 0o700)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def read(self, path: str, limit: int = MAX_METADATA_BYTES) -> bytes:
        parts = _parts(path)
        parent = self.directory(parts[:-1])
        descriptor = None
        try:
            descriptor = os.open(parts[-1], _flags(), dir_fd=parent)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ProjectHistoryError("archive file is not a private regular file")
            if before.st_size > limit:
                raise ProjectHistoryError("archive file exceeds its size limit")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(256 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            data = b"".join(chunks)
            if (
                len(data) > limit
                or len(data) != before.st_size
                or not os.path.samestat(before, named)
                or (
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                    before.st_nlink,
                )
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)
            ):
                raise ProjectHistoryError("archive file changed while being read")
            return data
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)

    def write(self, path: str, data: bytes, *, immutable: bool = False) -> None:
        parts = _parts(path)
        parent = self.directory(parts[:-1], create=True)
        temporary = ".pending-" + uuid.uuid4().hex
        try:
            try:
                existing = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                    raise ProjectHistoryError(
                        "archive target is not a private regular file"
                    )
                if immutable:
                    if self.read(path, max(MAX_METADATA_BYTES, MAX_FILE_BYTES)) != data:
                        raise ProjectHistoryError(
                            "immutable project history was modified"
                        )
                    return
            delta = len(data) - (existing.st_size if existing else 0)
            if getattr(self, "used_bytes", 0) + delta > MAX_ARCHIVE_BYTES:
                raise ProjectHistoryError("project history reached its storage limit")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "wb") as sink:
                sink.write(data)
                sink.flush()
                os.fsync(sink.fileno())
            if immutable:
                try:
                    os.link(
                        temporary,
                        parts[-1],
                        src_dir_fd=parent,
                        dst_dir_fd=parent,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    if self.read(path, max(MAX_METADATA_BYTES, MAX_FILE_BYTES)) != data:
                        raise ProjectHistoryError(
                            "immutable project history was modified"
                        )
            else:
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            self.used_bytes = getattr(self, "used_bytes", 0) + delta
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def names(self, path: str, limit: int = MAX_REVISIONS) -> list[str]:
        try:
            descriptor = self.directory(_parts(path))
        except FileNotFoundError:
            return []
        try:
            names = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    if len(names) >= limit:
                        raise ProjectHistoryError(
                            "project history contains too many entries"
                        )
                    if entry.is_symlink():
                        raise ProjectHistoryError(
                            "project history contains a symbolic link"
                        )
                    names.append(entry.name)
            return sorted(names)
        finally:
            os.close(descriptor)


class ProjectHistoryService:
    """Bounded snapshots with explicit omissions; no import or execution API."""

    def __init__(self, store: Any, cfg: Any):
        self.store, self.cfg = store, cfg
        self._states: dict[str, dict] = {}

    def _root(self, pid: str) -> Path:
        return project_folder_path(self.store, self.cfg, pid)

    def _error(self, pid: str, error: Exception, *, remember: bool = True) -> dict:
        skipped = isinstance(error, ProjectFolderError) and error.code in {
            "no_project_folder",
            "local_project_folders_only",
        }
        missing = isinstance(error, FileNotFoundError)
        result = {
            "state": "skipped" if skipped else "error",
            "error": (
                "archived session or revision not found"
                if missing
                else (
                    str(error)
                    if isinstance(error, (ProjectHistoryError, ProjectFolderError))
                    else "project history could not be read or saved safely"
                )
            ),
            "status": 404 if missing else getattr(error, "status", 403),
            "code": (
                "history_not_found"
                if missing
                else getattr(error, "code", "project_history_denied")
            ),
        }
        if self._states.get(pid, {}).get("last_saved_at"):
            result["last_saved_at"] = self._states[pid]["last_saved_at"]
        if remember:
            self._states[pid] = result
        return result

    def _settings(self, pid: str) -> dict:
        project = self.store.get_project(pid) or {}
        return _sanitize(
            {
                "schema_version": 1,
                "project": {
                    key: project.get(key) or ""
                    for key in ("name", "description", "context")
                },
            }
        )

    def _write_settings(self, tree: _Tree, settings: dict) -> None:
        data = _json(settings)
        if len(data) > MAX_METADATA_BYTES:
            raise ProjectHistoryError("project settings exceed the archive limit")
        tree.write(".openai4s/.gitignore", b"*\n", immutable=True)
        tree.write(
            f".openai4s/settings-revisions/{_hash(data)}.json", data, immutable=True
        )
        tree.write(".openai4s/settings.json", data)

    def sync_project(self, pid: str, *, include_sessions: bool = False) -> dict:
        with _LOCK:
            try:
                root = self._root(pid)
                settings = self._settings(pid)
                with _Tree(root) as tree:
                    self._archive_budget(tree)
                    self._write_settings(tree, settings)
                if include_sessions:
                    sessions = self.store.project_session_ids(pid)
                    if len(sessions) > MAX_FILES:
                        raise ProjectHistoryError(
                            "project has too many sessions to archive in one operation"
                        )
                    for session in sessions:
                        result = self.sync_session(session)
                        if result["state"] == "error":
                            return result
                result = {
                    "state": "saved",
                    "last_saved_at": int(time.time() * 1000),
                    "error": None,
                }
                self._states[pid] = result
                return result
            except Exception as error:
                return self._error(pid, error)

    def _file_sources(
        self, rootid: str, workspace: Path, omissions: list[dict]
    ) -> list[tuple[dict, Path, Path]]:
        sources: list[tuple[dict, Path, Path]] = []
        if workspace.exists():
            # Descriptor-relative scandir stays bounded even for wide/deep
            # trees. No symlink directory is ever opened or followed.
            scanned = 0
            stack: list[tuple[str, ...]] = [()]
            with _Tree(workspace) as source:
                while stack:
                    parts = stack.pop()
                    try:
                        descriptor = source.directory(parts)
                    except OSError:
                        omissions.append(
                            {
                                "path": "/".join(parts),
                                "reason": "unsafe_or_missing_directory",
                            }
                        )
                        continue
                    children = []
                    try:
                        with os.scandir(descriptor) as entries:
                            for entry in entries:
                                scanned += 1
                                if (
                                    scanned > MAX_SCAN_ENTRIES
                                    or len(sources) >= MAX_FILES
                                ):
                                    omissions.append(
                                        {
                                            "path": "workspace",
                                            "reason": "file_count_limit",
                                        }
                                    )
                                    return sources
                                relative = "/".join((*parts, entry.name))
                                if (
                                    entry.name == ".openai4s"
                                    or is_secret_path(relative)
                                    or _is_secret_path(relative)
                                ):
                                    omissions.append(
                                        {"path": "[redacted]", "reason": "secret_path"}
                                    )
                                    continue
                                try:
                                    info = entry.stat(follow_symlinks=False)
                                except OSError:
                                    omissions.append(
                                        {
                                            "path": relative,
                                            "reason": "unsafe_missing_or_oversized_file",
                                        }
                                    )
                                    continue
                                if stat.S_ISDIR(info.st_mode):
                                    children.append((*parts, entry.name))
                                else:
                                    sources.append(
                                        (
                                            {
                                                "path": "workspace/" + relative,
                                                "name": entry.name,
                                                "version_id": None,
                                                "kind": "workspace",
                                            },
                                            workspace,
                                            Path(relative),
                                        )
                                    )
                    finally:
                        os.close(descriptor)
                    stack.extend(sorted(children, reverse=True))
            sources.sort(key=lambda item: item[0]["path"])
        artifacts = self.store.list_artifacts({"root_frame_id": rootid})
        if len(artifacts) > MAX_FILES:
            omissions.append({"path": "artifacts", "reason": "file_count_limit"})
        for artifact in artifacts[:MAX_FILES]:
            aid = str(artifact.get("artifact_id") or artifact.get("id") or "")
            for version in self.store.list_versions(aid):
                if len(sources) >= MAX_FILES:
                    omissions.append(
                        {"path": "artifacts", "reason": "file_count_limit"}
                    )
                    return sources
                filename = str(
                    version.get("filename") or artifact.get("filename") or "file"
                )
                if is_secret_path(filename) or _is_secret_path(filename):
                    omissions.append({"path": "[redacted]", "reason": "secret_path"})
                    continue
                vid = str(version.get("version_id") or "")
                _identity(vid)
                metadata = self.store.version_meta(vid) or {}
                if metadata.get("artifact_id") != aid:
                    omissions.append(
                        {"path": filename, "reason": "artifact_version_unavailable"}
                    )
                    continue
                snapshot = metadata.get("snapshot_path")
                if not snapshot:
                    omissions.append(
                        {"path": filename, "reason": "no_immutable_snapshot"}
                    )
                    continue
                path = Path(os.path.abspath(str(snapshot)))
                trusted = next(
                    (
                        base.resolve()
                        for base in trusted_snapshot_roots(self.cfg.data_dir)
                        if path.is_relative_to(base.resolve())
                    ),
                    None,
                )
                if trusted is None:
                    omissions.append(
                        {"path": filename, "reason": "untrusted_snapshot_path"}
                    )
                    continue
                sources.append(
                    (
                        {
                            "path": f"artifacts/{vid}/{Path(filename).name}",
                            "name": Path(filename).name,
                            "version_id": vid,
                            "artifact_id": aid,
                            "kind": "artifact",
                            "expected_sha256": version.get("checksum"),
                        },
                        trusted,
                        path.relative_to(trusted),
                    )
                )
        return sources

    def _snapshot(
        self, frame: dict, workspace: Path, tree: _Tree, branch_id: str
    ) -> tuple[dict, dict[str, bytes]]:
        rootid = str(frame.get("frame_id") or frame.get("id"))
        pid = str(frame.get("project_id") or "default")
        omissions: list[dict] = []
        messages = self.store.list_branch_message_boundaries(
            rootid, branch_id=branch_id, limit=MAX_RECORDS + 1
        )
        if len(messages) > MAX_RECORDS:
            omissions.append({"path": "messages", "reason": "record_count_limit"})
        messages = [
            _sanitize(
                {
                    key: item.get(key)
                    for key in (
                        "message_id",
                        "branch_id",
                        "seq",
                        "role",
                        "content",
                        "created_at",
                    )
                }
            )
            for item in messages[:MAX_RECORDS]
        ]
        cell_rows = project_branch_records(
            self.store,
            rootid,
            branch_id,
            list_local=lambda selected: self.store.list_cells(
                rootid, branch_id=selected
            ),
            record_position=lambda cell: int(
                cell.get("state_revision") or cell.get("cell_index") or 0
            ),
            cursor_key="cell_cursor",
        )
        if len(cell_rows) > MAX_RECORDS:
            omissions.append({"path": "cells", "reason": "record_count_limit"})
        cells = []
        for item in cell_rows[:MAX_RECORDS]:
            detail = (
                self.store.cell_detail(str(item.get("producing_cell_id") or "")) or item
            )
            cells.append(
                _sanitize(
                    {
                        key: detail.get(key)
                        for key in (
                            "producing_cell_id",
                            "cell_index",
                            "state_revision",
                            "branch_id",
                            "language",
                            "code",
                            "stdout",
                            "stderr",
                            "error",
                            "created_at",
                        )
                    }
                )
            )
        payloads = {
            "messages.jsonl": b"".join(_json(item) + b"\n" for item in messages),
            "cells.json": _json(cells),
            "transcript.md": (
                "# "
                + _safe_text(frame.get("name") or "Conversation")
                + "\n\n"
                + "\n\n".join(
                    "## "
                    + str(item.get("role") or "message")
                    + "\n\n"
                    + str(item.get("content") or "")
                    for item in messages
                )
            ).encode("utf-8"),
        }
        if any(len(data) > MAX_METADATA_BYTES for data in payloads.values()):
            raise ProjectHistoryError("conversation text exceeds the archive limit")
        files = []
        total = 0
        for record, base, relative in self._file_sources(rootid, workspace, omissions):
            try:
                with _Tree(base) as source:
                    data = source.read(relative.as_posix(), MAX_FILE_BYTES)
                total += len(data)
                if total > MAX_SESSION_BYTES:
                    omissions.append(
                        {"path": record["path"], "reason": "session_size_limit"}
                    )
                    continue
                digest = _hash(data)
                if (
                    record.get("expected_sha256")
                    and record["expected_sha256"] != digest
                ):
                    raise ProjectHistoryError("artifact checksum mismatch")
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                if text is not None and _safe_text(text) != text:
                    omissions.append(
                        {"path": record["path"], "reason": "credential_content"}
                    )
                    continue
            except (OSError, ValueError, RuntimeError):
                omissions.append(
                    {
                        "path": record["path"],
                        "reason": "unsafe_missing_or_oversized_file",
                    }
                )
                continue
            # A corrupt history object is not a source-file omission. Refuse
            # publication so a failure cannot stamp a successful archive.
            tree.write(".openai4s/objects/" + digest, data, immutable=True)
            record.pop("expected_sha256", None)
            files.append({**record, "sha256": digest, "size_bytes": len(data)})
        settings = {
            **self._settings(pid),
            "session": _sanitize(
                {key: frame.get(key) for key in ("name", "model", "runtime_env")}
            ),
        }
        manifest = {
            "schema_version": 1,
            "session_id": rootid,
            "branch_id": branch_id,
            "history_scope": {
                "messages": "active_branch_projection",
                "cells": "active_branch_projection",
                "workspace": "active_branch",
                "artifacts": "all_retained_session_versions",
            },
            "title": _safe_text(frame.get("name") or "Conversation"),
            "updated_at": int(frame.get("updated_at") or 0),
            "settings": settings,
            "message_count": len(messages),
            "cell_count": len(cells),
            "file_count": len(files),
            "files": files,
            "omissions": omissions,
            "members": {
                name: {"sha256": _hash(data), "size_bytes": len(data)}
                for name, data in payloads.items()
            },
        }
        return manifest, payloads

    def _archive_budget(self, tree: _Tree) -> None:
        total = 0
        stack = [(".openai4s",)]
        scanned = 0
        while stack:
            parts = stack.pop()
            try:
                descriptor = tree.directory(parts)
            except FileNotFoundError:
                continue
            try:
                with os.scandir(descriptor) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > MAX_SCAN_ENTRIES:
                            raise ProjectHistoryError(
                                "project history reached its entry limit"
                            )
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            stack.append((*parts, entry.name))
                        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            total += info.st_size
                        else:
                            raise ProjectHistoryError(
                                "project history contains an unsafe file"
                            )
                        if total > MAX_ARCHIVE_BYTES:
                            raise ProjectHistoryError(
                                "project history reached its storage limit"
                            )
            finally:
                os.close(descriptor)
        tree.used_bytes = total

    def sync_session(
        self,
        rootid: str,
        *,
        workspace: Path | None = None,
        branch_id: str | None = None,
    ) -> dict:
        pid = ""
        with _LOCK:
            try:
                _identity(rootid)
                frame = self.store.get_frame(rootid)
                if frame is None or (frame.get("root_frame_id") or rootid) != rootid:
                    raise ProjectHistoryError("session not found")
                pid = str(frame.get("project_id") or "default")
                root = self._root(pid)
                guard = self.store.session_export_guard(rootid)
                active_branch = str(guard["active_branch_id"])
                if guard.get("recovery_marker_present") or (
                    branch_id is not None and branch_id != active_branch
                ):
                    raise ProjectHistoryError(
                        "session branch changed or is recovering; save again",
                        409,
                        "project_history_session_changed",
                    )
                if workspace is None and active_branch != rootid:
                    # The Web runner owns active branch/placement workspace
                    # resolution; guessing the main workspace would mislabel it.
                    raise ProjectHistoryError(
                        "active branch workspace is required for this snapshot",
                        409,
                        "project_history_workspace_required",
                    )
                workspace = (
                    Path(workspace)
                    if workspace is not None
                    else Path(self.cfg.data_dir) / "agent-workspaces" / rootid
                )
                if workspace.is_symlink():
                    raise ProjectHistoryError(
                        "history source workspace is a symbolic link"
                    )
                workspace = workspace.resolve()
                if (
                    root == workspace
                    or root in workspace.parents
                    or workspace in root.parents
                ):
                    raise ProjectHistoryError(
                        "history source workspace overlaps the project folder"
                    )
                with _Tree(root) as tree:
                    self._archive_budget(tree)
                    manifest, payloads = self._snapshot(
                        frame, workspace, tree, active_branch
                    )
                    self._check_snapshot_guard(rootid, pid, root, guard)
                    revision = _hash(_json(manifest))
                    session = ".openai4s/sessions/" + rootid
                    revisions = tree.names(session + "/revisions")
                    if revision not in revisions and len(revisions) >= MAX_REVISIONS:
                        raise ProjectHistoryError(
                            "session history reached its revision limit"
                        )
                    try:
                        previous = json.loads(tree.read(session + "/index.json"))
                    except FileNotFoundError:
                        previous = {}
                    prefix = session + "/revisions/" + revision + "/"
                    now = int(time.time() * 1000)
                    if revision in revisions:
                        _, old_manifest = self._read_revision(tree, rootid, revision)
                        manifest["created_at"] = old_manifest["created_at"]
                    else:
                        manifest["created_at"] = now
                    if len(_json(manifest)) > MAX_METADATA_BYTES:
                        raise ProjectHistoryError(
                            "session manifest exceeds the archive limit"
                        )
                    for name, data in payloads.items():
                        tree.write(prefix + name, data, immutable=True)
                    tree.write(
                        prefix + "manifest.json", _json(manifest), immutable=True
                    )
                    self._write_settings(tree, self._settings(pid))
                    index = {
                        key: manifest[key]
                        for key in (
                            "session_id",
                            "branch_id",
                            "title",
                            "updated_at",
                            "message_count",
                            "cell_count",
                            "file_count",
                        )
                    }
                    index.update(
                        revision_id=revision,
                        last_saved_at=now,
                        revisions_count=len(set(revisions) | {revision}),
                    )
                    # A reader sees either the previous committed revision or
                    # this complete one. Convenience files are non-authoritative.
                    for name, data in payloads.items():
                        tree.write(session + "/" + name, data)
                    self._archive_budget(tree)
                    self._check_snapshot_guard(rootid, pid, root, guard)
                    tree.write(session + "/index.json", _json(index))
                result = {
                    "state": (
                        "unchanged"
                        if previous.get("revision_id") == revision
                        else "saved"
                    ),
                    "session_id": rootid,
                    "revision_id": revision,
                    "last_saved_at": now,
                    "omissions": manifest["omissions"],
                    "error": None,
                }
                self._states[pid] = result
                return result
            except Exception as error:
                return {**self._error(pid, error), "session_id": rootid}

    def _check_snapshot_guard(
        self, rootid: str, pid: str, root: Path, guard: dict
    ) -> None:
        frame = self.store.get_frame(rootid)
        if (
            frame is None
            or frame.get("project_id") != pid
            or self._root(pid) != root
            or self.store.session_export_guard(rootid) != guard
        ):
            raise ProjectHistoryError(
                "session branch or project folder changed; save again",
                409,
                "project_history_session_changed",
            )

    def _index(self, tree: _Tree, sid: str) -> dict:
        _identity(sid)
        row = json.loads(tree.read(f".openai4s/sessions/{sid}/index.json"))
        if not isinstance(row, dict) or row.get("session_id") != sid:
            raise ProjectHistoryError("invalid archived session index")
        _identity(row.get("revision_id"), digest=True)
        return row

    def status(self, pid: str) -> dict:
        with _LOCK:
            try:
                root = self._root(pid)
                with _Tree(root) as tree:
                    self._archive_budget(tree)
                    rows = [
                        self._index(tree, sid)
                        for sid in tree.names(".openai4s/sessions")
                    ]
                latest = max(
                    (int(row.get("last_saved_at") or 0) for row in rows), default=0
                )
                return {
                    "enabled": True,
                    "folder_path": str(root),
                    "history_path": str(root / ".openai4s"),
                    "state": "ready",
                    "sessions_count": len(rows),
                    "last_saved_at": latest or None,
                    "error": None,
                    **self._states.get(pid, {}),
                }
            except Exception as error:
                return {"enabled": False, **self._error(pid, error)}

    def list_sessions(self, pid: str) -> dict:
        with _LOCK:
            try:
                with _Tree(self._root(pid)) as tree:
                    rows = [
                        self._index(tree, sid)
                        for sid in tree.names(".openai4s/sessions")
                    ]
                rows.sort(
                    key=lambda row: int(row.get("last_saved_at") or 0), reverse=True
                )
                return {
                    "state": "ready",
                    "sessions": rows,
                    "read_only": True,
                    "untrusted": True,
                    "error": None,
                }
            except Exception as error:
                return {**self._error(pid, error, remember=False), "sessions": []}

    def _read_revision(
        self, tree: _Tree, sid: str, revision: str | None
    ) -> tuple[str, dict]:
        _identity(sid)
        revision = _identity(
            revision or self._index(tree, sid)["revision_id"], digest=True
        )
        raw = tree.read(f".openai4s/sessions/{sid}/revisions/{revision}/manifest.json")
        manifest = json.loads(raw)
        if (
            not isinstance(manifest, dict)
            or _hash(
                _json(
                    {
                        key: value
                        for key, value in manifest.items()
                        if key != "created_at"
                    }
                )
            )
            != revision
            or manifest.get("session_id") != sid
            or manifest.get("schema_version") != 1
        ):
            raise ProjectHistoryError("project history integrity check failed")
        return revision, manifest

    def read_session(self, pid: str, sid: str, revision_id: str | None = None) -> dict:
        with _LOCK:
            try:
                with _Tree(self._root(pid)) as tree:
                    revision, manifest = self._read_revision(tree, sid, revision_id)
                    payloads = {}
                    for name in ("messages.jsonl", "cells.json", "transcript.md"):
                        data = tree.read(
                            f".openai4s/sessions/{sid}/revisions/{revision}/{name}"
                        )
                        receipt = manifest["members"][name]
                        if (
                            _hash(data) != receipt["sha256"]
                            or len(data) != receipt["size_bytes"]
                        ):
                            raise ProjectHistoryError(
                                "project history integrity check failed"
                            )
                        payloads[name] = data
                    revisions = []
                    for value in tree.names(f".openai4s/sessions/{sid}/revisions"):
                        if _DIGEST.fullmatch(value):
                            _, archived = self._read_revision(tree, sid, value)
                            revisions.append(
                                {
                                    "revision_id": value,
                                    "created_at": archived.get("created_at"),
                                    "branch_id": archived.get("branch_id"),
                                }
                            )
                    revisions.sort(
                        key=lambda item: int(item.get("created_at") or 0), reverse=True
                    )
                return {
                    **manifest,
                    "revision_id": revision,
                    "revisions": revisions,
                    "messages": [
                        json.loads(line)
                        for line in payloads["messages.jsonl"].splitlines()
                        if line
                    ],
                    "cells": json.loads(payloads["cells.json"]),
                    "transcript": payloads["transcript.md"].decode("utf-8"),
                    "state": "ready",
                    "read_only": True,
                    "untrusted": True,
                    "error": None,
                }
            except Exception as error:
                return self._error(pid, error, remember=False)

    def read_file(self, pid: str, sid: str, revision_id: str | None, path: str) -> dict:
        with _LOCK:
            try:
                with _Tree(self._root(pid)) as tree:
                    revision, manifest = self._read_revision(tree, sid, revision_id)
                    record = next(
                        (
                            item
                            for item in manifest["files"]
                            if item.get("path") == path
                        ),
                        None,
                    )
                    if record is None:
                        raise ProjectHistoryError(
                            "archived file not found", 404, "history_not_found"
                        )
                    digest = _identity(record["sha256"], digest=True)
                    data = tree.read(".openai4s/objects/" + digest, MAX_FILE_BYTES)
                    if _hash(data) != digest or len(data) != record["size_bytes"]:
                        raise ProjectHistoryError(
                            "project history file integrity check failed"
                        )
                return {
                    "state": "ready",
                    "data": data,
                    "name": Path(str(record["name"])).name,
                    "content_type": mimetypes.guess_type(str(record["name"]))[0]
                    or "application/octet-stream",
                    "size_bytes": len(data),
                    "sha256": digest,
                    "revision_id": revision,
                    "read_only": True,
                    "untrusted": True,
                    "error": None,
                }
            except Exception as error:
                return self._error(pid, error, remember=False)

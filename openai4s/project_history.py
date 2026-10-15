"""Portable, inert project history under the explicitly selected .openai4s dir.

SQLite remains the live authority. This archive is an append-only human-readable
copy, never an execution restore or a source of permissions. File versions and
conversation records are content-addressed objects shared by every revision
that contains them, and revisions remain after the live project/session is
deleted.
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
import zlib
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any

from openai4s.artifact_restore import trusted_snapshot_roots
from openai4s.host.files import is_secret_path
from openai4s.project_folders import ProjectFolderError, project_folder_path
from openai4s.server.session_package import (
    _REDACTED,
    _is_secret_path,
    _sanitize,
    _secret_text_bytes,
    known_secret_bytes,
)
from openai4s.storage.branch_projection import project_branch_records

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_SESSION_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILES = 1000
MAX_RECORDS = 5000
MAX_REVISIONS = 1000
MAX_SCAN_ENTRIES = 20_000
#: Manifest format. 2 moved the payloads out of each revision directory into
#: shared chunk objects and dropped the frame's ``updated_at`` from identity.
_SCHEMA = 2
#: Each a JSONL list of records, stored as chunks under ``objects/``.
_MEMBERS = ("messages.jsonl", "cells.jsonl", "files.jsonl")
_CHUNK_BYTES = 256 * 1024
#: A record closes its chunk with probability 1/_CHUNK_SPLIT; no chunk holds
#: more than _CHUNK_RECORDS, which bounds the open tail each save rewrites.
_CHUNK_SPLIT = 8
_CHUNK_RECORDS = 2 * _CHUNK_SPLIT
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
#: Per-archive locks, keyed by the project folder. Saves of one archive run
#: one at a time; readers never wait for a save, only for the replacement of
#: the two pointer files they read (``index.json``, ``revisions.json``).
_GUARD = threading.Lock()
_SAVING: dict[str, threading.RLock] = {}
_POINTERS: dict[str, threading.RLock] = {}
#: Error code for a reached archive limit. Retrying cannot clear it, so the
#: deletion refusal names the way out instead of only "retry".
LIMIT_CODE = "project_history_limit"
#: The session's branch, checkpoint or folder moved while it was snapshotted.
CHANGED_CODE = "project_history_session_changed"


def _scrub(value: Any, secrets: tuple[bytes, ...]) -> Any:
    """Pattern-sanitize, then replace configured secret values verbatim.

    Pattern rules miss a provider key with no recognizable prefix, e.g. one a
    user pasted into chat. Replacing (not refusing) keeps a key in a message
    from making the session impossible to save, and therefore to delete.
    """
    texts = [text for text in (s.decode("utf-8", "ignore") for s in secrets) if text]

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            for text in texts:
                if text in item:
                    item = item.replace(text, _REDACTED)
            return item
        if isinstance(item, dict):
            return {key: walk(child) for key, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        return item

    return walk(_sanitize(value))


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


def _archive_lock(table: dict[str, threading.RLock], root: Path) -> threading.RLock:
    with _GUARD:
        return table.setdefault(str(root), threading.RLock())


def _chunks(lines: list[bytes]) -> list[bytes]:
    """Group JSONL records into content-defined chunks.

    A record closes its chunk when its own CRC selects it or the chunk reaches
    ``_CHUNK_RECORDS`` or ``_CHUNK_BYTES``. Each depends only on the records
    since the previous boundary, so appending records leaves every closed
    chunk byte-identical and therefore shared by address with the previous
    revision. Copying the whole payload into each revision made a long
    conversation's archive grow with the square of its length.
    """
    chunks: list[bytes] = []
    current: list[bytes] = []
    size = 0
    for line in lines:
        current.append(line)
        size += len(line)
        if (
            size >= _CHUNK_BYTES
            or len(current) >= _CHUNK_RECORDS
            or zlib.crc32(line) % _CHUNK_SPLIT == 0
        ):
            chunks.append(b"".join(current))
            current, size = [], 0
    if current:
        chunks.append(b"".join(current))
    return chunks


def _records(data: bytes) -> list[Any]:
    return [json.loads(line) for line in data.split(b"\n") if line]


def _transcript(title: Any, messages: list[dict]) -> bytes:
    """A readable rendering of the messages; derived, so never archived."""
    return (
        "# "
        + str(title)
        + "\n\n"
        + "\n\n".join(
            "## "
            + str(item.get("role") or "message")
            + "\n\n"
            + str(item.get("content") or "")
            for item in messages
        )
    ).encode("utf-8")


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
        # Measured by the first write that would grow the archive, then kept
        # current by every write; a save that adds nothing never walks it.
        self.used_bytes: int | None = None
        self.used_entries = 0

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
                        if self.used_bytes is not None:
                            self.used_entries += 1
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

    def stat(self, path: str) -> os.stat_result:
        """No-follow metadata through the pinned directory chain."""
        parts = _parts(path)
        parent = self.directory(parts[:-1])
        try:
            return os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(parent)

    def measure(self) -> int:
        """Walk the archive once; refuse unsafe entries and reached limits."""
        total = entries = 0
        stack = [(".openai4s",)]
        while stack:
            parts = stack.pop()
            try:
                descriptor = self.directory(parts)
            except FileNotFoundError:
                continue
            try:
                with os.scandir(descriptor) as items:
                    for entry in items:
                        entries += 1
                        if entries > MAX_SCAN_ENTRIES:
                            raise ProjectHistoryError(
                                "project history reached its entry limit",
                                409,
                                LIMIT_CODE,
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
                                "project history reached its storage limit",
                                409,
                                LIMIT_CODE,
                            )
            finally:
                os.close(descriptor)
        self.used_bytes, self.used_entries = total, entries
        return total

    def has_object(self, digest: str, size: int) -> bool:
        try:
            info = self.stat(".openai4s/objects/" + digest)
        except FileNotFoundError:
            return False
        return (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == size
        )

    def write(
        self,
        path: str,
        data: bytes,
        *,
        immutable: bool = False,
        content_addressed: bool = False,
    ) -> None:
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
                    # A content-addressed object is named by its SHA-256, which
                    # every download verifies; re-reading it on each save to
                    # compare bytes doubled the I/O of every autosave.
                    if content_addressed:
                        if existing.st_size != len(data):
                            raise ProjectHistoryError(
                                "immutable project history was modified"
                            )
                        return
                    if self.read(path, max(MAX_METADATA_BYTES, MAX_FILE_BYTES)) != data:
                        raise ProjectHistoryError(
                            "immutable project history was modified"
                        )
                    return
            delta = len(data) - (existing.st_size if existing else 0)
            if existing is None or delta > 0:
                used = self.measure() if self.used_bytes is None else self.used_bytes
                if used + delta > MAX_ARCHIVE_BYTES:
                    raise ProjectHistoryError(
                        "project history reached its storage limit", 409, LIMIT_CODE
                    )
                if existing is None and self.used_entries >= MAX_SCAN_ENTRIES:
                    raise ProjectHistoryError(
                        "project history reached its entry limit", 409, LIMIT_CODE
                    )
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
                    # An identical copy won the race; nothing new is stored.
                    return
            else:
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            if self.used_bytes is not None:
                self.used_bytes += delta
                if existing is None:
                    self.used_entries += 1
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
                            "project history contains too many entries", 409, LIMIT_CODE
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
        # (source, metadata, secret set) -> archived digest, so an unchanged
        # file is not re-read and re-hashed on every autosave.
        self._digests: dict[tuple, str] = {}

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

    def _settings(self, pid: str, secrets: tuple[bytes, ...] | None = None) -> dict:
        project = self.store.get_project(pid) or {}
        return _scrub(
            {
                "schema_version": 1,
                "project": {
                    key: project.get(key) or ""
                    for key in ("name", "description", "context")
                },
            },
            known_secret_bytes(self.store) if secrets is None else secrets,
        )

    def _write_settings(self, tree: _Tree, settings: dict) -> None:
        data = _json(settings)
        if len(data) > MAX_METADATA_BYTES:
            raise ProjectHistoryError("project settings exceed the archive limit")
        # A convenience for the user's own tools, not history: an edited copy
        # must not make every later save fail as "modified".
        try:
            tree.stat(".openai4s/.gitignore")
        except FileNotFoundError:
            tree.write(".openai4s/.gitignore", b"*\n", immutable=True)
        tree.write(
            f".openai4s/settings-revisions/{_hash(data)}.json", data, immutable=True
        )
        try:
            current = tree.read(".openai4s/settings.json")
        except (FileNotFoundError, ProjectHistoryError):
            current = None
        if current != data:
            tree.write(".openai4s/settings.json", data)

    def sync_project(self, pid: str, *, include_sessions: bool = False) -> dict:
        try:
            root = self._root(pid)
            settings = self._settings(pid)
            with _archive_lock(_SAVING, root), _Tree(root) as tree:
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
        # One read for every version and its snapshot path, not a
        # list_versions + version_meta query per Artifact and version.
        versions = self.store.list_root_artifact_versions(rootid)
        for artifact in artifacts[:MAX_FILES]:
            aid = str(artifact.get("artifact_id") or artifact.get("id") or "")
            for version in versions.get(aid, ()):
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
                snapshot = version.get("snapshot_path")
                if not snapshot:
                    omissions.append(
                        {"path": filename, "reason": "no_immutable_snapshot"}
                    )
                    continue
                lexical = Path(os.path.abspath(Path(str(snapshot)).expanduser()))
                try:
                    # Resolve parent aliases (/tmp -> /private/tmp) as the
                    # restore path does, never the final component: the
                    # no-follow read below decides whether that is a link.
                    path = lexical.parent.resolve(strict=True) / lexical.name
                except (OSError, RuntimeError):
                    omissions.append(
                        {"path": filename, "reason": "unsafe_missing_or_oversized_file"}
                    )
                    continue
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
        self,
        frame: dict,
        workspace: Path,
        tree: _Tree,
        branch_id: str,
        secrets: tuple[bytes, ...],
    ) -> tuple[dict, dict[str, bytes], dict[str, bytes]]:
        """Return the manifest, its chunk objects by digest, and the whole
        payloads (for the session's convenience copies)."""
        rootid = str(frame.get("frame_id") or frame.get("id"))
        pid = str(frame.get("project_id") or "default")
        omissions: list[dict] = []
        title = _scrub(frame.get("name") or "Conversation", secrets)
        # Past the cap keep the newest records: the end of a conversation is
        # what has not been archived yet, and what a deletion would lose.
        newest = self.store.list_branch_message_boundaries(
            rootid, branch_id=branch_id, limit=MAX_RECORDS + 1, newest_first=True
        )
        if len(newest) > MAX_RECORDS:
            omissions.append({"path": "messages", "reason": "record_count_limit"})
        messages = list(reversed(newest[:MAX_RECORDS]))
        messages = [
            _scrub(
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
                },
                secrets,
            )
            for item in messages
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
        # ``list_cells`` rows already carry every archived column; a
        # ``cell_detail`` query per cell on every save added nothing.
        cells = [
            _scrub(
                {
                    key: item.get(key)
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
                },
                secrets,
            )
            for item in cell_rows[-MAX_RECORDS:]
        ]
        records = {
            "messages.jsonl": [_json(item) + b"\n" for item in messages],
            "cells.jsonl": [_json(item) + b"\n" for item in cells],
        }
        payloads = {name: b"".join(lines) for name, lines in records.items()}
        payloads["transcript.md"] = _transcript(title, messages)
        if any(len(data) > MAX_METADATA_BYTES for data in payloads.values()):
            raise ProjectHistoryError("conversation text exceeds the archive limit")
        files = []
        total = 0
        # An unchanged file (same inode, size, times and secret set) whose
        # object is already archived is not re-read on every autosave.
        secrets_key = _hash(b"\0".join(secrets))
        if len(self._digests) > 100_000:
            self._digests.clear()
        with ExitStack() as opened:
            sources: dict[Path, _Tree] = {}
            for record, base, relative in self._file_sources(
                rootid, workspace, omissions
            ):
                name = relative.as_posix()
                try:
                    if base not in sources:
                        sources[base] = opened.enter_context(_Tree(base))
                    source = sources[base]
                    info = source.stat(name)
                    key = (
                        str(base),
                        name,
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                        secrets_key,
                        record.get("expected_sha256"),
                    )
                    digest = self._digests.get(key)
                    data = None
                    if not (
                        digest
                        and stat.S_ISREG(info.st_mode)
                        and info.st_nlink == 1
                        and tree.has_object(digest, info.st_size)
                    ):
                        data = source.read(name, MAX_FILE_BYTES)
                        digest = _hash(data)
                    size = info.st_size if data is None else len(data)
                    # Only retained files spend the budget; an omitted large
                    # file must not crowd out smaller ones after it.
                    if total + size > MAX_SESSION_BYTES:
                        omissions.append(
                            {"path": record["path"], "reason": "session_size_limit"}
                        )
                        continue
                    if data is not None:
                        if (
                            record.get("expected_sha256")
                            and record["expected_sha256"] != digest
                        ):
                            omissions.append(
                                {
                                    "path": record["path"],
                                    "reason": "artifact_checksum_mismatch",
                                }
                            )
                            continue
                        # Lossy decode, so binary files cannot carry a key past
                        # the pattern scan; configured values match as bytes.
                        if _secret_text_bytes(data) or any(
                            secret in data for secret in secrets
                        ):
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
                if data is not None:
                    # A corrupt history object is not a source-file omission.
                    # Refuse publication so a failure cannot stamp a success.
                    tree.write(
                        ".openai4s/objects/" + digest,
                        data,
                        immutable=True,
                        content_addressed=True,
                    )
                    self._digests[key] = digest
                total += size
                record.pop("expected_sha256", None)
                files.append({**record, "sha256": digest, "size_bytes": size})
        settings = {
            **self._settings(pid, secrets),
            "session": _scrub(
                {key: frame.get(key) for key in ("name", "model", "runtime_env")},
                secrets,
            ),
        }
        records["files.jsonl"] = [_json(item) + b"\n" for item in files]
        payloads["files.jsonl"] = b"".join(records["files.jsonl"])
        if len(payloads["files.jsonl"]) > MAX_METADATA_BYTES:
            raise ProjectHistoryError("file list exceeds the archive limit")
        objects: dict[str, bytes] = {}
        members = {}
        for name, lines in records.items():
            digests = []
            for chunk in _chunks(lines):
                digest = _hash(chunk)
                objects[digest] = chunk
                digests.append(digest)
            members[name] = {
                "sha256": _hash(payloads[name]),
                "size_bytes": len(payloads[name]),
                "chunks": digests,
            }
        # Identity is archived content only. The frame's ``updated_at`` moves
        # with every status change, which made an unchanged session look new.
        manifest = {
            "schema_version": _SCHEMA,
            "session_id": rootid,
            "branch_id": branch_id,
            "history_scope": {
                "messages": "active_branch_projection",
                "cells": "active_branch_projection",
                "workspace": "active_branch",
                "artifacts": "all_retained_session_versions",
            },
            "title": title,
            "settings": settings,
            "message_count": len(messages),
            "cell_count": len(cells),
            "file_count": len(files),
            "omissions": omissions,
            "members": members,
        }
        for data in (*payloads.values(), _json(manifest)):
            if any(secret in data for secret in secrets):
                raise ProjectHistoryError(
                    "project history snapshot still contains secret material"
                )
        return manifest, objects, payloads

    def sync_session(
        self,
        rootid: str,
        *,
        workspace: Path | None = None,
        branch_id: str | None = None,
    ) -> dict:
        pid = ""
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
                    CHANGED_CODE,
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
                raise ProjectHistoryError("history source workspace is a symbolic link")
            workspace = workspace.resolve()
            if (
                root == workspace
                or root in workspace.parents
                or workspace in root.parents
            ):
                raise ProjectHistoryError(
                    "history source workspace overlaps the project folder"
                )
            # Configured key values, so a key with no recognizable prefix
            # is still caught; the same residual check Session packages
            # fail closed on. Resolved once per save, not per use.
            secrets = known_secret_bytes(self.store)
            with _archive_lock(_SAVING, root), _Tree(root) as tree:
                manifest, objects, payloads = self._snapshot(
                    frame, workspace, tree, active_branch, secrets
                )
                self._check_snapshot_guard(rootid, pid, root, guard)
                revision = _hash(_json(manifest))
                session = ".openai4s/sessions/" + rootid
                # Chunks a previous revision already holds cost one stat each.
                for digest, chunk in objects.items():
                    tree.write(
                        ".openai4s/objects/" + digest,
                        chunk,
                        immutable=True,
                        content_addressed=True,
                    )
                try:
                    with _archive_lock(_POINTERS, root):
                        previous = json.loads(tree.read(session + "/index.json"))
                except (FileNotFoundError, ValueError):
                    # Only reports "unchanged"; a changed save rewrites it.
                    previous = {}
                if not isinstance(previous, dict):
                    previous = {}
                now = int(time.time() * 1000)
                try:
                    _, archived = self._read_revision(tree, rootid, revision)
                except FileNotFoundError:
                    # New, or an interrupted save never wrote its manifest.
                    archived = None
                if archived is None or previous.get("revision_id") != revision:
                    if archived is None:
                        revisions = tree.names(session + "/revisions")
                        if (
                            revision not in revisions
                            and len(revisions) >= MAX_REVISIONS
                        ):
                            raise ProjectHistoryError(
                                "session history reached its revision limit",
                                409,
                                LIMIT_CODE,
                            )
                        manifest["created_at"] = now
                        data = _json(manifest)
                        if len(data) > MAX_METADATA_BYTES:
                            raise ProjectHistoryError(
                                "session manifest exceeds the archive limit"
                            )
                        tree.write(
                            session + "/revisions/" + revision + "/manifest.json",
                            data,
                            immutable=True,
                        )
                    else:
                        created = archived.get("created_at")
                        manifest["created_at"] = (
                            created if isinstance(created, int) else now
                        )
                    count = self._record_revision(tree, session, manifest, revision)
                    self._write_settings(tree, self._settings(pid, secrets))
                    index = {
                        key: manifest[key]
                        for key in (
                            "session_id",
                            "branch_id",
                            "title",
                            "message_count",
                            "cell_count",
                            "file_count",
                        )
                    }
                    index.update(
                        updated_at=int(frame.get("updated_at") or 0),
                        revision_id=revision,
                        last_saved_at=now,
                        revisions_count=count,
                    )
                    # A reader sees either the previous committed revision or
                    # this complete one. Convenience files are non-authoritative.
                    for name, data in payloads.items():
                        tree.write(session + "/" + name, data)
                    self._check_snapshot_guard(rootid, pid, root, guard)
                    with _archive_lock(_POINTERS, root):
                        tree.write(session + "/index.json", _json(index))
                # Otherwise nothing archived changed, and nothing is written:
                # an unchanged session never adds a revision, so autosave
                # cannot spend the archive's limits on it.
            result = {
                "state": (
                    "unchanged" if previous.get("revision_id") == revision else "saved"
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
            # A session that moved on mid-snapshot is transient: the call
            # still reports it (deletion must not proceed), but it is not
            # remembered as the project's save state.
            transient = getattr(error, "code", "") == CHANGED_CODE
            return {
                **self._error(pid, error, remember=not transient),
                "session_id": rootid,
            }

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
                CHANGED_CODE,
            )

    @staticmethod
    def _listed_revisions(tree: _Tree, session: str) -> dict[str, dict]:
        """Read the non-authoritative revision list; ignore malformed rows."""
        try:
            with _archive_lock(_POINTERS, tree.root):
                rows = json.loads(tree.read(session + "/revisions.json"))
        except (FileNotFoundError, ValueError):
            return {}
        listed: dict[str, dict] = {}
        for row in rows if isinstance(rows, list) else ():
            if (
                isinstance(row, dict)
                and isinstance(row.get("revision_id"), str)
                and _DIGEST.fullmatch(row["revision_id"])
                and isinstance(row.get("created_at"), int)
                and isinstance(row.get("branch_id"), str)
            ):
                listed[row["revision_id"]] = {
                    key: row[key] for key in ("revision_id", "created_at", "branch_id")
                }
        return listed

    def _record_revision(
        self, tree: _Tree, session: str, manifest: dict, revision: str
    ) -> int:
        """List ``revision`` once; return how many revisions are listed."""
        listed = self._listed_revisions(tree, session)
        if revision in listed:
            return len(listed)
        listed[revision] = {
            "revision_id": revision,
            "created_at": int(manifest["created_at"]),
            "branch_id": str(manifest.get("branch_id") or ""),
        }
        with _archive_lock(_POINTERS, tree.root):
            tree.write(session + "/revisions.json", _json(list(listed.values())))
        return len(listed)

    def _index(self, tree: _Tree, sid: str) -> dict:
        _identity(sid)
        with _archive_lock(_POINTERS, tree.root):
            raw = tree.read(f".openai4s/sessions/{sid}/index.json")
        row = json.loads(raw)
        if not isinstance(row, dict) or row.get("session_id") != sid:
            raise ProjectHistoryError("invalid archived session index")
        _identity(row.get("revision_id"), digest=True)
        return row

    def _indexes(self, tree: _Tree) -> list[dict]:
        """Every committed session index; a session whose first save never
        published ``index.json`` has no revision to show yet, and a stray
        entry (a Finder ``.DS_Store``) is not a session."""
        rows = []
        for sid in tree.names(".openai4s/sessions"):
            if not _ID.fullmatch(sid):
                continue
            try:
                rows.append(self._index(tree, sid))
            except FileNotFoundError:
                continue
        return rows

    def forget_project(self, pid: str) -> None:
        """Drop a deleted project's remembered save state."""
        self._states.pop(pid, None)

    def status(self, pid: str, *, sessions: list[dict] | None = None) -> dict:
        """Read-only status; ``sessions`` reuses rows a caller already read.

        It does not walk the whole archive: limits and unsafe entries are
        reported by the save that meets them.
        """
        try:
            root = self._root(pid)
            if sessions is not None:
                rows = sessions
            else:
                with _Tree(root) as tree:
                    rows = self._indexes(tree)
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
            # A read failure is not a save outcome; remembering it would
            # keep reporting the error after reads recover.
            return {"enabled": False, **self._error(pid, error, remember=False)}

    def list_sessions(self, pid: str) -> dict:
        try:
            with _Tree(self._root(pid)) as tree:
                rows = self._indexes(tree)
            rows.sort(key=lambda row: int(row.get("last_saved_at") or 0), reverse=True)
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
            or manifest.get("schema_version") != _SCHEMA
        ):
            raise ProjectHistoryError("project history integrity check failed")
        return revision, manifest

    @staticmethod
    def _member(tree: _Tree, manifest: dict, name: str) -> bytes:
        """Reassemble one payload from its chunks, verifying each and the whole."""
        receipt = (manifest.get("members") or {}).get(name)
        if (
            not isinstance(receipt, dict)
            or not isinstance(receipt.get("chunks"), list)
            or len(receipt["chunks"]) > MAX_RECORDS
            or not isinstance(receipt.get("size_bytes"), int)
            or not 0 <= receipt["size_bytes"] <= MAX_METADATA_BYTES
        ):
            raise ProjectHistoryError("project history integrity check failed")
        parts = []
        size = 0
        for digest in receipt["chunks"]:
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise ProjectHistoryError("project history integrity check failed")
            data = tree.read(".openai4s/objects/" + digest)
            size += len(data)
            if _hash(data) != digest or size > receipt["size_bytes"]:
                raise ProjectHistoryError("project history integrity check failed")
            parts.append(data)
        data = b"".join(parts)
        if _hash(data) != receipt.get("sha256") or len(data) != receipt["size_bytes"]:
            raise ProjectHistoryError("project history integrity check failed")
        return data

    def read_session(self, pid: str, sid: str, revision_id: str | None = None) -> dict:
        try:
            with _Tree(self._root(pid)) as tree:
                revision, manifest = self._read_revision(tree, sid, revision_id)
                payloads = {
                    name: self._member(tree, manifest, name) for name in _MEMBERS
                }
                # The picker needs ids and times, not every manifest hashed;
                # the selected revision above is still fully verified.
                listed = self._listed_revisions(tree, f".openai4s/sessions/{sid}")
                revisions = []
                for value in tree.names(f".openai4s/sessions/{sid}/revisions"):
                    if not _DIGEST.fullmatch(value):
                        continue
                    row = listed.get(value)
                    if row is None:
                        try:
                            _, archived = self._read_revision(tree, sid, value)
                        except (FileNotFoundError, ProjectHistoryError):
                            # An interrupted save's partial directory is
                            # not a revision; the selected one is verified.
                            continue
                        row = {
                            "revision_id": value,
                            "created_at": archived.get("created_at"),
                            "branch_id": archived.get("branch_id"),
                        }
                    revisions.append(row)
                revisions.sort(
                    key=lambda item: int(item.get("created_at") or 0), reverse=True
                )
            messages = _records(payloads["messages.jsonl"])
            return {
                **manifest,
                "revision_id": revision,
                "revisions": revisions,
                "messages": messages,
                "cells": _records(payloads["cells.jsonl"]),
                "files": _records(payloads["files.jsonl"]),
                "transcript": _transcript(manifest.get("title"), messages).decode(
                    "utf-8"
                ),
                "state": "ready",
                "read_only": True,
                "untrusted": True,
                "error": None,
            }
        except Exception as error:
            return self._error(pid, error, remember=False)

    def read_file(self, pid: str, sid: str, revision_id: str | None, path: str) -> dict:
        try:
            with _Tree(self._root(pid)) as tree:
                revision, manifest = self._read_revision(tree, sid, revision_id)
                record = next(
                    (
                        item
                        for item in _records(
                            self._member(tree, manifest, "files.jsonl")
                        )
                        if isinstance(item, dict) and item.get("path") == path
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

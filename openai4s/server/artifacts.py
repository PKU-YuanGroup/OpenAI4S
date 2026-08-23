"""Versioned workspace artifact capture for persistent scientific sessions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import platform as _pf
import re
import shutil
import stat
import threading
import uuid
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from openai4s.artifact_restore import (
    ArtifactRestoreDenied,
    ArtifactRestoreRefused,
    ArtifactRestoreService,
    trusted_snapshot_roots,
)
from openai4s.execution import CaptureResult
from openai4s.server.errors import record_diagnostic
from openai4s.storage.artifacts import ArtifactDeliveryReferenceError

_JUNK_DIR_SEGMENTS = frozenset({"__pycache__", "node_modules", "site-packages", "venv"})
_EMBEDDED_IMAGE_TYPES = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)
_MAX_EMBEDDED_FIGURE_BYTES = 8 * 1024 * 1024
_MAX_ARTIFACT_RECEIPTS = 512
EventSink = Callable[[dict[str, Any]], None]
Broadcast = Callable[[str, dict[str, Any]], None]

_ARTIFACT_WRITER_LOCKS: dict[str, threading.RLock] = {}
_ARTIFACT_WRITER_LOCKS_GUARD = threading.Lock()


def _shared_artifact_writer_lock(data_dir: Path) -> threading.RLock:
    """Return the one in-process writer lock for a canonical data directory."""

    key = str(Path(data_dir).expanduser().resolve())
    with _ARTIFACT_WRITER_LOCKS_GUARD:
        lock = _ARTIFACT_WRITER_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ARTIFACT_WRITER_LOCKS[key] = lock
        return lock


_TEXT_EDIT_EXT = (
    ".txt",
    ".log",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".fasta",
    ".fa",
    ".nwk",
    ".treefile",
    ".xml",
    ".yaml",
    ".yml",
    ".sh",
    ".r",
    ".tex",
    ".html",
    ".htm",
    ".css",
)
_BINARY_EXT = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".pdb",
    ".cif",
    ".mol",
    ".mol2",
    ".sdf",
    ".xyz",
)


class ArtifactOperationError(Exception):
    """An artifact mutation that the HTTP layer can map to a response."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ArtifactSession(Protocol):
    root_frame_id: str
    project_id: str
    workspace: Path


WorkspaceFileState = tuple[int, int, int, int, int]
WorkspaceSnapshot = dict[str, WorkspaceFileState]


@dataclass(frozen=True)
class PromotionTarget:
    """A minimal ArtifactSession for REST-time cell promotion.

    Promoting a cell happens outside any live kernel session, so the gateway
    supplies just the three fields ``register_file`` needs rather than reviving
    a full SessionState.
    """

    root_frame_id: str
    project_id: str
    workspace: Path


@dataclass(frozen=True)
class FrozenCaptureSnapshot:
    """A fully-written immutable snapshot verified before SQLite sees it."""

    path: Path
    size_bytes: int
    checksum: str


@dataclass(frozen=True)
class _DelegatedCaptureToken:
    """Workspace baseline held across one delegated Code Cell."""

    before: WorkspaceSnapshot


@dataclass(frozen=True)
class _DelegatedCaptureClaim:
    """Exact live-file identity already captured under a child frame."""

    fingerprint: WorkspaceFileState
    failed: bool = False


def _secure_upload_directory_flags() -> int:
    """Flags required to pin one upload directory without following aliases."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if os.name != "posix" or not nofollow or not directory:
        raise RuntimeError("secure Artifact writes are unavailable on this platform")
    return (
        os.O_RDONLY
        | nofollow
        | directory
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _require_secure_upload_dirfd() -> None:
    """Fail closed when Python cannot express the required POSIX operations."""

    required = (os.open, os.stat, os.mkdir, os.unlink, os.rename, os.readlink)
    if any(operation not in os.supports_dir_fd for operation in required):
        raise RuntimeError("secure Artifact writes are unavailable on this platform")
    _secure_upload_directory_flags()


def _upload_inode(metadata: os.stat_result) -> tuple[int, int]:
    return (int(metadata.st_dev), int(metadata.st_ino))


class _PinnedUploadDirectory:
    """One no-follow directory chain retained for an upload transaction.

    The kernel may rename a workspace directory or replace its pathname with a
    symlink while the daemon is staging an edit. All live-file operations use
    this descriptor, so such a swap cannot redirect a write. ``assert_current``
    additionally detects when the recorded workspace pathname no longer names
    this inode, allowing the transaction to roll back instead of committing a
    live path whose bytes landed in a detached directory.
    """

    def __init__(
        self,
        descriptor: int,
        *,
        root: Path,
        parts: tuple[str, ...],
    ) -> None:
        self.fd = descriptor
        self.root = root
        self.parts = parts
        self.path = root.joinpath(*parts)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("Artifact upload parent is not a directory")
        self.identity = _upload_inode(metadata)

    def __enter__(self) -> "_PinnedUploadDirectory":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    @staticmethod
    def _component(name: str) -> str:
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or "\x00" in name
            or os.sep in name
            or (os.altsep is not None and os.altsep in name)
        ):
            raise OSError("Artifact upload path component is invalid")
        return name

    @classmethod
    def open_under(
        cls,
        root: Path,
        parts: tuple[str, ...],
        *,
        create: bool,
    ) -> "_PinnedUploadDirectory":
        """Open a parent from a trusted root, never following a child link."""

        _require_secure_upload_dirfd()
        root = Path(root)
        clean = tuple(cls._component(part) for part in parts)
        before = os.stat(root, follow_symlinks=False)
        descriptor = os.open(root, _secure_upload_directory_flags())
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode) or _upload_inode(
                before
            ) != _upload_inode(opened):
                raise OSError("Artifact workspace root changed during secure open")
            for part in clean:
                try:
                    child = os.open(
                        part,
                        _secure_upload_directory_flags(),
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, 0o777, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(
                        part,
                        _secure_upload_directory_flags(),
                        dir_fd=descriptor,
                    )
                try:
                    metadata = os.fstat(child)
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise OSError("Artifact upload parent is not a directory")
                except BaseException:
                    os.close(child)
                    raise
                os.close(descriptor)
                descriptor = child
            return cls(descriptor, root=root, parts=clean)
        except BaseException:
            os.close(descriptor)
            raise

    def assert_current(self) -> None:
        """Require the workspace pathname to still identify this directory."""

        with self.open_under(self.root, self.parts, create=False) as current:
            if current.identity != self.identity:
                raise OSError("Artifact upload parent changed during transaction")

    def _name_for(self, path: Path) -> str:
        if path.parent != self.path:
            raise OSError("Artifact transaction path changed parent")
        return self._component(path.name)

    def lstat(self, path: Path) -> os.stat_result:
        return os.stat(self._name_for(path), dir_fd=self.fd, follow_symlinks=False)

    def exists(self, path: Path) -> bool:
        try:
            self.lstat(path)
            return True
        except FileNotFoundError:
            return False

    def readlink(self, path: Path) -> str:
        return os.readlink(self._name_for(path), dir_fd=self.fd)

    def open_read(self, path: Path) -> int:
        return os.open(
            self._name_for(path),
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=self.fd,
        )

    def create_exclusive(self, path: Path, mode: int = 0o600) -> int:
        return os.open(
            self._name_for(path),
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            mode,
            dir_fd=self.fd,
        )

    def replace(self, source: Path, destination: Path) -> None:
        # POSIX ``rename`` replaces an existing non-directory destination and
        # is the dirfd-capable primitive whose support was checked above.
        os.rename(
            self._name_for(source),
            self._name_for(destination),
            src_dir_fd=self.fd,
            dst_dir_fd=self.fd,
        )

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        try:
            os.unlink(self._name_for(path), dir_fd=self.fd)
        except FileNotFoundError:
            if not missing_ok:
                raise

    def fsync(self) -> None:
        os.fsync(self.fd)


def _upload_file_state(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Fields that must remain stable while exact Artifact bytes are read."""

    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
        int(metadata.st_nlink),
    )


class _PinnedUploadFile:
    """A regular file descriptor kept bound to one name in a pinned parent.

    Opening with ``O_NOFOLLOW`` is not enough: another workspace writer can
    rename the final component immediately after ``open`` and leave the daemon
    hashing a detached inode.  Every verification therefore brackets the read
    with ``fstat``, then requires a no-follow ``lstat`` of the current name to
    be the same inode with the same mutable metadata.  The descriptor remains
    open across publication so the post-rename target can be proved to be the
    exact staged inode rather than merely another file with equal bytes.
    """

    def __init__(
        self,
        descriptor: int,
        *,
        directory: _PinnedUploadDirectory,
        path: Path,
    ) -> None:
        self.fd = descriptor
        self.directory = directory
        self.path = path

    def __enter__(self) -> "_PinnedUploadFile":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    @classmethod
    def open_existing(
        cls, directory: _PinnedUploadDirectory, path: Path
    ) -> "_PinnedUploadFile":
        return cls(directory.open_read(path), directory=directory, path=path)

    @classmethod
    def create(
        cls, directory: _PinnedUploadDirectory, path: Path
    ) -> "_PinnedUploadFile":
        return cls(directory.create_exclusive(path), directory=directory, path=path)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self.fd, view)
            if written <= 0:  # pragma: no cover - OS write contract
                raise OSError("upload stage write made no progress")
            view = view[written:]
        os.fsync(self.fd)

    def verified_bytes(
        self,
        *,
        named_as: Path | None = None,
        size_bytes: int | None = None,
        checksum: str | None = None,
    ) -> bytes:
        """Read stable bytes and prove the requested name still owns this fd."""

        before = os.fstat(self.fd)
        if not stat.S_ISREG(before.st_mode) or int(before.st_nlink) != 1:
            raise OSError("exact Artifact target is not a private regular file")
        os.lseek(self.fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(self.fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(self.fd)
        if _upload_file_state(before) != _upload_file_state(after) or total != int(
            after.st_size
        ):
            raise OSError("exact Artifact target changed while it was read")
        named = self.directory.lstat(named_as or self.path)
        if not os.path.samestat(after, named) or _upload_file_state(
            after
        ) != _upload_file_state(named):
            raise OSError("exact Artifact target name changed during verification")
        actual_checksum = digest.hexdigest()
        if size_bytes is not None and total != size_bytes:
            raise OSError("exact Artifact target size does not match")
        if checksum is not None and actual_checksum != checksum:
            raise OSError("exact Artifact target checksum does not match")
        return b"".join(chunks)


@dataclass
class _PinnedVersionStage:
    """Pending immutable snapshot held through rename and SQLite commit."""

    path: Path
    directory: _PinnedUploadDirectory
    file: _PinnedUploadFile

    def close(self) -> None:
        self.file.close()
        self.directory.close()


def _remote_receipt_input_versions(source: Mapping[str, Any]) -> list[str]:
    """Validate the ordered lineage identities in one Stage 11 receipt."""

    if source.get("kind") != "remote_compute":
        return []
    raw = source.get("input_versions")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ArtifactOperationError(
            500, "Host Artifact receipt lineage evidence is invalid"
        )
    versions: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ArtifactOperationError(
                500, "Host Artifact receipt lineage evidence is invalid"
            )
        if item not in versions:
            versions.append(item)
    return versions


def artifact_receipt_map(
    receipts: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Validate Host-owned evidence without folding duplicate filenames.

    A directory snapshot can prove only the final bytes at one filename.  Two
    receipts for that filename therefore cannot both be consumed exactly once,
    even when their digests happen to match.  Reject the whole set before any
    Artifact row or event is published instead of letting a dict comprehension
    silently turn two Host calls into one provenance claim.
    """

    validated: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(receipts):
        if index >= _MAX_ARTIFACT_RECEIPTS or not isinstance(raw, Mapping):
            raise ArtifactOperationError(
                500, "Host Artifact receipt evidence is invalid"
            )
        filename = raw.get("filename")
        checksum = raw.get("checksum")
        source = raw.get("source")
        if (
            not isinstance(filename, str)
            or not filename
            or len(filename) > 4096
            or not isinstance(checksum, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", checksum) is None
            or not isinstance(source, Mapping)
        ):
            raise ArtifactOperationError(
                500, "Host Artifact receipt evidence is invalid"
            )
        if filename in validated:
            raise ArtifactOperationError(
                500, "Host Artifact receipt filename was claimed more than once"
            )
        normalized_source = dict(source)
        if normalized_source.get("kind") == "remote_compute":
            normalized_source["input_versions"] = _remote_receipt_input_versions(
                normalized_source
            )
        item = dict(raw)
        item["checksum"] = checksum.lower()
        item["source"] = normalized_source
        validated[filename] = item
    return validated


def _take_delegated_artifact_receipts(result: object) -> dict[str, dict[str, Any]]:
    if not isinstance(result, dict):
        return {}
    raw = result.pop("_openai4s_artifact_receipts", None)
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise ArtifactOperationError(500, "Host Artifact receipt evidence is invalid")
    return artifact_receipt_map(raw)


class DelegatedCellCaptureHooks:
    """Bridge a child Agent's Cell boundary to the Web Artifact manager.

    The Agent runtime deliberately knows only the two duck-typed calls.  Frame,
    workspace, durable capture, and parent-sweep reconciliation stay in the
    server-owned Artifact service.
    """

    def __init__(
        self,
        manager: "ArtifactManager",
        session: ArtifactSession,
        producer_frame_id: str,
        emit: EventSink,
    ) -> None:
        self._manager = manager
        self._session = session
        self._producer_frame_id = producer_frame_id
        self._emit = emit

    def before(self, _action: object) -> _DelegatedCaptureToken:
        self._manager.protect_latest(self._session)
        return _DelegatedCaptureToken(self._manager.snapshot(self._session.workspace))

    def after(
        self,
        action: object,
        token: _DelegatedCaptureToken,
        result: dict[str, Any] | None,
    ) -> None:
        language = str(getattr(action, "language", None) or "python")
        producing_cell_id = None
        if isinstance(result, dict) and result.get("id"):
            producing_cell_id = str(result["id"])
        artifact_receipts = self._validated_receipts_or_claim(token, result)
        self._capture(
            token,
            language=language,
            producing_cell_id=producing_cell_id,
            artifact_receipts=artifact_receipts,
        )

    def before_native(self, action: object) -> _DelegatedCaptureToken:
        """Open the same exact boundary for one writing native Tool call."""

        return self.before(action)

    def after_native(
        self,
        _action: object,
        token: _DelegatedCaptureToken,
        result: object,
    ) -> None:
        """Capture a native write under the child frame, never its parent."""

        self._capture(
            token,
            language="native",
            producing_cell_id=None,
            artifact_receipts=self._validated_receipts_or_claim(token, result),
        )

    def after_native_with_receipts(
        self,
        _action: object,
        token: _DelegatedCaptureToken,
        _result: object,
        receipts: list[dict[str, Any]],
    ) -> None:
        """Capture Host-owned evidence without changing the legacy hook shape."""

        receipt_result = {"_openai4s_artifact_receipts": list(receipts)}
        self._capture(
            token,
            language="native",
            producing_cell_id=None,
            artifact_receipts=self._validated_receipts_or_claim(token, receipt_result),
        )

    def _validated_receipts_or_claim(
        self,
        token: _DelegatedCaptureToken,
        result: object,
    ) -> dict[str, dict[str, Any]]:
        """Keep invalid child evidence from being laundered by its parent."""

        try:
            return _take_delegated_artifact_receipts(result)
        except BaseException:
            self._manager.claim_delegated_changes(
                self._session.workspace, token.before, failed=True
            )
            raise

    def _capture(
        self,
        token: _DelegatedCaptureToken,
        *,
        language: str,
        producing_cell_id: str | None,
        artifact_receipts: Mapping[str, Mapping[str, Any]],
    ) -> None:
        try:
            capture = self._manager.capture(
                self._session,
                0,
                producing_cell_id,
                token.before,
                self._emit,
                language=language,
                producer_frame_id=self._producer_frame_id,
                artifact_receipts=artifact_receipts,
            )
        except BaseException:
            # The child write happened but could not be durably attributed.
            # Mark the exact unchanged files so the parent's outer sweep fails
            # closed instead of laundering them into parent provenance.
            self._manager.claim_delegated_changes(
                self._session.workspace, token.before, failed=True
            )
            raise
        self._manager.claim_delegated_artifacts(
            capture.artifacts,
            workspace=self._session.workspace,
        )


def _md_fence(body: str) -> str:
    """A backtick fence guaranteed longer than any backtick run in ``body``."""
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    return "`" * max(3, longest + 1)


def _write_confined_text(workspace: Path, relative: Path, content: str) -> Path:
    """Write under ``workspace`` without following a final-component symlink."""
    root = workspace.expanduser().resolve()
    directory = root / relative.parent
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise OSError("artifact directory must not be a symlink")
    resolved_directory = directory.resolve(strict=True)
    resolved_directory.relative_to(root)
    target = resolved_directory / relative.name
    if target.is_symlink():
        raise OSError("artifact target must not be a symlink")
    target.resolve(strict=False).relative_to(root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor: int | None = None
    try:
        if os.open in os.supports_dir_fd:
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            directory_descriptor = os.open(resolved_directory, directory_flags)
            descriptor = os.open(
                relative.name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        else:  # pragma: no cover - native Windows kernels are unsupported
            descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    if target.is_symlink() or not target.resolve(strict=True).is_relative_to(root):
        raise OSError("artifact target escaped its workspace")
    return target


def _same_interpreter(interpreter: Any, has_generation: bool = False) -> bool:
    """True when the kernel ran in this very process's interpreter.

    Only then may this process's own version strings be attributed to it.

    A *missing* interpreter is the daemon fallback only when there is no
    generation on record. With a generation but no interpreter — a legacy or
    imported one — the runtime is unknown, and stamping the daemon's Python
    version and implementation onto it is the same confidently-wrong provenance
    the package-list path already refuses. So a missing interpreter matches
    only in the no-generation case.
    """
    if not interpreter:
        return not has_generation
    # Same executable *and* same environment. A virtualenv's bin/python is a
    # symlink to the base python, so a resolved-executable match alone would
    # stamp the daemon's version/implementation onto a different environment.
    from openai4s.kernel.preinstall import _is_this_interpreter

    try:
        return _is_this_interpreter(str(interpreter))
    except OSError:
        return False


class ArtifactManager:
    #: A generation ends when its kernel does, so this cannot grow without
    #: bound in practice. The ceiling is a backstop against a session that
    #: restarts its kernel thousands of times, not a tuning knob.
    _FREEZE_CACHE_MAX = 256
    _DELEGATED_CLAIM_MAX = 10_000

    def __init__(
        self,
        *,
        data_dir: Path,
        store: Any,
        workspace_for: Callable[[str], Path],
        broadcast: Callable[[str, dict], None],
        guess_content_type: Callable[[str], str],
        checksum: Callable[[Path], str],
        trusted_delivery: bool = False,
        recover_uploads: bool = True,
        allow_external_workspace_root: bool = False,
    ) -> None:
        self.data_dir = data_dir
        self.store = store
        self.workspace_for = workspace_for
        self.broadcast = broadcast
        self.guess_content_type = guess_content_type
        self.checksum = checksum
        # Rollout is explicitly opt-in.  The flag-off path below remains the
        # pre-Stage-1 record-then-backfill behavior until its gate graduates.
        self.trusted_delivery = bool(trusted_delivery)
        self.allow_external_workspace_root = bool(allow_external_workspace_root)
        # (generation_id, interpreter) -> frozen packages, or None when the
        # interpreter refused to be read. See _frozen_packages.
        self._freeze_cache: dict[tuple[str, str], list[dict[str, Any]] | None] = {}
        self._freeze_lock = threading.Lock()
        # A delegated child is captured before the blocked parent Cell resumes.
        # The parent's later whole-workspace sweep sees the same mtime and would
        # otherwise add a false parent observation. Claims are exact live-file
        # identities and remain valid through every ancestor's nested sweep; a
        # subsequent write invalidates them by fingerprint. The map is bounded
        # independently of session life.
        self._delegated_claims: dict[str, dict[str, _DelegatedCaptureClaim]] = {}
        self._delegated_claim_lock = threading.Lock()
        self._delegated_claim_overflow: set[str] = set()
        # Upload spans immutable bytes, a live workspace path, and SQLite.  A
        # per-manager lock makes the journal below an exact single-writer
        # protocol for a filename instead of allowing two HTTP workers to
        # restore over one another after a fault.
        self._upload_lock = _shared_artifact_writer_lock(self.data_dir)
        if recover_uploads:
            # A second Web-delegated or direct/CLI manager may share this data
            # directory. Never mistake the first manager's active intent for a
            # crash merely because this instance was constructed mid-write.
            self.recover_upload_journals()
        # Web owns one manager whose workspace resolver understands every
        # session. Delegated HostDispatchers share the Store but otherwise
        # construct a constant-workspace fallback that cannot safely recover a
        # sibling session's retained journal. Publish the first manager on the
        # Store lifetime so every dispatcher reuses the canonical resolver.
        if getattr(self.store, "_artifact_manager_backend", None) is None:
            self.store._artifact_manager_backend = self

    @contextmanager
    def writer_transaction(self) -> Iterator[None]:
        """Serialize an entire higher-level Artifact policy/write boundary."""

        with self._upload_lock:
            yield

    def recover_upload_journals(self) -> None:
        """Reconcile retained intents without racing an active shared writer."""

        with self._upload_lock:
            self._recover_upload_journals()

    def _notify(
        self,
        root_frame_id: str | None,
        event: dict[str, Any],
        broadcast: Broadcast | None,
    ) -> None:
        if root_frame_id:
            (broadcast or self.broadcast)(root_frame_id, event)

    def versions_dir(self) -> Path:
        directory = self.data_dir / "artifact-versions"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def live_path(self, artifact: dict) -> Path:
        root_frame_id = artifact.get("root_frame_id") or "default"
        workspace = self.workspace_for(root_frame_id).expanduser().resolve()
        filename = artifact.get("filename")
        if not isinstance(filename, str) or not filename or "\x00" in filename:
            raise ArtifactOperationError(400, "artifact filename is invalid")
        candidate = Path(filename)
        if candidate.is_absolute():
            raise ArtifactOperationError(400, "artifact path must be relative")
        target = (workspace / candidate).expanduser().resolve()
        try:
            target.relative_to(workspace)
        except ValueError as error:
            raise ArtifactOperationError(
                400, "artifact live path escapes its workspace"
            ) from error
        return target

    def _upload_target(
        self,
        *,
        frame_id: str | None,
        filename: Any,
        exact: bool,
    ) -> tuple[Path, Path, str]:
        """Return a lexical target below the root a dirfd traversal will pin."""

        if exact:
            if not isinstance(filename, str) or not filename or "\x00" in filename:
                raise ArtifactOperationError(400, "artifact filename is invalid")
            candidate = Path(filename)
            if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
                raise ArtifactOperationError(
                    400, "artifact live path escapes its workspace"
                )
            parts = tuple(part for part in candidate.parts if part not in {"", "."})
            if not parts:
                raise ArtifactOperationError(400, "artifact filename is invalid")
            if frame_id:
                root = self._upload_workspace_root(str(frame_id))
                target = root.joinpath(*parts)
            else:
                # A frameless upload lives in the instance's dedicated uploads
                # directory, not in the default Session workspace.  Exact
                # edits must retain that same live namespace: treating a
                # missing frame as the literal frame id ``default`` made an
                # unchanged edit validate the wrong path and fail 500, while a
                # changed edit silently moved the Artifact into a workspace.
                # Keep ``data_dir`` as the trusted root so ``uploads`` is still
                # acquired by the same no-follow dirfd traversal as every
                # nested Session parent.
                root = self.data_dir.expanduser().resolve()
                target = root.joinpath("uploads", *parts)
            return root, target, str(Path(*parts))

        upload_name = Path(str(filename)).name
        if not upload_name or "\x00" in upload_name:
            raise ArtifactOperationError(400, "artifact filename is invalid")
        if frame_id:
            root = self._upload_workspace_root(str(frame_id))
            target = root / upload_name
        else:
            root = self.data_dir.expanduser().resolve()
            target = root / "uploads" / upload_name
        return root, target, upload_name

    def _upload_workspace_root(self, frame_id: str) -> Path:
        """Derive a workspace root without resolving its attacker-owned leaf."""

        lexical_data = Path(os.path.abspath(self.data_dir.expanduser()))
        canonical_data = lexical_data.resolve()
        lexical_workspace = Path(
            os.path.abspath(self.workspace_for(frame_id).expanduser())
        )
        if self.allow_external_workspace_root:
            return lexical_workspace.resolve()
        try:
            relative = lexical_workspace.relative_to(lexical_data)
        except ValueError:
            try:
                relative = lexical_workspace.relative_to(canonical_data)
            except ValueError as error:
                raise OSError(
                    "Artifact workspace escapes its trusted data root"
                ) from error
        # Canonicalizing only the trusted data root preserves configured aliases
        # while leaving the workspace leaf for O_NOFOLLOW to reject if a kernel
        # replaced that directory with a symlink before this transaction began.
        return canonical_data.joinpath(*relative.parts)

    def _exact_artifact_target(self, artifact: dict) -> tuple[Path, Path, str]:
        """Recover an exact live target from its trusted current version path.

        Artifact metadata may be renamed without moving the historical live
        file, so ``artifact.filename`` is not an authority for an exact edit or
        restore.  The current version row is.  Keep the final component lexical
        for the no-follow dirfd open below; resolving it here would turn an
        attacker-created alias into an apparently legitimate outside path.
        """

        version_id = artifact.get("latest_version_id")
        current = self.store.version_meta(version_id) if version_id else None
        if not isinstance(current, dict) or current.get("artifact_id") != artifact.get(
            "artifact_id"
        ):
            raise ArtifactOperationError(409, "artifact head metadata is inconsistent")
        frame_id = artifact.get("root_frame_id")
        if frame_id:
            root = self._upload_workspace_root(str(frame_id))
            live_root = root
        else:
            root = self.data_dir.expanduser().resolve()
            live_root = root / "uploads"
        raw_path = current.get("path")
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise ArtifactOperationError(409, "artifact live path is unavailable")
        candidate = Path(raw_path).expanduser()
        target = Path(
            os.path.abspath(
                candidate if candidate.is_absolute() else live_root / candidate
            )
        )
        try:
            relative = target.relative_to(live_root)
        except ValueError as error:
            raise ArtifactOperationError(
                409, "artifact live path escapes its workspace"
            ) from error
        if not relative.parts:
            raise ArtifactOperationError(409, "artifact live path is invalid")
        # ``stored_filename`` is presentation metadata.  It deliberately does
        # not select the filesystem target.
        stored_filename = str(artifact.get("filename") or relative)
        return root, target, stored_filename

    @staticmethod
    def _open_upload_parent(
        root: Path,
        target: Path,
        *,
        create: bool,
    ) -> _PinnedUploadDirectory:
        try:
            relative_parent = target.parent.relative_to(root)
        except ValueError as error:
            raise OSError("Artifact upload parent escapes its trusted root") from error
        return _PinnedUploadDirectory.open_under(
            root,
            tuple(relative_parent.parts),
            create=create,
        )

    def _open_journal_upload_parent(
        self, payload: Mapping[str, Any]
    ) -> _PinnedUploadDirectory:
        frame_id = payload.get("workspace_frame_id", payload.get("frame_id"))
        root = (
            self._upload_workspace_root(str(frame_id))
            if isinstance(frame_id, str) and frame_id
            else self.data_dir.expanduser().resolve()
        )
        target = Path(str(payload["target"]))
        directory = self._open_upload_parent(root, target, create=False)
        expected = (
            int(payload["target_parent_dev"]),
            int(payload["target_parent_ino"]),
        )
        if directory.identity != expected:
            directory.close()
            raise OSError("Artifact upload journal parent identity changed")
        return directory

    def restore_live_path(self, artifact: dict, current: dict) -> Path:
        """Resolve the exact live file while rejecting workspace escapes."""
        current_id = current.get("version_id")
        if current_id != artifact.get("latest_version_id"):
            raise PermissionError("artifact live path is not the current version")
        _root, target, _filename = self._exact_artifact_target(artifact)
        return target

    def _stage_version_bytes_pinned(
        self, filename: str, data: bytes
    ) -> _PinnedVersionStage:
        """Stage immutable bytes while retaining their parent and inode."""

        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
        root = self.data_dir.expanduser().resolve()
        directory = _PinnedUploadDirectory.open_under(
            root, ("artifact-versions",), create=True
        )
        pending = directory.path / f".pending-{uuid.uuid4().hex}__{safe}"
        pinned: _PinnedUploadFile | None = None
        try:
            pinned = _PinnedUploadFile.create(directory, pending)
            pinned.write(data)
            pinned.verified_bytes(
                named_as=pending,
                size_bytes=len(data),
                checksum=hashlib.sha256(data).hexdigest(),
            )
            directory.fsync()
            directory.assert_current()
            return _PinnedVersionStage(pending, directory, pinned)
        except BaseException:
            if pinned is not None:
                pinned.close()
            try:
                directory.unlink(pending, missing_ok=True)
                directory.fsync()
            finally:
                directory.close()
            raise

    @staticmethod
    def _promote_version_stage(
        stage: _PinnedVersionStage,
        final: Path,
        *,
        size_bytes: int,
        checksum: str,
    ) -> None:
        """Rename a held pending inode and prove the final name still owns it."""

        stage.file.verified_bytes(
            named_as=stage.path,
            size_bytes=size_bytes,
            checksum=checksum,
        )
        stage.directory.assert_current()
        stage.directory.replace(stage.path, final)
        stage.file.verified_bytes(
            named_as=final,
            size_bytes=size_bytes,
            checksum=checksum,
        )
        stage.directory.fsync()
        stage.directory.assert_current()

    @staticmethod
    def _upload_path_exists(
        path: Path, directory: _PinnedUploadDirectory | None = None
    ) -> bool:
        if directory is not None:
            return directory.exists(path)
        return os.path.lexists(os.fspath(path))

    @staticmethod
    def _remove_upload_path(
        path: Path, directory: _PinnedUploadDirectory | None = None
    ) -> None:
        if not ArtifactManager._upload_path_exists(path, directory):
            return
        metadata = directory.lstat(path) if directory is not None else path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            raise OSError("upload transaction path is a directory")
        if directory is not None:
            directory.unlink(path)
        else:
            path.unlink()

    @staticmethod
    def _fsync_directory(
        path: Path, directory: _PinnedUploadDirectory | None = None
    ) -> None:
        if directory is not None:
            if directory.path != path:
                raise OSError("Artifact transaction fsync changed directory")
            directory.fsync()
            return
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_durable_upload_file(
        path: Path,
        data: bytes,
        *,
        directory: _PinnedUploadDirectory | None = None,
    ) -> None:
        descriptor: int | None = None
        try:
            descriptor = (
                directory.create_exclusive(path)
                if directory is not None
                else os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            )
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:  # pragma: no cover - OS write contract
                    raise OSError("upload stage write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        ArtifactManager._fsync_directory(path.parent, directory)

    @staticmethod
    def _read_upload_journal(journal: Path) -> dict[str, Any]:
        descriptor: int | None = None
        try:
            descriptor = os.open(journal, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or status.st_size > 64 * 1024:
                raise OSError("upload journal is not a bounded regular file")
            chunks: list[bytes] = []
            remaining = status.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    raise OSError("upload journal ended before its recorded size")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise OSError("upload journal grew while it was read")
            after = os.fstat(descriptor)
            if (
                status.st_dev != after.st_dev
                or status.st_ino != after.st_ino
                or status.st_size != after.st_size
                or status.st_mtime_ns != after.st_mtime_ns
                or status.st_ctime_ns != after.st_ctime_ns
            ):
                raise OSError("upload journal changed while it was read")
            value = json.loads(b"".join(chunks).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("upload journal is not an object")
            return value
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _upload_file_matches(
        path: Path,
        size_bytes: int,
        checksum: str,
        directory: _PinnedUploadDirectory | None = None,
    ) -> bool:
        descriptor: int | None = None
        try:
            if directory is not None:
                with _PinnedUploadFile.open_existing(directory, path) as pinned:
                    pinned.verified_bytes(
                        size_bytes=size_bytes,
                        checksum=checksum,
                    )
                return True
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or int(status.st_nlink) != 1
                or status.st_size != size_bytes
            ):
                return False
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            named = os.stat(path, follow_symlinks=False)
            return bool(
                _upload_file_state(status) == _upload_file_state(after)
                and os.path.samestat(after, named)
                and _upload_file_state(after) == _upload_file_state(named)
                and digest.hexdigest() == checksum
            )
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _describe_upload_live(
        path: Path,
        directory: _PinnedUploadDirectory | None = None,
        *,
        reject_aliases: bool = False,
    ) -> dict[str, Any]:
        if not ArtifactManager._upload_path_exists(path, directory):
            return {"had_live": False, "previous_kind": "missing"}
        metadata = directory.lstat(path) if directory is not None else path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            if reject_aliases:
                raise OSError("exact Artifact target must not be a symlink")
            return {
                "had_live": True,
                "previous_kind": "symlink",
                "previous_symlink": (
                    directory.readlink(path)
                    if directory is not None
                    else os.readlink(path)
                ),
            }
        descriptor = (
            directory.open_read(path)
            if directory is not None
            else os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        )
        try:
            if directory is not None:
                pinned = _PinnedUploadFile(
                    descriptor,
                    directory=directory,
                    path=path,
                )
                descriptor = None
                try:
                    data = pinned.verified_bytes()
                finally:
                    pinned.close()
                return {
                    "had_live": True,
                    "previous_kind": "regular",
                    "previous_size_bytes": len(data),
                    "previous_checksum": hashlib.sha256(data).hexdigest(),
                }
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise OSError("upload target is not a regular file or symlink")
            if int(before.st_nlink) != 1:
                raise OSError("exact Artifact target must not be multiply linked")
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or size != after.st_size
            ):
                raise OSError("upload target changed while it was inspected")
            return {
                "had_live": True,
                "previous_kind": "regular",
                "previous_size_bytes": size,
                "previous_checksum": digest.hexdigest(),
            }
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _read_exact_upload_live(path: Path, directory: _PinnedUploadDirectory) -> bytes:
        """Read the exact regular inode below the already-pinned parent."""

        with _PinnedUploadFile.open_existing(directory, path) as pinned:
            return pinned.verified_bytes()

    def _write_upload_journal(self, journal: Path, payload: dict[str, Any]) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        temporary = journal.with_name(journal.name + ".part")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:  # pragma: no cover - OS write contract
                    raise OSError("upload journal write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, journal)
            self._fsync_directory(journal.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _validated_upload_journal(self, journal: Path, payload: Any) -> dict[str, Any]:
        if journal.is_symlink() or not journal.is_file():
            raise ValueError("upload journal is not a regular file")
        if not isinstance(payload, dict) or payload.get("schema_version") != 4:
            raise ValueError("unsupported upload journal")
        version_id = payload.get("version_id")
        artifact_id = payload.get("artifact_id")
        checksum = payload.get("checksum")
        size_bytes = payload.get("size_bytes")
        if (
            not isinstance(version_id, str)
            or not re.fullmatch(r"v-[A-Za-z0-9_-]+", version_id)
            or journal.name != f".upload-{version_id}.json"
            or not isinstance(artifact_id, str)
            or not artifact_id
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or not isinstance(payload.get("target_parent_dev"), int)
            or payload["target_parent_dev"] < 0
            or not isinstance(payload.get("target_parent_ino"), int)
            or payload["target_parent_ino"] <= 0
            or not isinstance(payload.get("published_dev"), int)
            or payload["published_dev"] < 0
            or not isinstance(payload.get("published_ino"), int)
            or payload["published_ino"] <= 0
            or not isinstance(payload.get("final_dev"), int)
            or payload["final_dev"] < 0
            or not isinstance(payload.get("final_ino"), int)
            or payload["final_ino"] <= 0
        ):
            raise ValueError("invalid upload journal identity")

        data_root = self.data_dir.expanduser().resolve()
        versions_root = self.data_dir.expanduser().resolve() / "artifact-versions"
        if versions_root.is_symlink() or journal.parent != versions_root:
            raise ValueError("upload journal directory is unsafe")
        paths: dict[str, Path] = {}
        for key in ("target", "staged", "pending", "final", "backup"):
            value = payload.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError("invalid upload journal path")
            candidate = Path(os.path.abspath(value))
            if key in {"pending", "final"} and not candidate.is_relative_to(
                versions_root
            ):
                raise ValueError("upload journal path escapes data directory")
            paths[key] = candidate
        if paths["staged"].parent != paths["target"].parent:
            raise ValueError("upload stage is not beside its target")
        if paths["backup"].parent != paths["target"].parent:
            raise ValueError("upload backup is not beside its target")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", paths["target"].name or "artifact")
        if (
            not re.fullmatch(
                re.escape(paths["target"].name) + r"\.[0-9a-f]{8}\.part",
                paths["staged"].name,
            )
            or not re.fullmatch(
                r"\.pending-[0-9a-f]{32}__" + re.escape(safe),
                paths["pending"].name,
            )
            or paths["final"].name != f"{version_id}__{safe}"
            or paths["backup"].name
            != f".{paths['target'].name}.upload-{version_id}.backup"
        ):
            raise ValueError("upload journal path does not match its transaction")
        if not isinstance(payload.get("had_live"), bool):
            raise ValueError("invalid upload journal live-file state")
        previous_kind = payload.get("previous_kind")
        if previous_kind not in {"missing", "regular", "symlink"}:
            raise ValueError("invalid previous upload file kind")
        if bool(payload["had_live"]) != (previous_kind != "missing"):
            raise ValueError("inconsistent previous upload file state")
        if previous_kind == "regular":
            if (
                not isinstance(payload.get("previous_size_bytes"), int)
                or payload["previous_size_bytes"] < 0
                or not isinstance(payload.get("previous_checksum"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload["previous_checksum"])
            ):
                raise ValueError("invalid previous upload checksum")
        if previous_kind == "symlink" and not isinstance(
            payload.get("previous_symlink"), str
        ):
            raise ValueError("invalid previous upload symlink")
        previous_version_id = payload.get("previous_version_id")
        if previous_version_id is not None and not isinstance(previous_version_id, str):
            raise ValueError("invalid previous upload version")
        previous_updated_at = payload.get("previous_updated_at")
        if previous_updated_at is not None and not isinstance(previous_updated_at, int):
            raise ValueError("invalid previous upload timestamp")
        previous_filename = payload.get("previous_filename")
        previous_content_type = payload.get("previous_content_type")
        if previous_version_id is None:
            if previous_filename is not None or previous_content_type is not None:
                raise ValueError("invalid previous upload metadata")
        elif (
            not isinstance(previous_filename, str)
            or not previous_filename
            or (
                previous_content_type is not None
                and not isinstance(previous_content_type, str)
            )
        ):
            raise ValueError("invalid previous upload metadata")
        frame_id = payload.get("frame_id")
        if frame_id is not None and (not isinstance(frame_id, str) or not frame_id):
            raise ValueError("invalid upload journal frame")
        workspace_frame_id = payload.get("workspace_frame_id", frame_id)
        if workspace_frame_id is not None and (
            not isinstance(workspace_frame_id, str) or not workspace_frame_id
        ):
            raise ValueError("invalid upload journal workspace frame")
        expected_root = (
            self._upload_workspace_root(workspace_frame_id)
            if workspace_frame_id is not None
            else data_root / "uploads"
        )
        # Keep this comparison lexical. The recovery caller subsequently opens
        # every target-parent component from the trusted root by no-follow
        # dirfd traversal and checks the journaled parent inode. Resolving only
        # ``expected_root`` here changes
        # ``/var/...`` into ``/private/var/...`` on macOS while the journal
        # paths remain under the equally valid lexical spelling, causing every
        # frameless upload in a TemporaryDirectory to fail as a false escape.
        # Resolving both sides is not equivalent: an existing final-component
        # symlink is transaction state that publication replaces without
        # following, so resolving it would inspect an unrelated target.
        try:
            paths["target"].relative_to(expected_root)
        except ValueError as error:
            raise ValueError(
                "upload journal target does not match its frame"
            ) from error
        if paths["target"] == expected_root:
            raise ValueError("upload journal target does not match its frame")
        return {**payload, **paths, "journal": journal}

    def _upload_path_matches_previous(
        self,
        path: Path,
        payload: dict[str, Any],
        directory: _PinnedUploadDirectory | None = None,
    ) -> bool:
        kind = payload["previous_kind"]
        if kind == "missing":
            return not self._upload_path_exists(path, directory)
        if kind == "symlink":
            try:
                metadata = (
                    directory.lstat(path) if directory is not None else path.lstat()
                )
                return (
                    stat.S_ISLNK(metadata.st_mode)
                    and (
                        directory.readlink(path)
                        if directory is not None
                        else os.readlink(path)
                    )
                    == payload["previous_symlink"]
                )
            except OSError:
                return False
        return self._upload_file_matches(
            path,
            payload["previous_size_bytes"],
            payload["previous_checksum"],
            directory,
        )

    def _remove_new_upload_path(
        self,
        path: Path,
        payload: dict[str, Any],
        directory: _PinnedUploadDirectory | None = None,
        *,
        pinned: _PinnedUploadFile | None = None,
    ) -> None:
        if not self._upload_path_exists(path, directory):
            return
        if pinned is not None:
            pinned.verified_bytes(
                named_as=path,
                size_bytes=payload["size_bytes"],
                checksum=payload["checksum"],
            )
        elif not self._upload_file_matches(
            path, payload["size_bytes"], payload["checksum"], directory
        ):
            raise OSError("refusing to remove unverified upload transaction bytes")
        self._remove_upload_path(path, directory)

    def _restore_upload_files(
        self,
        payload: dict[str, Any],
        live_parent: _PinnedUploadDirectory,
        *,
        previous_file: _PinnedUploadFile | None = None,
        previous_bytes: bytes | None = None,
        staged_file: _PinnedUploadFile | None = None,
        version_stage: _PinnedVersionStage | None = None,
    ) -> None:
        target = payload["target"]
        backup = payload["backup"]
        had_live = bool(payload["had_live"])
        if self._upload_path_exists(backup, live_parent):
            backup_is_previous = False
            if previous_file is not None:
                try:
                    previous_file.verified_bytes(
                        named_as=backup,
                        size_bytes=payload.get("previous_size_bytes"),
                        checksum=payload.get("previous_checksum"),
                    )
                    backup_is_previous = True
                except OSError:
                    backup_is_previous = False
            else:
                backup_is_previous = self._upload_path_matches_previous(
                    backup, payload, live_parent
                )
            try:
                self._remove_new_upload_path(
                    target,
                    payload,
                    live_parent,
                    pinned=staged_file,
                )
            except OSError:
                if previous_bytes is None:
                    raise
                # Exact writers hold the exclusive mutation lease. A different
                # final component here is therefore the hostile name swap that
                # caused this rollback, not an independent accepted writer.
                self._remove_upload_path(target, live_parent)
            if backup_is_previous:
                live_parent.replace(backup, target)
                if previous_file is not None:
                    previous_file.verified_bytes(named_as=target)
            elif previous_bytes is not None:
                # A final-component swap between verification and rename can
                # put an attacker-selected inode under our reserved backup
                # name.  Do not bless it merely because its bytes collide;
                # discard that transaction-owned link and reconstruct the
                # previous durable truth from the already-verified held read.
                self._remove_upload_path(backup, live_parent)
                self._restore_previous_upload_bytes(target, previous_bytes, live_parent)
            else:
                raise OSError("upload backup does not match the previous live entry")
            self._fsync_directory(target.parent, live_parent)
        elif not had_live and self._upload_path_exists(target, live_parent):
            try:
                self._remove_new_upload_path(
                    target,
                    payload,
                    live_parent,
                    pinned=staged_file,
                )
            except OSError:
                # The name was proven absent under the process-wide writer
                # lock, then reserved by this journal. A different inode here
                # is the hostile final-component swap that triggered abort,
                # not a separately admitted Artifact writer.
                self._remove_upload_path(target, live_parent)
            self._fsync_directory(target.parent, live_parent)
        elif had_live and not self._upload_path_matches_previous(
            target, payload, live_parent
        ):
            if previous_bytes is None:
                raise OSError("previous upload live entry cannot be recovered")
            self._remove_upload_path(target, live_parent)
            self._restore_previous_upload_bytes(target, previous_bytes, live_parent)
            self._fsync_directory(target.parent, live_parent)

        self._remove_new_upload_path(payload["staged"], payload, live_parent)
        for key in ("pending", "final"):
            try:
                self._remove_new_upload_path(
                    payload[key],
                    payload,
                    version_stage.directory if version_stage is not None else None,
                    pinned=version_stage.file if version_stage is not None else None,
                )
            except OSError:
                if version_stage is None:
                    raise
                # Both names are random transaction-owned entries under the
                # pinned versions directory. A different inode here is the
                # hostile name swap that caused the abort, never historical
                # data, so discard it rather than retaining attacker bytes.
                self._remove_upload_path(payload[key], version_stage.directory)
        if self._upload_path_exists(backup, live_parent):
            if not self._upload_path_matches_previous(backup, payload, live_parent):
                raise OSError("refusing to remove an unverified upload backup")
            self._remove_upload_path(backup, live_parent)
        payload["journal"].unlink(missing_ok=True)
        self._fsync_directory(payload["journal"].parent)

    @staticmethod
    def _restore_previous_upload_bytes(
        target: Path,
        previous_bytes: bytes,
        live_parent: _PinnedUploadDirectory,
    ) -> None:
        rollback = target.with_name(
            f".{target.name}.rollback-{uuid.uuid4().hex[:8]}.part"
        )
        with _PinnedUploadFile.create(live_parent, rollback) as restored:
            restored.write(previous_bytes)
            restored.verified_bytes(named_as=rollback)
            live_parent.replace(rollback, target)
            restored.verified_bytes(
                named_as=target,
                size_bytes=len(previous_bytes),
                checksum=hashlib.sha256(previous_bytes).hexdigest(),
            )

    def _abort_upload(
        self,
        payload: dict[str, Any],
        live_parent: _PinnedUploadDirectory,
        *,
        previous_file: _PinnedUploadFile | None = None,
        previous_bytes: bytes | None = None,
        staged_file: _PinnedUploadFile | None = None,
        version_stage: _PinnedVersionStage | None = None,
    ) -> None:
        if previous_bytes is None and payload.get("previous_kind") == "regular":
            previous_version_id = payload.get("previous_version_id")
            previous_meta = (
                self.store.version_meta(previous_version_id)
                if previous_version_id
                else None
            )
            if isinstance(previous_meta, dict):
                _snapshot, recovered = ArtifactRestoreService(
                    store=self.store,
                    primary_snapshot_dir=self.versions_dir(),
                    trusted_snapshot_dirs=trusted_snapshot_roots(self.data_dir),
                    resolve_live_path=self.restore_live_path,
                ).verified_snapshot_bytes(previous_meta)
                if len(recovered) != payload.get(
                    "previous_size_bytes"
                ) or hashlib.sha256(recovered).hexdigest() != payload.get(
                    "previous_checksum"
                ):
                    raise OSError("previous upload snapshot does not match journal")
                previous_bytes = recovered
        meta = self.store.version_meta(payload["version_id"])
        if isinstance(meta, dict):
            self.store.rollback_artifact_upload(
                artifact_id=payload["artifact_id"],
                version_id=payload["version_id"],
                previous_version_id=payload.get("previous_version_id"),
                previous_updated_at=payload.get("previous_updated_at"),
                previous_filename=payload.get("previous_filename"),
                previous_content_type=payload.get("previous_content_type"),
            )
        else:
            artifact = self.store.get_artifact(payload["artifact_id"])
            previous_version_id = payload.get("previous_version_id")
            if previous_version_id is None:
                if artifact is not None:
                    raise RuntimeError(
                        "upload journal does not match the current Artifact"
                    )
            else:
                previous = self.store.version_meta(previous_version_id)
                if (
                    not isinstance(artifact, dict)
                    or artifact.get("latest_version_id") != previous_version_id
                    or artifact.get("updated_at") != payload.get("previous_updated_at")
                    or not isinstance(previous, dict)
                    or previous.get("artifact_id") != payload["artifact_id"]
                ):
                    raise RuntimeError(
                        "upload journal previous head is no longer current"
                    )
        self._restore_upload_files(
            payload,
            live_parent,
            previous_file=previous_file,
            previous_bytes=previous_bytes,
            staged_file=staged_file,
            version_stage=version_stage,
        )

    def _recover_upload_journal(self, journal: Path) -> None:
        payload = self._validated_upload_journal(
            journal, self._read_upload_journal(journal)
        )
        with self._open_journal_upload_parent(payload) as live_parent:
            meta = self.store.version_meta(payload["version_id"])
            artifact = self.store.get_artifact(payload["artifact_id"])
            committed = bool(
                isinstance(meta, dict)
                and isinstance(artifact, dict)
                and artifact.get("latest_version_id") == payload["version_id"]
                and meta.get("artifact_id") == payload["artifact_id"]
                and meta.get("frame_id") == payload.get("frame_id")
                and meta.get("path") == str(payload["target"])
                and meta.get("snapshot_path") == str(payload["final"])
                and meta.get("size_bytes") == payload["size_bytes"]
                and meta.get("checksum") == payload["checksum"]
            )
            if committed:
                final_ok = self._upload_file_matches(
                    payload["final"], payload["size_bytes"], payload["checksum"]
                )
                if final_ok:
                    final_status = os.stat(payload["final"], follow_symlinks=False)
                    final_ok = _upload_inode(final_status) == (
                        payload["final_dev"],
                        payload["final_ino"],
                    )
                target_ok = self._upload_file_matches(
                    payload["target"],
                    payload["size_bytes"],
                    payload["checksum"],
                    live_parent,
                )
                if target_ok:
                    target_status = live_parent.lstat(payload["target"])
                    target_ok = _upload_inode(target_status) == (
                        payload["published_dev"],
                        payload["published_ino"],
                    )
                if final_ok and target_ok:
                    self._remove_new_upload_path(
                        payload["staged"], payload, live_parent
                    )
                    self._remove_new_upload_path(payload["pending"], payload)
                    backup = payload["backup"]
                    if self._upload_path_exists(backup, live_parent):
                        if not self._upload_path_matches_previous(
                            backup, payload, live_parent
                        ):
                            raise OSError("upload recovery backup is not exact")
                        self._remove_upload_path(backup, live_parent)
                    journal.unlink(missing_ok=True)
                    self._fsync_directory(journal.parent)
                    return
            # An uncommitted publish, or a committed row without both promised
            # byte copies, is not a successful upload. Restore the exact previous
            # head and live entry instead of guessing which half should win.
            self._abort_upload(payload, live_parent)

    def _recover_upload_journals(self) -> None:
        directory = self.data_dir / "artifact-versions"
        if directory.is_symlink():
            raise RuntimeError("artifact upload recovery directory is unsafe")
        if not directory.exists():
            return
        if not directory.is_dir():
            raise RuntimeError("artifact upload recovery directory is unsafe")
        for discovered in sorted(directory.glob(".upload-v-*.json")):
            # Canonicalize an equivalent data-dir spelling without resolving
            # the final component: a journal symlink must reach the validator
            # as a symlink and fail closed, never be followed to its target.
            journal = discovered.parent.resolve(strict=True) / discovered.name
            try:
                self._recover_upload_journal(journal)
            except BaseException as error:
                record_diagnostic(error, surface="artifacts:upload:recover")
                # Keep the journal for an operator or the next retry and fail
                # closed. Serving the store while an upload has two unresolved
                # truths (SQLite and the workspace) would make the corrupt head
                # externally visible.
                raise RuntimeError(
                    "artifact upload recovery could not be verified"
                ) from None

    def freeze_capture_snapshot(
        self, filename: str, source_path: Path
    ) -> FrozenCaptureSnapshot:
        """Atomically freeze and verify one live output before its DB record.

        A unique temporary is streamed and fsynced, then atomically renamed to
        its immutable name.  Both the source identity and the final snapshot
        are checked: a file changed while it was copied, a short write, or a
        checksum disagreement leaves no snapshot and cannot reach SQLite.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
        token = uuid.uuid4().hex
        directory = self.versions_dir()
        pending = directory / f".capture-{token}.part"
        final = directory / f"capture-{token}__{safe}"
        source_descriptor: int | None = None
        target_descriptor: int | None = None
        try:
            source_descriptor = os.open(
                source_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            before = os.fstat(source_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise OSError("artifact source is not a regular file")
            target_descriptor = os.open(
                pending,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(source_descriptor, 1024 * 1024)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(target_descriptor, view)
                    if written <= 0:  # pragma: no cover - OS write contract
                        raise OSError("artifact snapshot write made no progress")
                    view = view[written:]
                size += len(chunk)
                digest.update(chunk)
            os.fsync(target_descriptor)
            after = os.fstat(source_descriptor)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or size != after.st_size
            ):
                raise OSError("artifact source changed during snapshot freeze")
            os.close(target_descriptor)
            target_descriptor = None
            os.replace(pending, final)
            directory_descriptor = os.open(
                directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            checksum = digest.hexdigest()
            if final.stat().st_size != size or self.checksum(final) != checksum:
                raise OSError("artifact snapshot verification failed")
            return FrozenCaptureSnapshot(final, size, checksum)
        except Exception:
            pending.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            raise
        finally:
            if target_descriptor is not None:
                os.close(target_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)

    def write_version_snapshot(
        self,
        version_id: str,
        filename: str,
        *,
        src_path: Path | None = None,
        data: bytes | None = None,
    ) -> None:
        """Freeze one version's bytes while its DB path stays live/mutable."""
        try:
            current = self.store.version_meta(version_id)
            existing = (current or {}).get("snapshot_path")
            if existing and Path(existing).is_file():
                return
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
            snapshot = self.versions_dir() / f"{version_id}__{safe}"
            if data is not None:
                snapshot.write_bytes(data)
            elif src_path is not None:
                shutil.copyfile(src_path, snapshot)
            else:
                return
            self.store.set_version_snapshot(version_id, str(snapshot))
        except OSError:
            pass

    def protect_latest(self, session: ArtifactSession) -> None:
        """Backfill immutable bytes before a later cell overwrites a live file."""
        try:
            artifacts = self.store.list_artifacts(
                {"root_frame_id": session.root_frame_id}
            )
        except Exception:  # noqa: BLE001
            return
        for artifact in artifacts:
            version_id = artifact.get("latest_version_id")
            if not version_id:
                continue
            try:
                meta = self.store.version_meta(version_id)
                if not meta or meta.get("snapshot_path") or not meta.get("path"):
                    continue
                path = Path(meta["path"])
                if path.is_file():
                    self.write_version_snapshot(
                        version_id,
                        meta.get("filename") or artifact.get("filename") or "artifact",
                        src_path=path,
                    )
            except Exception:  # noqa: BLE001
                continue

    def restore(self, artifact_id: str, version_id: str) -> dict:
        """Restore a historical snapshot as a fresh immutable version."""

        with self._upload_lock:
            return self._restore_locked(artifact_id, version_id)

    def materialise_version(
        self,
        *,
        source_version_id: str,
        filename: str,
        frame_id: str | None,
        workspace_frame_id: str,
        project_id: str,
        raw: bytes,
        producing_cell_id: str | None = None,
    ) -> dict:
        """Materialise a sibling version through the durable upload writer."""

        with self._upload_lock:
            source = self.store.version_meta(source_version_id)
            if not isinstance(source, dict):
                raise ArtifactOperationError(404, "materialisation source not found")
            try:
                _snapshot, verified_raw = ArtifactRestoreService(
                    store=self.store,
                    primary_snapshot_dir=self.versions_dir(),
                    trusted_snapshot_dirs=trusted_snapshot_roots(self.data_dir),
                    resolve_live_path=self.restore_live_path,
                ).verified_snapshot_bytes(source)
            except (OSError, ArtifactRestoreDenied, ArtifactRestoreRefused) as error:
                raise ArtifactOperationError(
                    409, "materialisation source could not be verified"
                ) from error
            if verified_raw != raw:
                raise ArtifactOperationError(
                    409, "materialisation source changed before publication"
                )
            uploaded = self._upload_locked(
                {
                    "filename": filename,
                    "frame_id": frame_id,
                    "workspace_frame_id": workspace_frame_id,
                    "project_id": project_id,
                },
                raw_override=raw,
                materialise_source_version_id=source_version_id,
                producing_cell_id=producing_cell_id,
            )
            artifact = self.store.get_artifact(uploaded["artifact_id"])
            version_id = str((artifact or {}).get("latest_version_id") or "")
            version = self.store.version_meta(version_id) if version_id else None
            if not isinstance(version, dict):
                raise ArtifactOperationError(
                    500, "materialised artifact version is unavailable"
                )
            return {
                **version,
                "materialised_from_version_id": source_version_id,
            }

    def _restore_locked(self, artifact_id: str, version_id: str) -> dict:
        """Restore through the exact upload journal while its writer lock is held."""

        artifact = self.store.get_artifact(artifact_id)
        version = self.store.version_meta(version_id)
        if not artifact or not version or version.get("artifact_id") != artifact_id:
            return {"error": "version not found"}
        try:
            if version_id == artifact.get("latest_version_id"):
                raise ArtifactRestoreRefused(
                    "restore requires a historical, non-current version"
                )
            _snapshot, source_data = ArtifactRestoreService(
                store=self.store,
                primary_snapshot_dir=self.versions_dir(),
                trusted_snapshot_dirs=trusted_snapshot_roots(self.data_dir),
                resolve_live_path=self.restore_live_path,
            ).verified_snapshot_bytes(version)
            self._upload_locked(
                {},
                broadcast=lambda _root, _event: None,
                exact_artifact=artifact,
                raw_override=source_data,
                restore_source_version_id=version_id,
            )
            current_artifact = self.store.get_artifact(artifact_id)
            if not current_artifact or not current_artifact.get("latest_version_id"):
                raise RuntimeError("restored artifact head is unavailable")
            restored_version_id = str(current_artifact["latest_version_id"])
            restored = self.store.version_meta(restored_version_id)
            if not restored or restored.get("artifact_id") != artifact_id:
                raise RuntimeError("restored artifact version is unavailable")
        except ArtifactRestoreDenied as refusal:
            return {"error": f"restore failed: {refusal}", "code": "restore_denied"}
        except ArtifactRestoreRefused as refusal:
            # Author-written, and the product: "checksum verification failed" is
            # exactly what the user has to be told, and suppressing it to be
            # safe would leave them with a restore that failed for no stated
            # reason.
            return {"error": f"restore failed: {refusal}", "code": "restore_refused"}
        except (
            ArtifactOperationError,
            KeyError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            # Anything else escaped from the OS layer with its own text. An
            # `OSError` here names the snapshot it could not read -- an absolute
            # path under the data directory, so the account's username -- and
            # this dict is the body of
            # `POST /artifacts/<id>/versions/<vid>/restore`. The original goes
            # to the operator record, redacted once and paired with the id.
            record_diagnostic(error, surface="artifacts:restore")
            return {"error": "restore failed", "code": "restore_failed"}

        current_artifact = self.store.get_artifact(artifact_id)
        root_frame_id = artifact.get("root_frame_id")
        if root_frame_id:
            self.broadcast(
                root_frame_id,
                {
                    "type": "artifact_created",
                    "root_frame_id": root_frame_id,
                    "artifact": {
                        "id": artifact_id,
                        "artifact_id": artifact_id,
                        "filename": restored.get("filename"),
                        "content_type": restored.get("content_type"),
                        "version_id": restored_version_id,
                        "root_frame_id": root_frame_id,
                        "restored_from_version_id": version_id,
                    },
                },
            )
        return {
            "ok": True,
            "artifact": current_artifact,
            "version_id": restored_version_id,
            "restored_from_version_id": version_id,
            "snapshot_verified": True,
        }

    def edit(
        self,
        artifact_id: str,
        content: str,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Serialize the complete exact edit decision and publication."""

        with self._upload_lock:
            return self._edit_locked(
                artifact_id,
                content,
                broadcast=broadcast,
            )

    def _edit_locked(
        self,
        artifact_id: str,
        content: str,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Save edited text as a new version without changing its live path."""
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise ArtifactOperationError(404, "artifact not found")
        if not is_text_editable(artifact.get("filename"), artifact.get("content_type")):
            raise ArtifactOperationError(415, "artifact is not text-editable")

        raw = content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        try:
            unchanged_version_id = self._validated_unchanged_exact_artifact(
                artifact_id,
                raw=raw,
                checksum=digest,
            )
        except ArtifactOperationError:
            raise
        except (OSError, RuntimeError) as error:
            record_diagnostic(error, surface="artifacts:edit:unchanged_live")
            raise ArtifactOperationError(500, "write failed") from error
        if unchanged_version_id is not None:
            return {
                "ok": True,
                "artifact_id": artifact_id,
                "version_id": unchanged_version_id,
                "size_bytes": len(raw),
                "unchanged": True,
            }
        try:
            # The public edit contract keeps its historical event shape below;
            # the exact-Artifact upload still owns the durable filesystem/SQLite
            # transaction, but its projection is intentionally swallowed here.
            uploaded = self.replace_artifact_text(
                artifact_id,
                content,
                broadcast=lambda _root, _event: None,
            )
        except ArtifactOperationError as error:
            if error.code < 500:
                raise
            # Do not expose the absolute path embedded in an OS failure.
            raise ArtifactOperationError(500, "write failed") from error

        version_id = uploaded.get("version_id")
        if not isinstance(version_id, str) or not version_id:
            raise ArtifactOperationError(500, "write failed")
        root_frame_id = artifact.get("root_frame_id")
        self._notify(
            root_frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": artifact_id,
                    "filename": artifact["filename"],
                    "version_id": version_id,
                    "root_frame_id": root_frame_id,
                },
            },
            broadcast,
        )
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "version_id": version_id,
            "size_bytes": len(raw),
            "unchanged": False,
        }

    def _validated_unchanged_exact_artifact(
        self,
        artifact_id: str,
        *,
        raw: bytes,
        checksum: str,
    ) -> str | None:
        """Return an unchanged head only after verifying its exact live inode."""

        with self._upload_lock:
            artifact = self.store.get_artifact(artifact_id)
            if not artifact:
                raise ArtifactOperationError(404, "artifact not found")
            version_id = artifact.get("latest_version_id")
            current = self.store.version_meta(version_id) if version_id else None
            if not current or current.get("checksum") != checksum:
                return None
            if current.get("size_bytes") != len(raw):
                raise OSError("exact Artifact head has inconsistent size metadata")
            root, target, _filename = self._exact_artifact_target(artifact)
            with self._open_upload_parent(root, target, create=False) as live_parent:
                with _PinnedUploadFile.open_existing(live_parent, target) as live_file:
                    live_file.verified_bytes(
                        size_bytes=len(raw),
                        checksum=checksum,
                    )
                    live_parent.assert_current()
                    # Rebind immediately before releasing the transaction lock;
                    # this is the unchanged path's linearization point.
                    live_file.verified_bytes(
                        size_bytes=len(raw),
                        checksum=checksum,
                    )
            if not current.get("snapshot_path"):
                # Preserve edit's legacy opportunistic freeze, but use the
                # caller bytes that were just proven to be this private live
                # inode instead of reopening the pathname after validation.
                self.write_version_snapshot(
                    str(version_id),
                    str(artifact.get("filename") or "artifact"),
                    data=raw,
                )
            return str(version_id)

    def rename(
        self,
        artifact_id: str,
        filename: str | None,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        with self._upload_lock:
            return self._rename_locked(
                artifact_id,
                filename,
                broadcast=broadcast,
            )

    def _rename_locked(
        self,
        artifact_id: str,
        filename: str | None,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Rename artifact metadata; the historical live file stays in place."""
        if not filename:
            raise ArtifactOperationError(400, "filename required")
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise ArtifactOperationError(404, "artifact not found")
        self.live_path({**artifact, "filename": filename})
        self.store.rename_artifact(artifact_id, filename)
        root_frame_id = artifact.get("root_frame_id")
        self._notify(
            root_frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": artifact_id,
                    "filename": filename,
                    "root_frame_id": root_frame_id,
                },
            },
            broadcast,
        )
        return {"ok": True, "artifact_id": artifact_id, "filename": filename}

    @staticmethod
    def _upload_bytes(payload: dict) -> bytes:
        """The exact bytes an upload carries, or a refusal.

        Two ways this used to rewrite scientific data without saying so.

        `b64decode` was called without `validate=True`, and in that mode it
        *silently discards* characters outside the base64 alphabet -- so a
        payload corrupted in transit decodes to different bytes and raises
        nothing. The artifact then carries a checksum computed over the wrong
        content, which is worse than a missing checksum because it is believed.

        And when decoding did raise, the fallback stored
        `encoded.encode("utf-8")`: the literal base64 *text* became the file.
        Upload a `.npy` with one character lost and the artifact contains the
        base64 string, versioned, hashed and indistinguishable from data.

        A caller that wants to upload text says so with `content_text`. A
        caller that sends base64 gets base64 or an error. The three fields are
        mutually exclusive because "which one did you mean" has no safe
        default.
        """
        fields = [
            name
            for name in ("content_base64", "content", "content_text")
            if payload.get(name) not in (None, "")
        ]
        if len(fields) > 1:
            raise ArtifactOperationError(
                400,
                "upload carries "
                + " and ".join(sorted(fields))
                + "; supply exactly one, because which one is authoritative "
                "cannot be guessed",
            )
        if not fields:
            return b""

        field = fields[0]
        value = payload[field]
        if field == "content_text":
            if not isinstance(value, str):
                raise ArtifactOperationError(400, "content_text must be a string")
            return value.encode("utf-8")
        if not isinstance(value, str):
            raise ArtifactOperationError(400, f"{field} must be a base64 string")
        # Whitespace is transport formatting -- plenty of tools wrap base64 at
        # 76 columns -- so it is stripped rather than rejected. Anything else
        # outside the alphabet is corruption, and `validate=True` is what makes
        # the difference visible: without it those characters are dropped and
        # the payload decodes to *different bytes* with no error at all.
        compact = re.sub(r"\s+", "", value)
        try:
            return base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ArtifactOperationError(
                400,
                f"{field} is not valid base64 ({error}); "
                "send content_text to upload text",
            ) from error

    def upload(
        self,
        payload: dict,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Serialize one recoverable upload transaction."""

        with self._upload_lock:
            return self._upload_locked(payload, broadcast=broadcast)

    def replace_artifact_text(
        self,
        artifact_id: str,
        content: str,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Publish an exact Artifact version through the upload transaction.

        Unlike the public upload surface, this trusted path preserves an
        existing relative filename, including a nested one.  It still uses
        the same durable snapshot/live/SQLite journal, and it cannot create or
        retarget an Artifact from caller-provided scope fields.
        """

        if not isinstance(content, str):
            raise ArtifactOperationError(400, "content must be a string")
        with self._upload_lock:
            artifact = self.store.get_artifact(artifact_id)
            if not artifact:
                raise ArtifactOperationError(404, "artifact not found")
            uploaded = self._upload_locked(
                {"content_text": content},
                broadcast=broadcast,
                exact_artifact=artifact,
            )
            # Keep the identity returned to the trusted editor inside the same
            # writer critical section.  Re-reading the head after releasing
            # this lock could report a later writer's version as this save.
            current = self.store.get_artifact(artifact_id)
            if not current or not current.get("latest_version_id"):
                raise ArtifactOperationError(500, "artifact version was not recorded")
            return {**uploaded, "version_id": current["latest_version_id"]}

    def _upload_locked(
        self,
        payload: dict,
        *,
        broadcast: Broadcast | None = None,
        exact_artifact: dict[str, Any] | None = None,
        raw_override: bytes | None = None,
        restore_source_version_id: str | None = None,
        materialise_source_version_id: str | None = None,
        producing_cell_id: str | None = None,
    ) -> dict:
        """Decode and register one JSON/base64 upload as a versioned artifact.

        The ordering is the contract. This used to be
        `target.write_bytes(raw)` followed by the same-name lookup and then
        `save_artifact`, whose scope resolution can still refuse -- so a
        `project_id` that did not match the frame's left the previous version's
        row naming a path whose bytes were now the *rejected* upload's. That is
        client-reachable rather than theoretical: `app.js` sends
        `S.project || undefined` and this method defaults the field to
        `"default"`, so an upload into a non-default-project session with the
        field omitted takes exactly that branch.

        Now every refusal happens first and the bytes are durably staged beside
        the target.  The immutable snapshot and live file are then published by
        a callback inside the repository savepoint, before the new head becomes
        visible; a durable journal lets startup either finish committed cleanup
        or restore the previous live file after an interrupted publish.  A
        handled failure leaves the previous live bytes, Artifact head, checksum,
        version count and event count all unchanged.
        """
        filename = (
            exact_artifact.get("filename")
            if exact_artifact is not None
            else payload.get("filename") or f"upload-{uuid.uuid4().hex[:8]}"
        )
        frame_id = (
            exact_artifact.get("root_frame_id")
            if exact_artifact is not None
            else payload.get("frame_id")
        )
        workspace_frame_id = payload.get("workspace_frame_id")
        if workspace_frame_id is None and frame_id:
            scope = self.store.resolve_frame_scope(str(frame_id))
            workspace_frame_id = scope.get("root_frame_id") or frame_id
        # `None` when the client said nothing, not `"default"`.
        #
        # `artifact_write_scope` treats a non-None `project_id` as an assertion
        # about the frame's project and refuses when the two disagree -- which
        # is right. Defaulting here turned "the client did not say" into "the
        # client said `default`", so uploading into any session outside the
        # `default` project raised `project_id conflicts with producer frame`
        # from a request that named no project at all. Every session in a real
        # project was un-uploadable-to. The resolver already falls back to
        # `"default"` itself when there is no producer frame, so the frameless
        # case is unchanged.
        project_id = (
            exact_artifact.get("project_id")
            if exact_artifact is not None
            else payload.get("project_id")
        )
        raw = raw_override if raw_override is not None else self._upload_bytes(payload)

        if exact_artifact is not None:
            root, target, stored_filename = self._exact_artifact_target(exact_artifact)
        else:
            root, target, stored_filename = self._upload_target(
                frame_id=workspace_frame_id,
                filename=filename,
                exact=False,
            )
        try:
            live_parent = self._open_upload_parent(root, target, create=True)
        except (OSError, RuntimeError) as error:
            record_diagnostic(error, surface="artifacts:upload:secure_parent")
            raise ArtifactOperationError(500, "upload staging failed") from error
        with live_parent:
            return self._upload_locked_in_parent(
                payload,
                raw=raw,
                target=target,
                stored_filename=stored_filename,
                frame_id=frame_id,
                workspace_frame_id=workspace_frame_id,
                project_id=project_id,
                live_parent=live_parent,
                broadcast=broadcast,
                exact_artifact=exact_artifact,
                restore_source_version_id=restore_source_version_id,
                materialise_source_version_id=materialise_source_version_id,
                producing_cell_id=producing_cell_id,
            )

    def _upload_locked_in_parent(
        self,
        payload: dict,
        *,
        raw: bytes,
        target: Path,
        stored_filename: str,
        frame_id: str | None,
        workspace_frame_id: str | None,
        project_id: str | None,
        live_parent: _PinnedUploadDirectory,
        broadcast: Broadcast | None,
        exact_artifact: dict[str, Any] | None,
        restore_source_version_id: str | None,
        materialise_source_version_id: str | None,
        producing_cell_id: str | None,
    ) -> dict:
        """Run one upload using only the pinned live-parent descriptor."""

        target_exists = self._upload_path_exists(target, live_parent)
        if target_exists:
            if materialise_source_version_id is not None:
                raise FileExistsError(
                    f"{stored_filename!r} already exists in this session's "
                    "workspace; materialising would overwrite it. Pass "
                    "filename= to choose another name."
                )
            if stat.S_ISDIR(live_parent.lstat(target).st_mode):
                raise ArtifactOperationError(409, "upload target is a directory")

        # Both of these can refuse, and neither touches disk.
        try:
            _explicit, root_frame_id, project_id = self.store.artifact_write_scope(
                frame_id=frame_id, project_id=project_id
            )
        except ValueError as conflict:
            # A scope disagreement is the caller's, not the daemon's. It used to
            # leave the repository as a bare `ValueError`, reach the dispatcher's
            # catch-all and be answered `500 internal_error` -- so a client that
            # named the wrong project was told the server had broken, with
            # nothing to act on. The message is the repository's own and names
            # only field names.
            raise ArtifactOperationError(409, str(conflict)) from conflict
        existing = exact_artifact
        if existing is None:
            existing = self.store.artifact_by_scope_filename(
                target.name,
                root_frame_id=root_frame_id,
                project_id=project_id,
            )
        if materialise_source_version_id is not None and existing is not None:
            raise FileExistsError(
                f"{stored_filename!r} already exists in this session; "
                "materialising would create a duplicate Artifact"
            )

        previous_file: _PinnedUploadFile | None = None
        previous_bytes: bytes | None = None
        if exact_artifact is not None and existing is not None:
            previous_version_id = existing.get("latest_version_id")
            previous = (
                self.store.version_meta(previous_version_id)
                if previous_version_id
                else None
            )
            if not isinstance(previous, dict):
                raise ArtifactOperationError(
                    409, "artifact head metadata is inconsistent"
                )
            expected_checksum = str(previous.get("checksum") or "")
            expected_size = previous.get("size_bytes")
            if not expected_checksum or expected_size is None:
                raise ArtifactOperationError(
                    409, "artifact head metadata is inconsistent"
                )
            try:
                previous_file = _PinnedUploadFile.open_existing(live_parent, target)
                previous_bytes = previous_file.verified_bytes(
                    size_bytes=int(expected_size),
                    checksum=expected_checksum,
                )
                live_parent.assert_current()
            except OSError as error:
                if previous_file is not None:
                    previous_file.close()
                    previous_file = None
                record_diagnostic(error, surface="artifacts:upload:previous_live")
                if restore_source_version_id is not None:
                    raise ArtifactRestoreRefused(
                        "workspace file has unversioned changes; save them before restore"
                    ) from error
                raise ArtifactOperationError(500, "upload staging failed") from error
            try:
                if not previous.get("snapshot_path"):
                    self.write_version_snapshot(
                        str(previous_version_id),
                        stored_filename,
                        data=previous_bytes,
                    )
                frozen = self.store.version_meta(previous_version_id)
                if not isinstance(frozen, dict) or frozen.get(
                    "artifact_id"
                ) != existing.get("artifact_id"):
                    raise OSError("previous Artifact snapshot was not recorded")
                _snapshot, frozen_bytes = ArtifactRestoreService(
                    store=self.store,
                    primary_snapshot_dir=self.versions_dir(),
                    trusted_snapshot_dirs=trusted_snapshot_roots(self.data_dir),
                    resolve_live_path=self.restore_live_path,
                ).verified_snapshot_bytes(frozen)
                if frozen_bytes != previous_bytes:
                    raise OSError("previous Artifact snapshot bytes changed")
            except Exception as error:
                previous_file.close()
                previous_file = None
                record_diagnostic(error, surface="artifacts:upload:previous_snapshot")
                # Crash recovery needs this immutable copy if the process dies
                # after live publication but before SQLite commits.  Verify
                # both pre-existing and newly backfilled snapshots before
                # creating any stage or journal; the legacy backfill helper is
                # best-effort and may otherwise swallow the disk error.
                raise ArtifactOperationError(500, "upload staging failed") from error

        # Both stages happen before any row exists, so everything that can fail
        # on the way in fails while nothing is visible: no version, no live
        # file, no event. The old order committed the row first and then wrote
        # the snapshot through a call that swallows `OSError`, which is how a
        # successful-looking upload produced a version no restore could read.
        staged = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
        pending: Path | None = None
        staged_file: _PinnedUploadFile | None = None
        version_stage: _PinnedVersionStage | None = None
        try:
            staged_file = _PinnedUploadFile.create(live_parent, staged)
            staged_file.write(raw)
            staged_file.verified_bytes(
                size_bytes=len(raw),
                checksum=hashlib.sha256(raw).hexdigest(),
            )
            self._fsync_directory(staged.parent, live_parent)
            version_stage = self._stage_version_bytes_pinned(target.name, raw)
            pending = version_stage.path
            live_parent.assert_current()
        except OSError as error:
            if staged_file is not None:
                staged_file.close()
            if previous_file is not None:
                previous_file.close()
            self._remove_upload_path(staged, live_parent)
            if pending is not None:
                if version_stage is not None:
                    version_stage.directory.unlink(pending, missing_ok=True)
                    version_stage.close()
                else:
                    pending.unlink(missing_ok=True)
            record_diagnostic(error, surface="artifacts:upload:stage")
            raise ArtifactOperationError(500, "upload staging failed") from error
        journal_payload: dict[str, Any] | None = None

        def publish(version_id: str, artifact_id: str) -> str:
            nonlocal journal_payload
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", target.name or "artifact")
            if version_stage is None:
                raise OSError("upload snapshot stage descriptor is unavailable")
            final = version_stage.directory.path / f"{version_id}__{safe}"
            journal = version_stage.directory.path / f".upload-{version_id}.json"
            backup = target.with_name(f".{target.name}.upload-{version_id}.backup")
            if previous_file is not None and previous_bytes is not None:
                verified_previous = previous_file.verified_bytes(named_as=target)
                if verified_previous != previous_bytes:
                    raise OSError("exact Artifact target changed before publication")
                previous_live = {
                    "had_live": True,
                    "previous_kind": "regular",
                    "previous_size_bytes": len(previous_bytes),
                    "previous_checksum": hashlib.sha256(previous_bytes).hexdigest(),
                }
            else:
                previous_live = self._describe_upload_live(
                    target,
                    live_parent,
                    reject_aliases=exact_artifact is not None,
                )
            if staged_file is None:
                raise OSError("upload stage descriptor is unavailable")
            if version_stage is None or pending is None:
                raise OSError("upload snapshot stage descriptor is unavailable")
            staged_status = os.fstat(staged_file.fd)
            final_status = os.fstat(version_stage.file.fd)
            raw_payload: dict[str, Any] = {
                "schema_version": 4,
                "artifact_id": artifact_id,
                "version_id": version_id,
                "frame_id": frame_id,
                "workspace_frame_id": workspace_frame_id,
                "previous_version_id": (
                    existing.get("latest_version_id") if existing else None
                ),
                "previous_updated_at": existing.get("updated_at") if existing else None,
                "previous_filename": existing.get("filename") if existing else None,
                "previous_content_type": (
                    existing.get("content_type") if existing else None
                ),
                "target": str(target),
                "staged": str(staged),
                "pending": str(pending),
                "final": str(final),
                "backup": str(backup),
                "target_parent_dev": live_parent.identity[0],
                "target_parent_ino": live_parent.identity[1],
                "published_dev": int(staged_status.st_dev),
                "published_ino": int(staged_status.st_ino),
                "final_dev": int(final_status.st_dev),
                "final_ino": int(final_status.st_ino),
                **previous_live,
                "size_bytes": len(raw),
                "checksum": hashlib.sha256(raw).hexdigest(),
            }
            self._write_upload_journal(journal, raw_payload)
            journal_payload = self._validated_upload_journal(journal, raw_payload)
            if (
                journal_payload["target_parent_dev"],
                journal_payload["target_parent_ino"],
            ) != live_parent.identity:
                raise OSError("upload journal parent identity is inconsistent")
            live_parent.assert_current()
            self._promote_version_stage(
                version_stage,
                final,
                size_bytes=len(raw),
                checksum=hashlib.sha256(raw).hexdigest(),
            )
            staged_file.verified_bytes(
                named_as=staged,
                size_bytes=len(raw),
                checksum=hashlib.sha256(raw).hexdigest(),
            )
            if journal_payload["had_live"]:
                if not self._upload_path_matches_previous(
                    target, journal_payload, live_parent
                ):
                    raise OSError("upload target changed before publication")
                live_parent.replace(target, backup)
                if previous_file is not None:
                    previous_file.verified_bytes(named_as=backup)
            live_parent.replace(staged, target)
            staged_file.verified_bytes(
                named_as=target,
                size_bytes=len(raw),
                checksum=hashlib.sha256(raw).hexdigest(),
            )
            live_parent.assert_current()
            self._fsync_directory(target.parent, live_parent)
            return str(final)

        try:
            if restore_source_version_id is not None:
                if existing is None:
                    raise ArtifactRestoreRefused("artifact restore target not found")
                record = self.store.record_artifact_restore(
                    artifact_id=existing["artifact_id"],
                    source_version_id=restore_source_version_id,
                    expected_latest_version_id=str(
                        existing.get("latest_version_id") or ""
                    ),
                    version_id=f"v-{uuid.uuid4().hex[:12]}",
                    path=str(target),
                    snapshot_path=None,
                    size_bytes=len(raw),
                    checksum=hashlib.sha256(raw).hexdigest(),
                    frame_id=frame_id,
                    root_frame_id=existing.get("root_frame_id"),
                    project_id=existing.get("project_id"),
                    publish=publish,
                )
            elif materialise_source_version_id is not None:
                record = self.store.materialise_artifact_version(
                    source_version_id=materialise_source_version_id,
                    artifact_id=f"a-{uuid.uuid4().hex[:12]}",
                    version_id=f"v-{uuid.uuid4().hex[:12]}",
                    filename=stored_filename,
                    path=str(target),
                    snapshot_path=None,
                    frame_id=frame_id,
                    root_frame_id=str(root_frame_id or ""),
                    project_id=str(project_id or "default"),
                    producing_cell_id=producing_cell_id,
                    publish=publish,
                )
            else:
                record = self.store.commit_artifact_upload(
                    path=str(target),
                    filename=stored_filename,
                    content_type=(
                        (
                            exact_artifact.get("content_type")
                            or self.guess_content_type(stored_filename)
                        )
                        if exact_artifact is not None
                        else self.guess_content_type(target.name)
                    ),
                    size_bytes=len(raw),
                    checksum=hashlib.sha256(raw).hexdigest(),
                    frame_id=frame_id,
                    project_id=project_id,
                    artifact_id=(existing["artifact_id"] if existing else None),
                    expected_previous_version_id=(
                        existing.get("latest_version_id") if existing else None
                    ),
                    expected_previous_updated_at=(
                        existing.get("updated_at") if existing else None
                    ),
                    publish=publish,
                )
        except BaseException as error:
            try:
                if journal_payload is not None:
                    self._abort_upload(
                        journal_payload,
                        live_parent,
                        previous_file=previous_file,
                        previous_bytes=previous_bytes,
                        staged_file=staged_file,
                        version_stage=version_stage,
                    )
                else:
                    self._remove_upload_path(staged, live_parent)
                    if pending is not None:
                        if version_stage is not None:
                            version_stage.directory.unlink(pending, missing_ok=True)
                        else:
                            pending.unlink(missing_ok=True)
            except BaseException as recovery_error:
                record_diagnostic(
                    recovery_error, surface="artifacts:upload:abort_recovery"
                )
                if staged_file is not None:
                    staged_file.close()
                if previous_file is not None:
                    previous_file.close()
                if version_stage is not None:
                    version_stage.close()
                raise
            if staged_file is not None:
                staged_file.close()
            if previous_file is not None:
                previous_file.close()
            if version_stage is not None:
                version_stage.close()
            if not isinstance(error, Exception):
                raise
            if isinstance(error, ArtifactRestoreRefused):
                raise
            if isinstance(error, ArtifactOperationError):
                raise
            record_diagnostic(error, surface="artifacts:upload:commit")
            raise ArtifactOperationError(500, "upload commit failed") from error

        if journal_payload is not None:
            try:
                if staged_file is None:
                    raise OSError("upload stage descriptor is unavailable")
                if version_stage is None:
                    raise OSError("upload snapshot stage descriptor is unavailable")
                staged_file.verified_bytes(
                    named_as=target,
                    size_bytes=len(raw),
                    checksum=hashlib.sha256(raw).hexdigest(),
                )
                if previous_file is not None:
                    previous_file.verified_bytes(named_as=journal_payload["backup"])
                version_stage.file.verified_bytes(
                    named_as=journal_payload["final"],
                    size_bytes=len(raw),
                    checksum=hashlib.sha256(raw).hexdigest(),
                )
                version_stage.directory.assert_current()
                live_parent.assert_current()
            except OSError as error:
                try:
                    self._abort_upload(
                        journal_payload,
                        live_parent,
                        previous_file=previous_file,
                        previous_bytes=previous_bytes,
                        staged_file=staged_file,
                        version_stage=version_stage,
                    )
                finally:
                    if staged_file is not None:
                        staged_file.close()
                    if previous_file is not None:
                        previous_file.close()
                    if version_stage is not None:
                        version_stage.close()
                record_diagnostic(error, surface="artifacts:upload:post_commit_verify")
                raise ArtifactOperationError(
                    500, "upload commit could not be verified"
                ) from error
            try:
                # A cleanup failure is recoverable from the retained journal,
                # but a detached parent is not a successful publication: the
                # committed row would name a pathname that no longer reaches
                # the inode whose bytes were verified above.
                live_parent.assert_current()
                self._remove_new_upload_path(
                    journal_payload["staged"], journal_payload, live_parent
                )
                self._remove_new_upload_path(
                    journal_payload["pending"],
                    journal_payload,
                    version_stage.directory if version_stage is not None else None,
                    pinned=version_stage.file if version_stage is not None else None,
                )
                backup = journal_payload["backup"]
                if self._upload_path_exists(backup, live_parent):
                    if previous_file is not None:
                        previous_file.verified_bytes(named_as=backup)
                    elif not self._upload_path_matches_previous(
                        backup, journal_payload, live_parent
                    ):
                        raise OSError("upload backup changed before cleanup")
                    self._remove_upload_path(backup, live_parent)
                live_parent.assert_current()
                journal_payload["journal"].unlink(missing_ok=True)
                self._fsync_directory(journal_payload["journal"].parent)
            except OSError as error:
                # The committed row, final snapshot, and live bytes are already
                # coherent. Keeping the journal is intentional: startup will
                # verify both copies and finish this idempotent cleanup.
                record_diagnostic(error, surface="artifacts:upload:cleanup")
                try:
                    live_parent.assert_current()
                except OSError:
                    if staged_file is not None:
                        staged_file.close()
                    if previous_file is not None:
                        previous_file.close()
                    if version_stage is not None:
                        version_stage.close()
                    raise ArtifactOperationError(
                        500, "upload commit could not be verified"
                    ) from error
        if staged_file is not None:
            staged_file.close()
        if previous_file is not None:
            previous_file.close()
        if version_stage is not None:
            version_stage.close()
        try:
            self._notify(
                frame_id,
                {
                    "type": "artifact_created",
                    "artifact": {
                        "id": record["artifact_id"],
                        "filename": stored_filename,
                        "content_type": record.get("content_type"),
                        "root_frame_id": frame_id,
                    },
                },
                broadcast,
            )
        except Exception as error:  # projection failure cannot undo a commit
            record_diagnostic(error, surface="artifacts:upload:notification")
        return {
            "artifact_id": record["artifact_id"],
            "id": record["artifact_id"],
            "filename": stored_filename,
        }

    def delete(
        self,
        artifact_id: str,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        with self._upload_lock:
            return self._delete_locked(artifact_id, broadcast=broadcast)

    def _delete_locked(
        self,
        artifact_id: str,
        *,
        broadcast: Broadcast | None = None,
    ) -> dict:
        """Delete an artifact, reclaim unreferenced files, and notify its frame."""
        artifact = self.store.get_artifact(artifact_id)
        try:
            stale_paths = self.store.delete_artifact(artifact_id)
        except ArtifactDeliveryReferenceError as error:
            raise ArtifactOperationError(
                409,
                "Artifact is still referenced by a completion message; "
                "delete the owning session instead",
            ) from error
        root_frame_id = artifact.get("root_frame_id") if artifact else None
        trusted_roots = [self.versions_dir()]
        if root_frame_id:
            trusted_roots.append(self.workspace_for(root_frame_id))
        else:
            trusted_roots.append(self.data_dir / "uploads")
        for path in stale_paths:
            try:
                candidate = Path(os.path.abspath(Path(path).expanduser()))
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=False)
                allowed = False
                for root in trusted_roots:
                    lexical_root = Path(os.path.abspath(root))
                    resolved_root = root.resolve()
                    if (
                        candidate == lexical_root or lexical_root in candidate.parents
                    ) and (
                        resolved == resolved_root or resolved_root in resolved.parents
                    ):
                        allowed = True
                        break
                if not allowed:
                    continue
                candidate.unlink()
            except OSError:
                pass
        self._notify(
            root_frame_id,
            {
                "type": "artifact_created",
                "root_frame_id": root_frame_id,
            },
            broadcast,
        )
        return {"ok": True}

    def snapshot(self, workspace: Path) -> WorkspaceSnapshot:
        """Return kernel-owned file identities for deliverable change detection.

        An mtime alone is caller-controlled: ``os.utime`` and ``copy2`` can
        restore it after replacing bytes. Device/inode/size plus kernel-owned
        ctime detects both replacement and same-length in-place writes while
        keeping the Cell boundary proportional to directory entries rather
        than hashing every potentially multi-gigabyte scientific input.
        """
        try:
            repo_roots = {git_dir.parent for git_dir in workspace.rglob(".git")}
        except OSError:
            repo_roots = set()
        result: WorkspaceSnapshot = {}
        for path in workspace.rglob("*"):
            if _ignored_file(path.relative_to(workspace)):
                continue
            if repo_roots and any(root in path.parents for root in repo_roots):
                continue
            fingerprint = self._live_fingerprint(path)
            if fingerprint is not None:
                result[str(path)] = fingerprint
        return result

    @staticmethod
    def _live_fingerprint(path: Path) -> WorkspaceFileState | None:
        """Identity of the exact regular live file a child already captured."""

        try:
            status = path.stat(follow_symlinks=False)
        except OSError:
            return None
        if not stat.S_ISREG(status.st_mode):
            return None
        return (
            int(status.st_dev),
            int(status.st_ino),
            int(status.st_size),
            int(status.st_mtime_ns),
            int(status.st_ctime_ns),
        )

    @staticmethod
    def _claim_key(path: Path | str) -> str:
        return os.path.abspath(os.fspath(path))

    @staticmethod
    def _claim_workspace_key(workspace: Path | str) -> str:
        return os.path.abspath(os.fspath(workspace))

    def _put_delegated_claim(
        self,
        path: Path,
        *,
        workspace: Path,
        failed: bool,
    ) -> None:
        fingerprint = self._live_fingerprint(path)
        if fingerprint is None:
            return
        key = self._claim_key(path)
        workspace_key = self._claim_workspace_key(workspace)
        with self._delegated_claim_lock:
            claims = self._delegated_claims.setdefault(workspace_key, {})
            if key not in claims and len(claims) >= self._DELEGATED_CLAIM_MAX:
                # Evicting the oldest claim would make a later ancestor sweep
                # assign that child's unchanged bytes to the ancestor. Once
                # exact reconciliation no longer fits, this manager remains
                # fail-closed instead of silently degrading provenance truth.
                self._delegated_claim_overflow.add(workspace_key)
                raise ArtifactOperationError(
                    500, "delegated artifact claim capacity exceeded"
                )
            claims.pop(key, None)
            claims[key] = _DelegatedCaptureClaim(
                fingerprint=fingerprint,
                failed=failed,
            )

    def claim_delegated_artifacts(
        self, artifacts: list[dict], *, workspace: Path
    ) -> None:
        """Exclude unchanged child bytes from every enclosing parent sweep."""

        for artifact in artifacts:
            path = artifact.get("storage_path")
            if path:
                self._put_delegated_claim(
                    Path(str(path)), workspace=workspace, failed=False
                )

    def claim_delegated_changes(
        self,
        workspace: Path,
        before: WorkspaceSnapshot,
        *,
        failed: bool,
    ) -> None:
        """Claim exact changed files after a child capture failure.

        A matching parent sweep must refuse rather than assign those bytes to
        the parent.  If the parent subsequently rewrites a file, its inode/
        size/mtime/ctime fingerprint changes and the stale claim is discarded.
        """

        after = self.snapshot(workspace)
        for raw_path, fingerprint in after.items():
            if before.get(raw_path) != fingerprint:
                self._put_delegated_claim(
                    Path(raw_path), workspace=workspace, failed=failed
                )

    def _matches_delegated_claim(
        self,
        path: Path,
        *,
        workspace: Path,
        consume_success: bool,
    ) -> bool:
        key = self._claim_key(path)
        workspace_key = self._claim_workspace_key(workspace)
        current = self._live_fingerprint(path)
        with self._delegated_claim_lock:
            claims = self._delegated_claims.get(workspace_key, {})
            claim = claims.get(key)
            if claim is not None and current != claim.fingerprint:
                claims.pop(key, None)
            elif claim is not None and not claim.failed and consume_success:
                # A nested child sweep must leave the claim for higher
                # ancestors. The root Web sweep is the terminal consumer; once
                # it skipped these exact bytes the entry can be reclaimed.
                claims.pop(key, None)
            if not claims:
                self._delegated_claims.pop(workspace_key, None)
        if claim is None or current != claim.fingerprint:
            return False
        if claim.failed:
            raise ArtifactOperationError(500, "delegated artifact capture failed")
        return True

    def delegated_cell_hooks(
        self,
        session: ArtifactSession,
        producer_frame_id: str,
        emit: EventSink,
    ) -> DelegatedCellCaptureHooks:
        """Build the Web-owned capture boundary for one delegated Agent."""

        return DelegatedCellCaptureHooks(self, session, producer_frame_id, emit)

    def _prevalidate_receipt_lineage(
        self,
        session: ArtifactSession,
        receipts: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, list[str]]:
        """Validate the complete receipt lineage set before any publication.

        Compute submission already resolves inputs inside the owning session,
        but a durable job row or compatibility manager can outlive that check.
        Revalidate the returned identities against the capture session here so
        a foreign/dangling input on receipt N cannot leave receipts 1..N-1
        published.  The repository repeats this check inside its savepoint to
        close the validation-to-insert race.
        """

        result: dict[str, list[str]] = {}
        for filename, receipt in receipts.items():
            source = receipt.get("source")
            if not isinstance(source, Mapping):
                raise ArtifactOperationError(
                    500, "Host Artifact receipt lineage evidence is invalid"
                )
            input_versions = _remote_receipt_input_versions(source)
            for version_id in input_versions:
                try:
                    metadata = self.store.version_meta(version_id)
                    artifact = (
                        self.store.get_artifact(str(metadata.get("artifact_id") or ""))
                        if isinstance(metadata, Mapping)
                        else None
                    )
                except Exception as error:
                    record_diagnostic(error, surface="artifacts:capture:lineage_scope")
                    raise ArtifactOperationError(
                        500, "Host Artifact receipt lineage evidence is invalid"
                    ) from error
                if (
                    not isinstance(artifact, Mapping)
                    or artifact.get("root_frame_id") != session.root_frame_id
                    or artifact.get("project_id") != session.project_id
                ):
                    raise ArtifactOperationError(
                        500, "Host Artifact receipt lineage evidence is invalid"
                    )
            result[filename] = input_versions
        return result

    def register_file(
        self,
        session: ArtifactSession,
        path: Path,
        cell_id: str | None,
        emit: EventSink,
        env_snapshot_id: str | None = None,
        *,
        producer_frame_id: str | None = None,
        source: Any = None,
        input_version_ids: list[str] | tuple[str, ...] | None = None,
        expected_checksum: str | None = None,
        frozen_snapshot: FrozenCaptureSnapshot | None = None,
    ) -> dict | None:
        """Persist one produced file as a versioned artifact and notify the UI."""
        relative = str(path.relative_to(session.workspace))
        frozen = frozen_snapshot
        if frozen is not None:
            size = frozen.size_bytes
            checksum = frozen.checksum
        elif self.trusted_delivery:
            try:
                frozen = self.freeze_capture_snapshot(relative, path)
            except Exception as error:
                record_diagnostic(error, surface="artifacts:capture:freeze")
                raise ArtifactOperationError(
                    500, "artifact snapshot freeze failed"
                ) from error
            size = frozen.size_bytes
            checksum = frozen.checksum
        else:
            try:
                size = path.stat().st_size
                checksum = self.checksum(path)
            except OSError:
                return None
        if expected_checksum is not None and checksum != expected_checksum:
            if frozen is not None:
                frozen.path.unlink(missing_ok=True)
            raise ArtifactOperationError(
                500, "native Artifact receipt did not match captured bytes"
            )
        record_fields: dict[str, Any] = {
            "path": str(path),
            "filename": relative,
            "content_type": self.guess_content_type(relative),
            "size_bytes": size,
            "checksum": checksum,
            "producing_cell_id": cell_id,
            "frame_id": producer_frame_id or session.root_frame_id,
            "root_frame_id": session.root_frame_id,
            "project_id": session.project_id,
            "env_snapshot_id": env_snapshot_id,
            "source": source,
            "input_version_ids": input_version_ids,
            "preserve_filename": True,
            "preserve_content_type": True,
        }
        if frozen is not None:
            record_fields.update(
                snapshot_path=str(frozen.path),
                reuse_matching_head=True,
            )
        try:
            record = self.store.record_cell_artifact(**record_fields)
        except Exception:
            if frozen is not None:
                frozen.path.unlink(missing_ok=True)
            raise
        display_filename = record.get("filename") or relative
        if frozen is None:
            self.write_version_snapshot(
                record["version_id"], display_filename, src_path=path
            )
        else:
            # A checksum-equal head may already own a verified snapshot.  Its
            # immutable version wins; the new per-capture freeze is then an
            # unreferenced staging file and must not accumulate.
            try:
                persisted = self.store.version_meta(record["version_id"])
            except Exception:  # noqa: BLE001 — keep a possibly referenced file
                persisted = None
            if persisted is not None and persisted.get("snapshot_path") != str(
                frozen.path
            ):
                frozen.path.unlink(missing_ok=True)
        emit(
            {
                "type": "artifact_created",
                "producing_cell_id": cell_id,
                "artifact": {
                    "id": record["artifact_id"],
                    "artifact_id": record["artifact_id"],
                    "version_id": record["version_id"],
                    "filename": display_filename,
                    "content_type": record.get("content_type"),
                    "size_bytes": size,
                    "project_id": session.project_id,
                    "root_frame_id": session.root_frame_id,
                    "producing_cell_id": cell_id,
                },
            }
        )
        try:
            version_number = len(self.store.list_versions(record["artifact_id"]))
        except Exception:  # noqa: BLE001
            version_number = 1
        return {
            "artifact_id": record["artifact_id"],
            "version_id": record["version_id"],
            "version_number": version_number,
            "filename": display_filename,
            "content_type": record.get("content_type"),
            "size_bytes": size,
            "checksum": checksum,
            "storage_path": record.get("path"),
        }

    def promote_cell(
        self,
        session: ArtifactSession,
        cell: dict,
        emit: EventSink,
    ) -> dict | None:
        """Freeze one notebook cell as a self-contained Markdown artifact.

        A cell's *files* are already captured as artifacts when it runs (see
        ``capture``); promotion fixes the analysis *step* itself — its code,
        stdout, and pointers to what it produced — into a shareable, versioned
        document the Files panel manages like any other artifact. The target
        path is derived from the cell id, so re-promoting the same cell rewrites
        the same file and the store versions it in place instead of spawning a
        duplicate.
        """
        cell_id = str(cell.get("producing_cell_id") or "").strip() or None
        index = cell.get("cell_index")
        stem = f"cell-{index}" if index is not None else "cell"
        token = hashlib.sha1((cell_id or stem).encode("utf-8")).hexdigest()[:8]
        relative = Path("promoted") / f"{stem}-{token}.md"
        try:
            _write_confined_text(
                session.workspace,
                relative,
                self._render_cell_markdown(cell, session.workspace),
            )
        except (OSError, ValueError):
            return None
        # _write_confined_text returns a fully-resolved path, but register_file
        # relativizes against the unresolved session.workspace; hand it the
        # unresolved path (same on-disk file) so relative_to() cannot raise when
        # the workspace prefix contains a symlink (e.g. /tmp -> /private/tmp).
        return self.register_file(session, session.workspace / relative, cell_id, emit)

    def _render_cell_markdown(self, cell: dict, workspace: Path) -> str:
        """Render a cell (code + output + produced files) as Markdown."""
        index = cell.get("cell_index")
        language = str(cell.get("language") or cell.get("kernel_id") or "python")
        heading = f"Cell {index}" if index is not None else "Notebook cell"
        source = (cell.get("source") or "").rstrip("\n")
        fence = _md_fence(source)
        lines: list[str] = [f"# {heading}", "", f"{fence}{language}", source, fence]
        stdout = (cell.get("stdout") or "").rstrip("\n")
        if stdout:
            out_fence = _md_fence(stdout)
            lines += ["", "## Output", "", out_fence, stdout, out_fence]
        error = (cell.get("error") or "").rstrip("\n")
        if error:
            err_fence = _md_fence(error)
            lines += ["", "## Error", "", err_fence, error, err_fence]
        figures = [str(fig) for fig in (cell.get("figures") or []) if fig]
        if figures:
            lines += ["", "## Figures", ""]
            lines += [self._render_promoted_figure(workspace, fig) for fig in figures]
        files = [str(name) for name in (cell.get("files_written") or []) if name]
        if files:
            lines += ["", "## Produced files", ""]
            lines += [f"- `{name}`" for name in files]
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_promoted_figure(workspace: Path, figure: str) -> str:
        """Embed a confined raster figure so the Markdown stays shareable."""
        label = Path(figure).name or "figure"
        try:
            root = workspace.expanduser().resolve()
            candidate = (root / figure).resolve(strict=True)
            candidate.relative_to(root)
            media_type = mimetypes.guess_type(candidate.name)[0] or ""
            size = candidate.stat().st_size
            if media_type not in _EMBEDDED_IMAGE_TYPES or not (
                0 < size <= _MAX_EMBEDDED_FIGURE_BYTES
            ):
                raise ValueError("figure is not an embeddable raster image")
            encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
            return f"![{label}](data:{media_type};base64,{encoded})"
        except (OSError, ValueError):
            # Preserve a useful, non-broken pointer when a historical figure is
            # missing, too large, unsupported, or outside the workspace.
            return f"- Figure artifact: `{figure}`"

    def capture(
        self,
        session: ArtifactSession,
        cell_index: int,
        cell_id: str | None,
        before: WorkspaceSnapshot,
        emit: EventSink,
        language: str = "python",
        run_system_cell: Callable[[str], dict] | None = None,
        drain_remote_provenance: Callable[[], Any] | None = None,
        *,
        producer_frame_id: str | None = None,
        honor_delegated_claims: bool = True,
        artifact_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> CaptureResult:
        receipt_map = (
            artifact_receipt_map(artifact_receipts.values())
            if artifact_receipts
            else {}
        )
        if artifact_receipts and set(receipt_map) != {
            str(filename) for filename in artifact_receipts
        }:
            raise ArtifactOperationError(
                500, "Host Artifact receipt filename mapping is invalid"
            )
        figures: list[str] = []
        if language == "python" and run_system_cell is not None:
            try:
                response = run_system_cell(_capture_snippet(cell_index))
                for line in (response.get("stdout") or "").splitlines():
                    if line.startswith("__OSFIGS__"):
                        try:
                            figures = json.loads(line[len("__OSFIGS__") :]) or []
                        except (ValueError, TypeError):
                            figures = []
            except Exception:  # noqa: BLE001 — capture is best-effort
                figures = []
        after = self.snapshot(session.workspace)
        changed = [
            Path(path)
            for path, fingerprint in after.items()
            if before.get(path) != fingerprint
        ]
        if honor_delegated_claims:
            workspace_key = self._claim_workspace_key(session.workspace)
            with self._delegated_claim_lock:
                claim_overflow = workspace_key in self._delegated_claim_overflow
            if changed and claim_overflow:
                raise ArtifactOperationError(
                    500, "delegated artifact claim capacity exceeded"
                )
            changed = [
                path
                for path in changed
                if not self._matches_delegated_claim(
                    path,
                    workspace=session.workspace,
                    consume_success=producer_frame_id is None,
                )
            ]
        changed_by_filename = {
            str(path.relative_to(session.workspace)): path for path in changed
        }
        unmatched = set(receipt_map).difference(changed_by_filename)
        if unmatched:
            # A Host call promised exact bytes which the enclosing Cell then
            # deleted, renamed, or restored to its baseline.  Publishing a
            # successful Cell would silently drop that durable result.  Check
            # the whole set before the first Artifact row/event so a later bad
            # receipt cannot leave a partially published prefix behind.
            raise ArtifactOperationError(
                500, "Host Artifact receipt did not match a changed workspace file"
            )
        receipt_lineage = self._prevalidate_receipt_lineage(session, receipt_map)
        # A receipt is Host-owned exact evidence.  Freeze *every* promised file
        # before publishing the first row/event: otherwise a persistent kernel
        # thread could rewrite receipt N after receipt 1 was committed and turn
        # an exact-evidence failure into a partially published Cell.  This is
        # independent of Stage 1 trusted delivery; Stage 10/11 receipts must
        # never fall back to a mutable live-path snapshot when that flag is off.
        frozen_receipts: dict[str, FrozenCaptureSnapshot] = {}
        try:
            for filename, receipt in receipt_map.items():
                try:
                    frozen = self.freeze_capture_snapshot(
                        filename, changed_by_filename[filename]
                    )
                except Exception as error:
                    record_diagnostic(error, surface="artifacts:capture:freeze")
                    raise ArtifactOperationError(
                        500, "artifact snapshot freeze failed"
                    ) from error
                frozen_receipts[filename] = frozen
                if frozen.checksum.lower() != receipt["checksum"]:
                    raise ArtifactOperationError(
                        500, "native Artifact receipt did not match captured bytes"
                    )
        except BaseException:
            for frozen in frozen_receipts.values():
                frozen.path.unlink(missing_ok=True)
            raise
        figure_set = set(figures)
        files_written: list[str] = []
        artifacts: list[dict] = []
        # `language` and the session's frame id were already in scope here and
        # simply were not passed on, which is why every artifact was stamped
        # with the daemon's Python environment regardless of what ran.
        # Drained on EVERY cell, not only on cells that wrote files. The
        # buffer's own docstring says "drained per cell", and it was not: a
        # cell that ran a remote GPU job and produced no local output left its
        # entry sitting there, and the next cell that happened to write a file
        # was stamped with it. A fold in cell 3 became the provenance of a
        # figure from cell 7 — provenance that is wrong rather than absent,
        # which is the failure this subsystem exists to prevent.
        #
        # `capture_environment` is what performs the drain, so it is called
        # either way; its result is only *kept* when there is an artifact to
        # attach it to. A remote run whose cell produced nothing has no
        # artifact to describe, and discarding it is the honest outcome.
        # Two different concerns, separated because they want opposite answers
        # on a cell that wrote nothing.
        #
        # The DRAIN must happen every cell. The buffer's own docstring says
        # "drained per cell" and it was not: the whole block was gated on the
        # cell having written files, so a cell that ran a remote GPU job and
        # produced no local output left its entry sitting there, and the next
        # cell that happened to write something was stamped with it. A fold in
        # cell 3 became the provenance of a figure from cell 7 — provenance
        # that is wrong rather than absent.
        #
        # The environment FREEZE should not happen on such a cell: it lists
        # packages, and there is no artifact for it to describe. Skipping it
        # was the sound half of the old behaviour and is kept.
        try:
            remote_entries = (
                drain_remote_provenance()
                if drain_remote_provenance is not None
                else None
            )
            env_snapshot_id = (
                self.capture_environment(
                    lambda: remote_entries,
                    root_frame_id=(
                        producer_frame_id or getattr(session, "root_frame_id", None)
                    ),
                    language=language,
                )
                if changed
                else None
            )
            for path in sorted(
                changed,
                key=lambda item: (
                    str(item.relative_to(session.workspace)) not in figure_set,
                    str(item),
                ),
            ):
                relative = str(path.relative_to(session.workspace))
                receipt = receipt_map.get(relative) or {}
                # Passing the snapshot transfers cleanup/DB ownership to
                # register_file.  Pop first so an emitter failure after the DB
                # commit cannot make this outer cleanup unlink a referenced
                # immutable version.
                frozen = (
                    frozen_receipts.pop(relative)
                    if relative in frozen_receipts
                    else None
                )
                metadata = self.register_file(
                    session,
                    path,
                    cell_id,
                    emit,
                    env_snapshot_id=env_snapshot_id,
                    producer_frame_id=producer_frame_id,
                    source=receipt.get("source"),
                    input_version_ids=receipt_lineage.get(relative),
                    expected_checksum=(
                        str(receipt["checksum"]) if receipt.get("checksum") else None
                    ),
                    frozen_snapshot=frozen,
                )
                if metadata is not None:
                    artifacts.append(metadata)
                if relative not in figure_set:
                    files_written.append(relative)
        except BaseException:
            for frozen in frozen_receipts.values():
                frozen.path.unlink(missing_ok=True)
            raise
        return CaptureResult(figures, files_written, artifacts)

    def capture_environment(
        self,
        drain_remote_provenance: Callable[[], Any] | None = None,
        *,
        root_frame_id: str | None = None,
        language: str = "python",
    ) -> str | None:
        """Record the environment of the kernel that produced these files.

        It used to record the *daemon's* — a zero-argument freeze of this
        process, stamped ``kind: "python"`` whatever had actually run. An R
        cell's artifact therefore carried a Python package list, and so did a
        Python cell running in a selected conda environment. Both are the same
        failure: provenance that is wrong rather than absent, presented by the
        UI as the kernel's own.

        The kernel generation is the authority. It knows the runtime, the
        interpreter, and the environment name, and its id ties the artifact to
        one exact kernel lifetime.
        """
        try:
            generation = self._generation_for(root_frame_id, language)
            snapshot = self._snapshot_for(generation, language)
            if drain_remote_provenance is not None:
                remote = drain_remote_provenance()
                if remote:
                    snapshot["remote"] = remote
            return self.store.upsert_env_snapshot(snapshot)
        except Exception:  # noqa: BLE001 — provenance cannot break artifact saving
            return None

    def _generation_for(
        self, root_frame_id: str | None, language: str
    ) -> dict[str, Any] | None:
        """The generation that actually produced these files, on this branch.

        Generations are registered per ``branch_id``, and the repository
        defaults an omitted one to ``root_frame_id`` — the root branch. Omitting
        it here meant a file written by a cell on a *forked* branch was
        attributed to the root branch's most recent kernel, or, if the root had
        none, degraded to the assumed snapshot. Either way the artifact's
        interpreter and package provenance described a kernel that did not
        produce it, which is the failure this whole path exists to prevent.
        """
        if not root_frame_id:
            return None
        latest = getattr(self.store, "latest_kernel_generation", None)
        if latest is None:
            return None
        try:
            active = getattr(self.store, "active_session_branch", None)
            branch_id = active(root_frame_id) if callable(active) else None
            return latest(root_frame_id, language, branch_id=branch_id or None)
        except Exception:  # noqa: BLE001
            return None

    def _snapshot_for(
        self, generation: dict[str, Any] | None, language: str
    ) -> dict[str, Any]:
        """Build the snapshot from what the generation actually says.

        With no generation on record -- a cell that wrote files before any
        kernel was registered, or a store that predates them -- fall back to
        describing this process, but say so, so a reader can tell a measured
        environment from an assumed one.
        """
        from openai4s.kernel import preinstall

        environment = (generation or {}).get("environment")
        environment = environment if isinstance(environment, dict) else {}
        runtime = str(environment.get("runtime") or language or "python").lower()
        interpreter = environment.get("interpreter")

        snapshot: dict[str, Any] = {
            "kind": runtime,
            "interpreter": interpreter,
            "environment_name": environment.get("environment_name"),
            "platform": _pf.platform(),
        }
        if generation:
            snapshot["generation_id"] = generation.get("generation_id")
            snapshot["environment_manifest_id"] = generation.get(
                "environment_manifest_id"
            )
        else:
            snapshot["provenance"] = "assumed: no kernel generation on record"

        if runtime == "python":
            if interpreter:
                packages = self._frozen_packages(interpreter, generation)
            elif generation:
                # A generation *is* on record — legacy, imported, or written
                # before the environment carried an interpreter path. Freezing
                # the daemon here attributed this process's packages to that
                # generation id, which is confidently wrong provenance rather
                # than absent provenance. The daemon may only describe the case
                # where no generation exists at all.
                packages = None
            else:
                packages = preinstall.full_freeze()
            if packages is None:
                # Naming what we could not read beats implying the daemon's
                # packages were this kernel's.
                snapshot["packages"] = []
                snapshot["package_count"] = 0
                snapshot["packages_unavailable"] = (
                    f"could not read distributions from {interpreter!r}"
                    if interpreter
                    else (
                        "this kernel generation records no interpreter, and "
                        "the daemon's packages are not this kernel's"
                    )
                )
            else:
                snapshot["packages"] = packages
                snapshot["package_count"] = len(packages)
            snapshot["python_version"] = (
                _pf.python_version()
                if _same_interpreter(interpreter, bool(generation))
                else None
            )
            snapshot["implementation"] = (
                _pf.python_implementation()
                if _same_interpreter(interpreter, bool(generation))
                else None
            )
        else:
            # A non-Python kernel has no Python package set, and claiming an
            # empty one would read as "nothing installed" rather than "not
            # applicable".
            snapshot["packages"] = []
            snapshot["package_count"] = 0
            snapshot["packages_unavailable"] = (
                f"{runtime} kernel: Python distribution metadata does not apply"
            )
        return snapshot

    def invalidate_freeze_cache(self) -> None:
        """Forget every cached package list.

        The cache is keyed by kernel generation on the premise that an
        environment cannot change within one — which `/kernel/install` breaks:
        installing with ``restart: false`` (or installing successfully and then
        failing to restart) mutates the *same* generation's interpreter. A stale
        entry would then attribute the pre-install package list to artifacts the
        new packages actually produced, which is provenance that is wrong rather
        than absent. The installer calls this so the next capture re-probes.
        """
        with self._freeze_lock:
            self._freeze_cache.clear()

    def _frozen_packages(
        self, interpreter: Any, generation: dict[str, Any] | None
    ) -> list[dict[str, Any]] | None:
        """Freeze a foreign interpreter once per kernel generation.

        ``freeze_for`` launches the target interpreter and enumerates its
        distributions — up to a 20-second wait. Its docstring says callers
        cache per generation because an environment cannot change within one;
        no caller did, so every cell that produced a file paid the full probe
        again. A persistent kernel writing a figure per cell paid it per
        figure.

        A failed probe is cached too: an interpreter that could not be read
        will not become readable within the same generation, and re-paying the
        timeout to rediscover that is the worst version of this.

        Keyed by generation because that is the exact lifetime over which the
        answer is constant. Without one there is nothing bounding the
        environment's stability, so the probe runs.
        """
        from openai4s.kernel import preinstall

        generation_id = str((generation or {}).get("generation_id") or "")
        if not generation_id:
            return preinstall.freeze_for(interpreter)
        key = (generation_id, str(interpreter))
        with self._freeze_lock:
            if key in self._freeze_cache:
                return self._freeze_cache[key]
        packages = preinstall.freeze_for(interpreter)
        with self._freeze_lock:
            # Bounded: one entry per (generation, interpreter), and a
            # generation ends when its kernel does.
            if len(self._freeze_cache) >= self._FREEZE_CACHE_MAX:
                self._freeze_cache.clear()
            self._freeze_cache[key] = packages
        return packages


def _capture_snippet(index: int) -> str:
    return (
        "import json as __oj\n"
        "__osfigs=[]\n"
        "try:\n"
        " import sys as __sys\n"
        " if 'matplotlib' in __sys.modules:\n"
        "  import matplotlib.pyplot as __plt\n"
        "  for __n in list(__plt.get_fignums()):\n"
        f"   __nm='figure_cell{index}_'+str(__n)+'.png'\n"
        "   try:\n"
        "    __plt.figure(__n).savefig(__nm,dpi=130,bbox_inches='tight')\n"
        "    __plt.close(__n); __osfigs.append(__nm)\n"
        "   except Exception: pass\n"
        "except Exception: pass\n"
        "print('__OSFIGS__'+__oj.dumps(__osfigs))\n"
    )


def _ignored_file(path: Path) -> bool:
    parts = path.parts
    if any(part.startswith(".") for part in parts):
        return True
    if any(
        part in _JUNK_DIR_SEGMENTS or part.endswith((".egg-info", ".dist-info"))
        for part in parts
    ):
        return True
    return path.name.endswith((".pyc", ".pyo"))


def is_text_editable(filename: str | None, content_type: str | None) -> bool:
    name = (filename or "").lower()
    content = (content_type or "").lower()
    if content.startswith("image/") or name.endswith(_BINARY_EXT):
        return False
    return (
        name.endswith(_TEXT_EDIT_EXT)
        or content.startswith("text/")
        or any(kind in content for kind in ("json", "csv", "xml", "javascript"))
    )


__all__ = ["ArtifactManager", "ArtifactOperationError", "is_text_editable"]

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.artifact_restore import (
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
EventSink = Callable[[dict[str, Any]], None]
Broadcast = Callable[[str, dict[str, Any]], None]

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
        self._capture(
            token,
            language=language,
            producing_cell_id=producing_cell_id,
        )

    def before_native(self, action: object) -> _DelegatedCaptureToken:
        """Open the same exact boundary for one writing native Tool call."""

        return self.before(action)

    def after_native(
        self,
        _action: object,
        token: _DelegatedCaptureToken,
        _result: object,
    ) -> None:
        """Capture a native write under the child frame, never its parent."""

        self._capture(token, language="native", producing_cell_id=None)

    def _capture(
        self,
        token: _DelegatedCaptureToken,
        *,
        language: str,
        producing_cell_id: str | None,
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
        self._upload_lock = threading.Lock()
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

    def restore_live_path(self, artifact: dict, current: dict) -> Path:
        """Resolve the exact live file while rejecting workspace escapes."""
        root_frame_id = artifact.get("root_frame_id") or "default"
        workspace = self.workspace_for(root_frame_id).expanduser().resolve()
        raw_path = current.get("path") or artifact.get("filename") or ""
        candidate = Path(raw_path)
        target = (
            (candidate if candidate.is_absolute() else workspace / candidate)
            .expanduser()
            .resolve()
        )
        try:
            target.relative_to(workspace)
        except ValueError as error:
            raise PermissionError("artifact live path escapes its workspace") from error
        return target

    def stage_version_bytes(self, filename: str, data: bytes) -> Path:
        """Freeze bytes under a pending name, before any version row exists.

        The strict half of `write_version_snapshot`, and the reason it exists:
        that method swallows `OSError`, so on the upload path a failed snapshot
        left a *committed* version whose `snapshot_path` was NULL and whose
        frozen bytes were nowhere -- and the call still returned success. The
        comment directly above the call said "a committed version must never
        lack the frozen bytes its checksum describes"; the code said otherwise,
        and `ArtifactRestoreService.verified_snapshot_bytes` refuses exactly
        that version, so the upload reported success and produced something no
        restore could ever read.

        Swallowing is right for `protect_latest`, which backfills opportunistically
        and must not fail a turn. It is wrong here, where the write is the thing
        that makes a version legitimate. Writing under a pending name lets the
        caller do it *before* the row is created, so a failure happens while
        nothing is visible rather than after the commit.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
        directory = self.versions_dir()
        directory.mkdir(parents=True, exist_ok=True)
        pending = directory / f".pending-{uuid.uuid4().hex}__{safe}"
        self._write_durable_upload_file(pending, data)
        return pending

    @staticmethod
    def _upload_path_exists(path: Path) -> bool:
        return os.path.lexists(os.fspath(path))

    @staticmethod
    def _remove_upload_path(path: Path) -> None:
        if not ArtifactManager._upload_path_exists(path):
            return
        if path.is_dir() and not path.is_symlink():
            raise OSError("upload transaction path is a directory")
        path.unlink()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_durable_upload_file(path: Path, data: bytes) -> None:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
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
        ArtifactManager._fsync_directory(path.parent)

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
    def _upload_file_matches(path: Path, size_bytes: int, checksum: str) -> bool:
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or status.st_size != size_bytes:
                return False
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            return digest.hexdigest() == checksum
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _describe_upload_live(path: Path) -> dict[str, Any]:
        if not os.path.lexists(os.fspath(path)):
            return {"had_live": False, "previous_kind": "missing"}
        if path.is_symlink():
            return {
                "had_live": True,
                "previous_kind": "symlink",
                "previous_symlink": os.readlink(path),
            }
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise OSError("upload target is not a regular file or symlink")
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
            os.close(descriptor)

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
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
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
        ):
            raise ValueError("invalid upload journal identity")

        data_root = Path(os.path.abspath(self.data_dir))
        versions_root = Path(os.path.abspath(self.data_dir / "artifact-versions"))
        if versions_root.is_symlink() or journal.parent != versions_root:
            raise ValueError("upload journal directory is unsafe")
        paths: dict[str, Path] = {}
        for key in ("target", "staged", "pending", "final", "backup"):
            value = payload.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError("invalid upload journal path")
            candidate = Path(os.path.abspath(value))
            expected_root = versions_root if key in {"pending", "final"} else data_root
            if not candidate.is_relative_to(expected_root):
                raise ValueError("upload journal path escapes data directory")
            paths[key] = candidate
        try:
            relative_parent = paths["target"].parent.relative_to(data_root)
            cursor = data_root
            for component in relative_parent.parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    raise ValueError("upload journal has a symlinked directory")
            if not paths["target"].parent.is_dir():
                raise ValueError("upload target directory is unavailable")
        except (OSError, ValueError) as error:
            raise ValueError("upload target directory is unsafe") from error
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
        frame_id = payload.get("frame_id")
        if frame_id is not None and (not isinstance(frame_id, str) or not frame_id):
            raise ValueError("invalid upload journal frame")
        expected_parent = (
            Path(os.path.abspath(self.workspace_for(frame_id)))
            if frame_id is not None
            else data_root / "uploads"
        )
        if paths["target"].parent != expected_parent:
            raise ValueError("upload journal target does not match its frame")
        return {**payload, **paths, "journal": journal}

    def _upload_path_matches_previous(
        self, path: Path, payload: dict[str, Any]
    ) -> bool:
        kind = payload["previous_kind"]
        if kind == "missing":
            return not self._upload_path_exists(path)
        if kind == "symlink":
            try:
                return (
                    path.is_symlink()
                    and os.readlink(path) == payload["previous_symlink"]
                )
            except OSError:
                return False
        return self._upload_file_matches(
            path,
            payload["previous_size_bytes"],
            payload["previous_checksum"],
        )

    def _remove_new_upload_path(self, path: Path, payload: dict[str, Any]) -> None:
        if not self._upload_path_exists(path):
            return
        if not self._upload_file_matches(
            path, payload["size_bytes"], payload["checksum"]
        ):
            raise OSError("refusing to remove unverified upload transaction bytes")
        self._remove_upload_path(path)

    def _restore_upload_files(self, payload: dict[str, Any]) -> None:
        target = payload["target"]
        backup = payload["backup"]
        had_live = bool(payload["had_live"])
        if self._upload_path_exists(backup):
            if not self._upload_path_matches_previous(backup, payload):
                raise OSError("upload backup does not match the previous live entry")
            self._remove_new_upload_path(target, payload)
            os.replace(backup, target)
            self._fsync_directory(target.parent)
        elif not had_live and self._upload_path_exists(target):
            self._remove_new_upload_path(target, payload)
            self._fsync_directory(target.parent)
        elif had_live and not self._upload_path_matches_previous(target, payload):
            raise OSError("previous upload live entry cannot be recovered")

        for key in ("staged", "pending", "final"):
            self._remove_new_upload_path(payload[key], payload)
        if self._upload_path_exists(backup):
            if not self._upload_path_matches_previous(backup, payload):
                raise OSError("refusing to remove an unverified upload backup")
            self._remove_upload_path(backup)
        payload["journal"].unlink(missing_ok=True)
        self._fsync_directory(payload["journal"].parent)

    def _abort_upload(self, payload: dict[str, Any]) -> None:
        meta = self.store.version_meta(payload["version_id"])
        if isinstance(meta, dict):
            self.store.rollback_artifact_upload(
                artifact_id=payload["artifact_id"],
                version_id=payload["version_id"],
                previous_version_id=payload.get("previous_version_id"),
                previous_updated_at=payload.get("previous_updated_at"),
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
        self._restore_upload_files(payload)

    def _recover_upload_journal(self, journal: Path) -> None:
        payload = self._validated_upload_journal(
            journal, self._read_upload_journal(journal)
        )
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
            target_ok = self._upload_file_matches(
                payload["target"], payload["size_bytes"], payload["checksum"]
            )
            if final_ok and target_ok:
                for key in ("staged", "pending"):
                    self._remove_new_upload_path(payload[key], payload)
                backup = payload["backup"]
                if self._upload_path_exists(backup):
                    if not self._upload_path_matches_previous(backup, payload):
                        raise OSError("upload recovery backup is not exact")
                    self._remove_upload_path(backup)
                journal.unlink(missing_ok=True)
                self._fsync_directory(journal.parent)
                return
        # An uncommitted publish, or a committed row without both promised byte
        # copies, is not a successful upload. Restore the exact previous head
        # and live entry instead of guessing which half should win.
        self._abort_upload(payload)

    def _recover_upload_journals(self) -> None:
        directory = self.data_dir / "artifact-versions"
        if directory.is_symlink():
            raise RuntimeError("artifact upload recovery directory is unsafe")
        if not directory.exists():
            return
        if not directory.is_dir():
            raise RuntimeError("artifact upload recovery directory is unsafe")
        for journal in sorted(directory.glob(".upload-v-*.json")):
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

    def promote_version_bytes(
        self, version_id: str, filename: str, pending: Path
    ) -> Path:
        """Give staged bytes their version-scoped immutable name.

        The upload repository records this path inside the same savepoint that
        creates the version.  Committing from here would split that transaction
        and recreate the half-committed head this helper is meant to prevent.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
        final = self.versions_dir() / f"{version_id}__{safe}"
        os.replace(str(pending), str(final))
        return final

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
        artifact = self.store.get_artifact(artifact_id)
        version = self.store.version_meta(version_id)
        if not artifact or not version or version.get("artifact_id") != artifact_id:
            return {"error": "version not found"}
        try:
            restored = ArtifactRestoreService(
                store=self.store,
                primary_snapshot_dir=self.versions_dir(),
                trusted_snapshot_dirs=trusted_snapshot_roots(self.data_dir),
                resolve_live_path=self.restore_live_path,
            ).restore(
                artifact=artifact,
                source_version_id=version_id,
                frame_id=artifact.get("root_frame_id"),
            )
        except ArtifactRestoreRefused as refusal:
            # Author-written, and the product: "checksum verification failed" is
            # exactly what the user has to be told, and suppressing it to be
            # safe would leave them with a restore that failed for no stated
            # reason.
            return {"error": f"restore failed: {refusal}", "code": "restore_refused"}
        except (KeyError, OSError, RuntimeError, ValueError) as error:
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
                        "version_id": restored["version_id"],
                        "root_frame_id": root_frame_id,
                        "restored_from_version_id": version_id,
                    },
                },
            )
        return {
            "ok": True,
            "artifact": current_artifact,
            "version_id": restored["version_id"],
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
        """Save edited text as a new version without changing its live path."""
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise ArtifactOperationError(404, "artifact not found")
        if not is_text_editable(artifact.get("filename"), artifact.get("content_type")):
            raise ArtifactOperationError(415, "artifact is not text-editable")

        live = self.live_path(artifact)
        current_version_id = artifact.get("latest_version_id")
        current = (
            self.store.version_meta(current_version_id) if current_version_id else None
        )
        try:
            if (
                current
                and not current.get("snapshot_path")
                and current.get("path")
                and Path(current["path"]).resolve() == live.resolve()
                and live.exists()
            ):
                self.write_version_snapshot(
                    current_version_id,
                    artifact["filename"],
                    data=live.read_bytes(),
                )
        except OSError:
            pass

        raw = content.encode("utf-8")
        try:
            live.parent.mkdir(parents=True, exist_ok=True)
            live.write_text(content, encoding="utf-8")
        except OSError as error:
            # Same reason as `restore` above: `strerror` arrives with the
            # absolute path it failed on, and a 500 body is a public surface.
            record_diagnostic(error, surface="artifacts:write")
            raise ArtifactOperationError(500, "write failed") from error

        record = self.store.save_artifact(
            path=str(live),
            filename=artifact["filename"],
            content_type=artifact.get("content_type"),
            size_bytes=len(raw),
            checksum=hashlib.sha256(raw).hexdigest(),
            frame_id=artifact.get("root_frame_id"),
            project_id=artifact.get("project_id"),
            artifact_id=artifact_id,
        )
        self.write_version_snapshot(
            record["version_id"], artifact["filename"], data=raw
        )
        root_frame_id = artifact.get("root_frame_id")
        self._notify(
            root_frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": artifact_id,
                    "filename": artifact["filename"],
                    "version_id": record["version_id"],
                    "root_frame_id": root_frame_id,
                },
            },
            broadcast,
        )
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "version_id": record["version_id"],
            "size_bytes": len(raw),
        }

    def rename(
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

    def _upload_locked(
        self,
        payload: dict,
        *,
        broadcast: Broadcast | None = None,
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
        filename = payload.get("filename") or f"upload-{uuid.uuid4().hex[:8]}"
        frame_id = payload.get("frame_id")
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
        project_id = payload.get("project_id")
        raw = self._upload_bytes(payload)

        workspace = (
            self.workspace_for(frame_id) if frame_id else self.data_dir / "uploads"
        )
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / Path(filename).name
        if (
            self._upload_path_exists(target)
            and target.is_dir()
            and not target.is_symlink()
        ):
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
        existing = self.store.artifact_by_scope_filename(
            target.name,
            root_frame_id=root_frame_id,
            project_id=project_id,
        )

        # Both stages happen before any row exists, so everything that can fail
        # on the way in fails while nothing is visible: no version, no live
        # file, no event. The old order committed the row first and then wrote
        # the snapshot through a call that swallows `OSError`, which is how a
        # successful-looking upload produced a version no restore could read.
        staged = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
        pending: Path | None = None
        try:
            self._write_durable_upload_file(staged, raw)
            pending = self.stage_version_bytes(target.name, raw)
        except OSError as error:
            staged.unlink(missing_ok=True)
            if pending is not None:
                pending.unlink(missing_ok=True)
            record_diagnostic(error, surface="artifacts:upload:stage")
            raise ArtifactOperationError(500, "upload staging failed") from error
        journal_payload: dict[str, Any] | None = None

        def publish(version_id: str, artifact_id: str) -> str:
            nonlocal journal_payload
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", target.name or "artifact")
            final = self.versions_dir() / f"{version_id}__{safe}"
            journal = self.versions_dir() / f".upload-{version_id}.json"
            backup = target.with_name(f".{target.name}.upload-{version_id}.backup")
            previous_live = self._describe_upload_live(target)
            raw_payload: dict[str, Any] = {
                "schema_version": 1,
                "artifact_id": artifact_id,
                "version_id": version_id,
                "frame_id": frame_id,
                "previous_version_id": (
                    existing.get("latest_version_id") if existing else None
                ),
                "previous_updated_at": existing.get("updated_at") if existing else None,
                "target": str(target),
                "staged": str(staged),
                "pending": str(pending),
                "final": str(final),
                "backup": str(backup),
                **previous_live,
                "size_bytes": len(raw),
                "checksum": hashlib.sha256(raw).hexdigest(),
            }
            self._write_upload_journal(journal, raw_payload)
            journal_payload = self._validated_upload_journal(journal, raw_payload)
            promoted = self.promote_version_bytes(version_id, target.name, pending)
            if promoted != final:
                raise OSError("upload snapshot promotion selected an unexpected path")
            if journal_payload["had_live"]:
                if not self._upload_path_matches_previous(target, journal_payload):
                    raise OSError("upload target changed before publication")
                os.replace(str(target), str(backup))
            os.replace(str(staged), str(target))
            self._fsync_directory(target.parent)
            self._fsync_directory(final.parent)
            return str(final)

        try:
            record = self.store.commit_artifact_upload(
                path=str(target),
                filename=target.name,
                content_type=self.guess_content_type(target.name),
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
                    self._abort_upload(journal_payload)
                else:
                    staged.unlink(missing_ok=True)
                    pending.unlink(missing_ok=True)
            except BaseException as recovery_error:
                record_diagnostic(
                    recovery_error, surface="artifacts:upload:abort_recovery"
                )
                raise
            if not isinstance(error, Exception):
                raise
            if isinstance(error, ArtifactOperationError):
                raise
            record_diagnostic(error, surface="artifacts:upload:commit")
            raise ArtifactOperationError(500, "upload commit failed") from error

        if journal_payload is not None:
            try:
                for key in ("staged", "pending"):
                    self._remove_new_upload_path(journal_payload[key], journal_payload)
                backup = journal_payload["backup"]
                if self._upload_path_exists(backup):
                    if not self._upload_path_matches_previous(backup, journal_payload):
                        raise OSError("upload backup changed before cleanup")
                    self._remove_upload_path(backup)
                journal_payload["journal"].unlink(missing_ok=True)
                self._fsync_directory(journal_payload["journal"].parent)
            except OSError as error:
                # The committed row, final snapshot, and live bytes are already
                # coherent. Keeping the journal is intentional: startup will
                # verify both copies and finish this idempotent cleanup.
                record_diagnostic(error, surface="artifacts:upload:cleanup")
        try:
            self._notify(
                frame_id,
                {
                    "type": "artifact_created",
                    "artifact": {
                        "id": record["artifact_id"],
                        "filename": target.name,
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
            "filename": target.name,
        }

    def delete(
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

    def register_file(
        self,
        session: ArtifactSession,
        path: Path,
        cell_id: str | None,
        emit: EventSink,
        env_snapshot_id: str | None = None,
        *,
        producer_frame_id: str | None = None,
    ) -> dict | None:
        """Persist one produced file as a versioned artifact and notify the UI."""
        relative = str(path.relative_to(session.workspace))
        frozen: FrozenCaptureSnapshot | None = None
        if self.trusted_delivery:
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
    ) -> CaptureResult:
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
        remote_entries = (
            drain_remote_provenance() if drain_remote_provenance is not None else None
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
            metadata = self.register_file(
                session,
                path,
                cell_id,
                emit,
                env_snapshot_id=env_snapshot_id,
                producer_frame_id=producer_frame_id,
            )
            if metadata is not None:
                artifacts.append(metadata)
            if relative not in figure_set:
                files_written.append(relative)
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

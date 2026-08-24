"""Shared append-only Artifact restore safety and filesystem transaction.

Both the native control plane and the Web Artifact manager delegate here so a
restore has one meaning everywhere: verified immutable source bytes are copied
to the confined workspace, a fresh version becomes current, and the historical
source row remains untouched.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any, Callable, Protocol


class ArtifactRestoreRefused(RuntimeError):
    """A refusal this module wrote, safe to show whoever asked for the restore.

    The distinction is the same one `server/errors.py` draws between a
    `GatewayError` and an `except Exception`: an author-written message is the
    product and must survive to the caller, while the text of an exception that
    escaped from the OS layer is not the caller's and routinely names an
    absolute path. Both used to arrive at `ArtifactManager.restore` as bare
    `RuntimeError`s, so the caller could either forward every message -- and
    with it the snapshot path, and the account's username -- or suppress every
    message, including "checksum verification failed", which is the one thing
    the user actually needs to be told.

    It subclasses `RuntimeError` rather than `Exception` so nothing that already
    catches this module's refusals has to learn a new type -- including the Host
    dispatcher, whose soft-fail contract turns exactly that into the
    agent-visible error.
    """


class ArtifactRestoreDenied(ArtifactRestoreRefused, PermissionError):
    """A refusal that is specifically about where the bytes live.

    Both bases carry weight. `ArtifactRestoreRefused` is what lets the message
    through to the caller; `PermissionError` is the type callers and tests
    already match on, and it says something the generic refusal does not --
    this snapshot is outside trusted storage, which is a boundary decision
    rather than a corrupt file.
    """


class ArtifactRestoreStore(Protocol):
    """Persistence surface required by :class:`ArtifactRestoreService`."""

    def version_meta(self, version_id: str) -> dict | None: ...

    def set_version_snapshot(self, version_id: str, snapshot_path: str) -> None: ...

    def record_artifact_restore(self, **fields: Any) -> dict: ...


LivePathResolver = Callable[[dict, dict], Path]


def trusted_snapshot_roots(data_dir: Path | str) -> tuple[Path, ...]:
    """Every directory the daemon itself writes immutable snapshots into.

    Derived in one place because the two call sites had drifted into listing
    the same two directories in opposite orders, and neither listed the third.
    Session import writes its snapshots under ``session-imports/<root>/artifacts/``
    and points the version rows at them, so `verified_snapshot_bytes` refused
    every imported artifact with "artifact snapshot is outside trusted
    storage". Two lists maintained by hand is how a directory comes to be
    written to but not readable.

    This is a containment boundary, not the integrity check. The bytes are
    still verified against the version row's recorded sha256 and size on every
    read, so widening the boundary to a directory the daemon owns does not
    weaken what a restore proves -- it stops the daemon refusing to read its
    own storage.
    """
    root = Path(data_dir).expanduser()
    return (
        root / "artifacts",
        root / "artifact-versions",
        root / "session-imports",
    )


class ArtifactRestoreService:
    """Verify immutable restore sources before the exact writer publishes them.

    Filesystem mutation intentionally does not live here.  The old service used
    pathname ``atomic_write`` for both the live file and rollback, creating a
    second, raceable restore implementation beside the Artifact upload journal.
    Callers now feed these verified bytes into that one pinned-parent writer.
    """

    def __init__(
        self,
        *,
        store: ArtifactRestoreStore,
        primary_snapshot_dir: Path,
        trusted_snapshot_dirs: tuple[Path, ...],
        resolve_live_path: LivePathResolver,
    ) -> None:
        self.store = store
        self.primary_snapshot_dir = Path(primary_snapshot_dir).expanduser()
        roots = (self.primary_snapshot_dir, *trusted_snapshot_dirs)
        self.trusted_snapshot_dirs = tuple(
            dict.fromkeys(path.expanduser().resolve() for path in roots)
        )
        self.resolve_live_path = resolve_live_path

    def verified_snapshot_bytes(self, version: dict) -> tuple[Path, bytes]:
        """Read one immutable snapshot only after root, hash, and size checks."""
        raw_path = version.get("snapshot_path")
        if not raw_path:
            raise ArtifactRestoreRefused(
                f"artifact version {version.get('version_id')!r} has no "
                "immutable snapshot"
            )
        try:
            lexical = Path(os.path.abspath(Path(raw_path).expanduser()))
            parent = lexical.parent.resolve(strict=True)
            path = parent / lexical.name
        except OSError as error:
            # The path is deliberately not quoted: it is absolute, under the
            # data directory, and this message is shown to whoever asked for
            # the restore. `version_id` identifies the same row and is theirs.
            raise ArtifactRestoreRefused("artifact snapshot is unavailable") from error
        if not any(parent.is_relative_to(root) for root in self.trusted_snapshot_dirs):
            raise ArtifactRestoreDenied("artifact snapshot is outside trusted storage")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or int(before.st_nlink) != 1:
                raise ArtifactRestoreRefused(
                    "artifact snapshot is not a private regular file"
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
            named = os.stat(path, follow_symlinks=False)
            before_state = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_nlink,
            )
            after_state = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_nlink,
            )
            named_state = (
                named.st_dev,
                named.st_ino,
                named.st_size,
                named.st_mtime_ns,
                named.st_ctime_ns,
                named.st_nlink,
            )
            data = b"".join(chunks)
            if (
                before_state != after_state
                or not os.path.samestat(after, named)
                or after_state != named_state
                or len(data) != int(after.st_size)
            ):
                raise ArtifactRestoreRefused(
                    "artifact snapshot changed while it was verified"
                )
        except ArtifactRestoreRefused:
            raise
        except OSError as error:
            raise ArtifactRestoreRefused("artifact snapshot is unavailable") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        expected_checksum = str(version.get("checksum") or "")
        if not expected_checksum:
            raise ArtifactRestoreRefused("artifact snapshot has no recorded checksum")
        actual_checksum = hashlib.sha256(data).hexdigest()
        if actual_checksum != expected_checksum:
            raise ArtifactRestoreRefused(
                "artifact snapshot checksum verification failed"
            )
        expected_size = version.get("size_bytes")
        if expected_size is not None and len(data) != int(expected_size):
            raise ArtifactRestoreRefused("artifact snapshot size verification failed")
        return path, data


__all__ = ["ArtifactRestoreService", "ArtifactRestoreStore"]

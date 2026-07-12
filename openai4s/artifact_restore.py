"""Shared append-only Artifact restore safety and filesystem transaction.

Both the native control plane and the Web Artifact manager delegate here so a
restore has one meaning everywhere: verified immutable source bytes are copied
to the confined workspace, a fresh version becomes current, and the historical
source row remains untouched.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol


class ArtifactRestoreStore(Protocol):
    """Persistence surface required by :class:`ArtifactRestoreService`."""

    def version_meta(self, version_id: str) -> dict | None:
        ...

    def set_version_snapshot(self, version_id: str, snapshot_path: str) -> None:
        ...

    def record_artifact_restore(self, **fields: Any) -> dict:
        ...


LivePathResolver = Callable[[dict, dict], Path]


class ArtifactRestoreService:
    """Restore immutable bytes through one append-only safety contract."""

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

    @staticmethod
    def atomic_write(path: Path, data: bytes) -> None:
        """Write bytes through a same-directory temporary and atomic replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def verified_snapshot_bytes(self, version: dict) -> tuple[Path, bytes]:
        """Read one immutable snapshot only after root, hash, and size checks."""
        raw_path = version.get("snapshot_path")
        if not raw_path:
            raise RuntimeError(
                f"artifact version {version.get('version_id')!r} has no "
                "immutable snapshot"
            )
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
        except OSError as error:
            raise RuntimeError(
                f"artifact snapshot is unavailable: {raw_path!r}"
            ) from error
        if not any(path.is_relative_to(root) for root in self.trusted_snapshot_dirs):
            raise PermissionError("artifact snapshot is outside trusted storage")
        if not path.is_file():
            raise RuntimeError("artifact snapshot is not a regular file")
        data = path.read_bytes()
        expected_checksum = str(version.get("checksum") or "")
        if not expected_checksum:
            raise RuntimeError("artifact snapshot has no recorded checksum")
        actual_checksum = hashlib.sha256(data).hexdigest()
        if actual_checksum != expected_checksum:
            raise RuntimeError("artifact snapshot checksum verification failed")
        expected_size = version.get("size_bytes")
        if expected_size is not None and len(data) != int(expected_size):
            raise RuntimeError("artifact snapshot size verification failed")
        return path, data

    def _protect_current_version(
        self,
        current: dict,
        live: Path,
    ) -> tuple[bool, bytes | None]:
        """Reject workspace drift and freeze the current head before overwrite."""
        live_exists = live.exists()
        if live_exists and not live.is_file():
            raise RuntimeError("artifact workspace target is not a regular file")
        live_data = live.read_bytes() if live_exists else None
        expected_checksum = str(current.get("checksum") or "")
        if not expected_checksum:
            raise RuntimeError("current artifact version has no recorded checksum")
        if live_data is not None:
            if hashlib.sha256(live_data).hexdigest() != expected_checksum:
                raise RuntimeError(
                    "workspace file has unversioned changes; save them before restore"
                )
            expected_size = current.get("size_bytes")
            if expected_size is not None and len(live_data) != int(expected_size):
                raise RuntimeError(
                    "workspace file size no longer matches current version"
                )

        if current.get("snapshot_path"):
            self.verified_snapshot_bytes(current)
        else:
            if live_data is None:
                raise RuntimeError(
                    "current artifact bytes are unavailable; restore would lose history"
                )
            self.primary_snapshot_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(
                r"[^A-Za-z0-9._-]+",
                "_",
                str(current.get("filename") or "artifact"),
            )
            snapshot = self.primary_snapshot_dir / (f"{current['version_id']}__{safe}")
            if snapshot.exists():
                raise RuntimeError("refusing to overwrite an existing snapshot")
            self.atomic_write(snapshot, live_data)
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != expected_checksum:
                snapshot.unlink(missing_ok=True)
                raise RuntimeError("failed to verify the protected current snapshot")
            try:
                self.store.set_version_snapshot(current["version_id"], str(snapshot))
            except Exception:
                snapshot.unlink(missing_ok=True)
                raise
        return live_exists, live_data

    def restore(
        self,
        *,
        artifact: dict,
        source_version_id: str,
        frame_id: str | None,
    ) -> dict:
        """Copy a historical snapshot into a fresh immutable Artifact version."""
        artifact_id = str(artifact.get("artifact_id") or "")
        current_version_id = str(artifact.get("latest_version_id") or "")
        if source_version_id == current_version_id:
            raise ValueError("restore requires a historical, non-current version")
        source = self.store.version_meta(source_version_id)
        if source is None or source.get("artifact_id") != artifact_id:
            raise KeyError(
                f"version {source_version_id!r} does not belong to artifact "
                f"{artifact_id!r}"
            )
        _source_path, source_data = self.verified_snapshot_bytes(source)

        current = self.store.version_meta(current_version_id)
        if current is None or current.get("artifact_id") != artifact_id:
            raise RuntimeError("artifact latest-version metadata is inconsistent")
        live = Path(self.resolve_live_path(artifact, current)).expanduser().resolve()
        live_existed, previous_data = self._protect_current_version(current, live)

        new_version_id = f"v-{uuid.uuid4().hex[:12]}"
        safe_filename = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            str(source.get("filename") or artifact.get("filename") or "artifact"),
        )
        self.primary_snapshot_dir.mkdir(parents=True, exist_ok=True)
        new_snapshot = self.primary_snapshot_dir / (
            f"{new_version_id}__{safe_filename}"
        )
        if new_snapshot.exists():
            raise RuntimeError("refusing to overwrite an existing snapshot")
        self.atomic_write(new_snapshot, source_data)
        checksum = hashlib.sha256(source_data).hexdigest()
        if hashlib.sha256(new_snapshot.read_bytes()).hexdigest() != checksum:
            new_snapshot.unlink(missing_ok=True)
            raise RuntimeError("restored snapshot checksum verification failed")

        try:
            self.atomic_write(live, source_data)
            record = self.store.record_artifact_restore(
                artifact_id=artifact_id,
                source_version_id=source_version_id,
                expected_latest_version_id=current_version_id,
                version_id=new_version_id,
                path=str(live),
                snapshot_path=str(new_snapshot),
                size_bytes=len(source_data),
                checksum=checksum,
                frame_id=frame_id,
                root_frame_id=artifact.get("root_frame_id"),
                project_id=artifact.get("project_id"),
            )
        except Exception as error:
            new_snapshot.unlink(missing_ok=True)
            try:
                if live_existed and previous_data is not None:
                    self.atomic_write(live, previous_data)
                else:
                    live.unlink(missing_ok=True)
            except OSError as rollback_error:
                raise RuntimeError(
                    "artifact restore failed and workspace rollback failed: "
                    f"{rollback_error}"
                ) from error
            raise
        return {
            "ok": True,
            **record,
            "snapshot_verified": True,
        }


__all__ = ["ArtifactRestoreService", "ArtifactRestoreStore"]

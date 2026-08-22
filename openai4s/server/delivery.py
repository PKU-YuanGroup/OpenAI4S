"""Verified manifests for recoverable completion delivery.

This service sits between Artifact capture and message persistence.  It accepts
only exact version ids, verifies the immutable snapshot bytes against their
durable metadata and ownership scope, and returns a path-free manifest suitable
for :class:`openai4s.storage.delivery.CompletionDeliveryRepository`.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openai4s.artifact_restore import trusted_snapshot_roots
from openai4s.server.urls import artifact_version_url
from openai4s.storage.delivery import canonical_json

_SHA256 = re.compile(r"[0-9a-f]{64}")


class CompletionDeliveryStore(Protocol):
    def version_meta(self, version_id: str) -> dict | None: ...

    def get_artifact(self, artifact_id: str) -> dict | None: ...

    def commit_completion_delivery(self, **fields: Any) -> dict[str, Any]: ...

    def promote_candidate_delivery(self, **fields: Any) -> dict[str, Any]: ...

    def get_completion_delivery(self, delivery_id: str) -> dict[str, Any] | None: ...


class DeliveryValidationError(RuntimeError):
    """A version cannot safely be claimed in a completion message."""


@dataclass(frozen=True)
class VerifiedDeliveryManifest:
    """A canonical manifest and the digest committed beside its message."""

    value: dict[str, Any]
    sha256: str


class CompletionDeliveryService:
    """Build exact-version manifests after verifying persisted snapshot bytes."""

    def __init__(
        self,
        *,
        store: CompletionDeliveryStore,
        data_dir: Path | str,
        snapshot_roots: Iterable[Path | str] | None = None,
    ) -> None:
        self.store = store
        roots = (
            tuple(Path(path).expanduser() for path in snapshot_roots)
            if snapshot_roots is not None
            else trusted_snapshot_roots(data_dir)
        )
        self.snapshot_roots = tuple(dict.fromkeys(path.resolve() for path in roots))
        if not self.snapshot_roots:
            raise ValueError("completion delivery needs a trusted snapshot root")

    def build_manifest(
        self,
        *,
        root_frame_id: str,
        project_id: str,
        versions: Iterable[str | Mapping[str, Any]],
    ) -> VerifiedDeliveryManifest:
        """Verify ``versions`` and return their canonical delivery manifest.

        A mapping may carry ``version_id`` or ``latest_version_id`` so callers
        can pass the Artifact rows already used by completion projection.  No
        Artifact-id or filename fallback exists here.
        """
        root = self._required_text("root_frame_id", root_frame_id)
        project = self._required_text("project_id", project_id)
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in versions:
            version_id = self._version_id(candidate)
            if version_id in seen:
                continue
            seen.add(version_id)
            entries.append(
                self._verified_entry(version_id, root_frame_id=root, project_id=project)
            )
        if not entries:
            raise DeliveryValidationError(
                "completion delivery has no exact Artifact versions"
            )
        value: dict[str, Any] = {
            "schema_version": 1,
            "root_frame_id": root,
            "project_id": project,
            "artifacts": entries,
        }
        encoded = canonical_json(value).encode("utf-8")
        return VerifiedDeliveryManifest(
            value=value,
            sha256=hashlib.sha256(encoded).hexdigest(),
        )

    def commit_verified_manifest(
        self,
        *,
        verified: VerifiedDeliveryManifest,
        idempotency_key: str,
        root_frame_id: str,
        branch_id: str | None,
        frame_id: str | None,
        content: str,
        message_metadata: Mapping[str, Any] | None = None,
        created_at: int | None = None,
    ) -> dict[str, Any]:
        """Commit only while every manifest snapshot still matches its bytes.

        The repository invokes ``snapshot_verifier`` after opening its write
        transaction and after resolving the exact version row.  That closes
        the scheduling gap between :meth:`build_manifest` and final-message
        persistence without putting filesystem policy into the Store facade.
        """
        return self.store.commit_completion_delivery(
            idempotency_key=idempotency_key,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            frame_id=frame_id,
            content=content,
            manifest=verified.value,
            message_metadata=message_metadata,
            expected_manifest_sha256=verified.sha256,
            created_at=created_at,
            snapshot_verifier=self.verify_snapshot,
        )

    def promote_candidate_delivery(
        self,
        *,
        delivery_id: str,
        message_id: str,
        root_frame_id: str,
        branch_id: str | None,
        frame_id: str | None,
        expected_content: str,
        content: str,
        message_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Promote an exact committed candidate before socket publication."""

        return self.store.promote_candidate_delivery(
            delivery_id=delivery_id,
            message_id=message_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            frame_id=frame_id,
            expected_content=expected_content,
            content=content,
            message_metadata=message_metadata,
        )

    def assert_review_matches_delivery(
        self,
        *,
        delivery_id: str,
        reviewed_snapshot: Mapping[str, Any],
        promoted_content: str,
    ) -> None:
        """Require Stage 5's final evidence to match the committed manifest.

        Candidate delivery is intentionally committed before review. A repair
        may change prose, but publishing that old manifest is safe only when
        the final re-review still names the same immutable Artifact versions
        and the promoted bytes still contain their server-authored URLs.
        """

        delivery = self.store.get_completion_delivery(str(delivery_id))
        if not isinstance(delivery, Mapping) or delivery.get("status") != "committed":
            raise DeliveryValidationError(
                "candidate completion delivery is not committed"
            )
        manifest = delivery.get("manifest")
        manifest = manifest if isinstance(manifest, Mapping) else {}
        manifest_rows = manifest.get("artifacts")
        snapshot_rows = reviewed_snapshot.get("artifacts")
        if not isinstance(manifest_rows, list) or not isinstance(snapshot_rows, list):
            raise DeliveryValidationError(
                "reviewed completion Artifact set is unavailable"
            )

        def manifest_identity(row: Mapping[str, Any]) -> tuple[str, str, int, str]:
            try:
                size = int(row.get("size_bytes"))
            except (TypeError, ValueError) as error:
                raise DeliveryValidationError(
                    "completion manifest Artifact size is invalid"
                ) from error
            return (
                str(row.get("artifact_id") or ""),
                str(row.get("version_id") or ""),
                size,
                str(row.get("sha256") or ""),
            )

        def review_identity(row: Mapping[str, Any]) -> tuple[str, str, int, str]:
            try:
                size = int(row.get("size_bytes"))
            except (TypeError, ValueError) as error:
                raise DeliveryValidationError(
                    "reviewed Artifact size is invalid"
                ) from error
            return (
                str(row.get("artifact_id") or ""),
                str(row.get("version_id") or ""),
                size,
                str(row.get("checksum") or ""),
            )

        expected = {
            manifest_identity(row) for row in manifest_rows if isinstance(row, Mapping)
        }
        reviewed = {
            review_identity(row) for row in snapshot_rows if isinstance(row, Mapping)
        }
        if (
            len(expected) != len(manifest_rows)
            or len(reviewed) != len(snapshot_rows)
            or expected != reviewed
        ):
            raise DeliveryValidationError(
                "reviewed Artifact set differs from the completion manifest"
            )
        for row in manifest_rows:
            assert isinstance(row, Mapping)
            version_id = str(row.get("version_id") or "")
            url = str(row.get("url") or "")
            if url != artifact_version_url(version_id) or url not in promoted_content:
                raise DeliveryValidationError(
                    "promoted answer does not carry its committed Artifact URL"
                )

    def _verified_entry(
        self, version_id: str, *, root_frame_id: str, project_id: str
    ) -> dict[str, Any]:
        version = self.store.version_meta(version_id)
        if not isinstance(version, dict):
            raise DeliveryValidationError("completion Artifact version is unavailable")
        if version.get("version_id") != version_id:
            raise DeliveryValidationError(
                "completion Artifact version identity changed"
            )
        artifact_id = version.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise DeliveryValidationError("completion Artifact ownership is missing")
        artifact = self.store.get_artifact(artifact_id)
        if not isinstance(artifact, dict):
            raise DeliveryValidationError("completion Artifact is unavailable")
        if artifact.get("artifact_id") not in (None, artifact_id):
            raise DeliveryValidationError("completion Artifact identity changed")
        if artifact.get("root_frame_id") != root_frame_id:
            raise DeliveryValidationError(
                "completion Artifact belongs to a different session"
            )
        if artifact.get("project_id") != project_id:
            raise DeliveryValidationError(
                "completion Artifact belongs to a different project"
            )

        actual_size, actual_checksum = self.verify_snapshot(version)

        filename = version.get("filename") or artifact.get("filename") or "artifact"
        entry: dict[str, Any] = {
            "artifact_id": artifact_id,
            "version_id": version_id,
            "filename": str(filename),
            "content_type": str(
                version.get("content_type") or artifact.get("content_type") or ""
            ),
            "size_bytes": actual_size,
            "sha256": actual_checksum,
            "url": artifact_version_url(version_id),
        }
        # Producer identity belongs to a capture observation, not necessarily
        # to the immutable version row: checksum-equal bytes may have been
        # produced again by another Cell.  The delivery manifest binds bytes
        # and URL only; per-capture producer/lineage truth is projected from
        # ``artifact_capture_observations``.
        return entry

    def verify_snapshot(self, version: Mapping[str, Any]) -> tuple[int, str]:
        """Verify one version row against its trusted snapshot bytes."""
        actual_size, actual_checksum, _body = self._verified_snapshot(
            version,
            capture_bytes=False,
        )
        return actual_size, actual_checksum

    def read_verified_snapshot(self, version: Mapping[str, Any]) -> bytes:
        """Return the exact bytes that passed the version row's identity check."""
        _actual_size, _actual_checksum, body = self._verified_snapshot(
            version,
            capture_bytes=True,
        )
        if body is None:  # pragma: no cover - capture_bytes=True is authoritative
            raise DeliveryValidationError("completion Artifact snapshot is unavailable")
        return body

    def _verified_snapshot(
        self,
        version: Mapping[str, Any],
        *,
        capture_bytes: bool,
    ) -> tuple[int, str, bytes | None]:
        expected_checksum = version.get("checksum")
        if not isinstance(expected_checksum, str) or not _SHA256.fullmatch(
            expected_checksum
        ):
            raise DeliveryValidationError(
                "completion Artifact has no valid recorded checksum"
            )
        expected_size = version.get("size_bytes")
        if (
            isinstance(expected_size, bool)
            or not isinstance(expected_size, int)
            or expected_size < 0
        ):
            raise DeliveryValidationError(
                "completion Artifact has no valid recorded size"
            )
        actual_size, actual_checksum, body = self._read_snapshot(
            version,
            capture_bytes=capture_bytes,
        )
        if actual_size != expected_size:
            raise DeliveryValidationError(
                "completion Artifact size verification failed"
            )
        if actual_checksum != expected_checksum:
            raise DeliveryValidationError(
                "completion Artifact checksum verification failed"
            )
        return actual_size, actual_checksum, body

    def _read_snapshot(
        self,
        version: Mapping[str, Any],
        *,
        capture_bytes: bool,
    ) -> tuple[int, str, bytes | None]:
        raw_path = version.get("snapshot_path")
        if not isinstance(raw_path, str) or not raw_path:
            raise DeliveryValidationError(
                "completion Artifact has no immutable snapshot"
            )
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
        except OSError as error:
            raise DeliveryValidationError(
                "completion Artifact snapshot is unavailable"
            ) from error
        if not any(path.is_relative_to(root) for root in self.snapshot_roots):
            raise DeliveryValidationError(
                "completion Artifact snapshot is outside trusted storage"
            )

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise DeliveryValidationError(
                    "completion Artifact snapshot is not a regular file"
                )
            digest = hashlib.sha256()
            size = 0
            chunks: list[bytes] | None = [] if capture_bytes else None
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or size != after.st_size
            ):
                raise DeliveryValidationError(
                    "completion Artifact snapshot changed during verification"
                )
            return (
                size,
                digest.hexdigest(),
                b"".join(chunks) if chunks is not None else None,
            )
        except DeliveryValidationError:
            raise
        except OSError as error:
            raise DeliveryValidationError(
                "completion Artifact snapshot is unavailable"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _version_id(candidate: str | Mapping[str, Any]) -> str:
        if isinstance(candidate, str):
            value: Any = candidate
        elif isinstance(candidate, Mapping):
            value = candidate.get("version_id") or candidate.get("latest_version_id")
        else:
            value = None
        if not isinstance(value, str) or not value:
            raise DeliveryValidationError(
                "completion Artifact is missing an exact version id"
            )
        return value

    @staticmethod
    def _required_text(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value


__all__ = [
    "CompletionDeliveryService",
    "CompletionDeliveryStore",
    "DeliveryValidationError",
    "VerifiedDeliveryManifest",
]

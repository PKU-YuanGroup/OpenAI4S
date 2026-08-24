"""Stage 11 durable remote-compute product hooks.

The manager already claims a job row before submit and never resubmits on
reconcile. This module is the opt-in product layer: first-access recovery
projection and harvest provenance that names the remote environment, input
versions, job receipt, and checksums.  The manager remains lazy; constructing
it for the first remote-compute call rehydrates durable jobs and never
resubmits them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any


def official_stage11_enabled(config: Any) -> bool:
    flags = getattr(config, "roadmap_features", None)
    return bool(
        flags is not None and getattr(flags, "stage11_durable_remote_compute", False)
    )


def _input_versions(value: Any) -> list[str]:
    """Return the exact ordered input identities carried by one job result."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("remote-compute input-version evidence is malformed")
    versions: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError("remote-compute input-version evidence is malformed")
        if item not in versions:
            versions.append(item)
    return versions


def harvest_source(
    job: Mapping[str, Any],
    *,
    checksums: Mapping[str, str] | None = None,
    input_versions: list[str] | None = None,
) -> dict[str, Any]:
    versions = _input_versions(
        input_versions if input_versions is not None else job.get("input_versions")
    )
    return {
        "kind": "remote_compute",
        "job_id": job.get("job_id"),
        "receipt": job.get("receipt") or job.get("sandbox_id") or job.get("pid"),
        "provider": job.get("provider"),
        "remote_environment": job.get("remote_environment")
        or job.get("alias")
        or job.get("provider"),
        "input_versions": versions,
        "checksums": dict(checksums or {}),
    }


def harvest_artifact_receipts(
    result: Any,
    *,
    workspace: str | Path,
) -> list[dict[str, Any]]:
    """Bind a verified manager manifest to exact workspace output paths.

    The manager builds ``artifact_manifest`` in a host-owned staging directory
    before publishing the tree into the Cell workspace. A receipt may trust
    that manifest, but never a guessed filename or a checksum recomputed from
    mutable live bytes. Running jobs and compatibility results without a full
    durable identity truthfully produce no receipts; malformed claimed harvest
    evidence is rejected instead of being partially attributed.
    """

    payload = result[0] if isinstance(result, tuple) else result
    if not isinstance(payload, Mapping):
        return []
    if payload.get("cached") is True:
        # A terminal re-poll names the already-published harvest; it did not
        # write this action's bytes. Reissuing its old receipt would either
        # create a duplicate Artifact event or make a no-change native capture
        # fail because there is nothing new to bind.
        return []
    output_files = payload.get("output_files")
    manifest = payload.get("artifact_manifest")
    if not output_files and not manifest:
        return []
    if not isinstance(output_files, list) or not isinstance(manifest, list):
        raise ValueError("remote-compute harvest evidence is malformed")
    if len(output_files) != len(manifest):
        raise ValueError("remote-compute harvest evidence is incomplete")
    identity = {
        "job_id": payload.get("job_id"),
        "provider": payload.get("provider"),
        "receipt": payload.get("receipt"),
    }
    if not all(identity.values()):
        # Compatibility managers predating Stage 11 can still harvest files,
        # but they cannot truthfully claim the new provenance contract.
        return []
    input_versions = _input_versions(payload.get("input_versions"))

    root = Path(workspace).expanduser().resolve()
    receipts: list[dict[str, Any]] = []
    for output_file, raw_entry in zip(output_files, manifest):
        if not isinstance(output_file, str) or not isinstance(raw_entry, Mapping):
            raise ValueError("remote-compute harvest evidence is malformed")
        manifest_path = raw_entry.get("path")
        checksum = raw_entry.get("sha256")
        if not isinstance(manifest_path, str) or not manifest_path:
            raise ValueError("remote-compute harvest path is invalid")
        parts = PurePosixPath(manifest_path)
        if parts.is_absolute() or ".." in parts.parts or "." in parts.parts:
            raise ValueError("remote-compute harvest path is invalid")
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in checksum)
        ):
            raise ValueError("remote-compute harvest checksum is invalid")
        output_path = Path(output_file).expanduser().resolve()
        try:
            relative = output_path.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError("remote-compute harvest escaped the workspace") from error
        relative_parts = PurePosixPath(relative).parts
        if len(relative_parts) < len(parts.parts) or tuple(
            relative_parts[-len(parts.parts) :]
        ) != tuple(parts.parts):
            raise ValueError("remote-compute manifest does not name its output")
        normalized_checksum = checksum.lower()
        source = harvest_source(
            {**identity, "remote_environment": payload.get("remote_environment")},
            checksums={relative: normalized_checksum},
            input_versions=input_versions,
        )
        receipts.append(
            {
                "filename": relative,
                "checksum": normalized_checksum,
                "source": source,
            }
        )
    return receipts

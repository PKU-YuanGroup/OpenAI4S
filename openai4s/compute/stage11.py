"""Stage 11 durable remote-compute product hooks.

The manager already claims a job row before submit and never resubmits on
reconcile. This module is the opt-in product layer: boot-time reconcile and
harvest provenance that names the remote environment, input versions, job
receipt, and checksums.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def official_stage11_enabled(config: Any) -> bool:
    flags = getattr(config, "roadmap_features", None)
    return bool(
        flags is not None and getattr(flags, "stage11_durable_remote_compute", False)
    )


def harvest_source(
    job: Mapping[str, Any],
    *,
    checksums: Mapping[str, str] | None = None,
    input_versions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "remote_compute",
        "job_id": job.get("job_id"),
        "receipt": job.get("receipt") or job.get("sandbox_id") or job.get("pid"),
        "provider": job.get("provider"),
        "remote_environment": job.get("remote_environment")
        or job.get("alias")
        or job.get("provider"),
        "input_versions": list(input_versions or job.get("input_versions") or []),
        "checksums": dict(checksums or {}),
    }


def stamp_harvest_artifacts(
    store: Any,
    artifacts: list[Mapping[str, Any]],
    result: Any,
    *,
    job: Mapping[str, Any] | None = None,
) -> int:
    """Attach remote-compute provenance to newly captured harvest versions."""

    payload = result[0] if isinstance(result, tuple) else result
    if not isinstance(payload, Mapping):
        payload = {}
    record = dict(job or {})
    record.setdefault("job_id", payload.get("job_id"))
    record.setdefault("receipt", payload.get("receipt") or payload.get("sandbox_id"))
    record.setdefault("provider", payload.get("provider"))
    if not record.get("job_id"):
        return 0
    stamped = 0
    for item in artifacts:
        version_id = item.get("version_id") or item.get("latest_version_id")
        if not version_id:
            continue
        checksum = item.get("checksum") or item.get("sha256")
        source = harvest_source(
            record,
            checksums={str(item.get("filename") or version_id): str(checksum or "")},
            input_versions=list(payload.get("input_versions") or []),
        )
        store.set_version_source(version_id, source)
        stamped += 1
    return stamped

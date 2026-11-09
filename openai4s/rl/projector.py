"""Project durable Artifact and Compute facts into a Science Trajectory.

The Store repositories remain the facts' owners.  This module only reads the
exact immutable IDs already present in RL execution-event payloads and emits
bounded ``ExecutionEvent`` references for the trainer-facing envelope.  It
never follows an Artifact head, copies a raw row, or invents a cost.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any, Iterable, Mapping

from .models import Episode, ExecutionEvent


def _json_column(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return default
    return decoded


def _version_ids(value: Any) -> tuple[str, ...]:
    """Find only explicit Artifact-version fields in an event payload."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key in (
            "version_id",
            "artifact_version_id",
            "input_version_id",
            "output_version_id",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                found.append(candidate)
        for key in ("input_versions", "output_versions", "artifact_version_ids"):
            candidates = value.get(key)
            if isinstance(candidates, (list, tuple)):
                found.extend(
                    item
                    for item in candidates
                    if isinstance(item, str) and item.strip()
                )
        for child in value.values():
            found.extend(_version_ids(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_version_ids(child))
    return tuple(dict.fromkeys(found))


def _job_ids(value: Any) -> tuple[str, ...]:
    """Find durable Compute job identities without treating arbitrary IDs as jobs."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key in ("job_id", "compute_job_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                found.append(candidate)
        for key in ("job_ids", "compute_job_ids"):
            candidates = value.get(key)
            if isinstance(candidates, (list, tuple)):
                found.extend(
                    item
                    for item in candidates
                    if isinstance(item, str) and item.strip()
                )
        for child in value.values():
            found.extend(_job_ids(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_job_ids(child))
    return tuple(dict.fromkeys(found))


class DurableFactProjector:
    """Read exact Artifact/Compute facts through the Store's connection."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def project(
        self, episode: Episode, events: Iterable[ExecutionEvent]
    ) -> tuple[ExecutionEvent, ...]:
        base = tuple(events)
        known = {event.event_id for event in base}
        version_sources: dict[str, str | None] = {}
        job_ids: list[str] = []
        for event in base:
            for version_id in _version_ids(event.payload):
                version_sources.setdefault(version_id, event.event_id)
            for job_id in _job_ids(event.payload):
                if job_id not in job_ids:
                    job_ids.append(job_id)
        reset = episode.metadata.get("reset")
        if isinstance(reset, Mapping):
            initial = reset.get("initial_artifacts")
            if isinstance(initial, Mapping):
                for value in initial.values():
                    if isinstance(value, str) and value.strip():
                        version_sources.setdefault(value, "initial")

        projected: list[ExecutionEvent] = []
        projected.extend(self._artifacts(episode, version_sources, known))
        projected.extend(self._compute(episode, job_ids, known))
        if not projected:
            return base
        next_sequence = max((event.sequence or 0 for event in base), default=0) + 1
        projected.sort(key=lambda event: (event.created_at, event.event_id))
        return base + tuple(
            replace(item, sequence=next_sequence + index)
            for index, item in enumerate(projected)
            if item.event_id not in known
        )

    def _artifacts(
        self,
        episode: Episode,
        sources: Mapping[str, str | None],
        known: set[str],
    ) -> list[ExecutionEvent]:
        if not sources:
            return []
        ids = tuple(sorted(sources))
        marks = ",".join("?" for _ in ids)
        rows = self._connection.execute(
            "SELECT v.version_id,v.artifact_id,v.filename,v.content_type,"
            "v.size_bytes,v.checksum,v.producing_cell_id,v.frame_id,v.created_at "
            f"FROM artifact_versions v WHERE v.version_id IN ({marks})",
            ids,
        ).fetchall()
        result: list[ExecutionEvent] = []
        for row in rows:
            event_id = f"projected:artifact:{row['version_id']}"
            if event_id in known:
                continue
            result.append(
                ExecutionEvent(
                    event_id=event_id,
                    episode_id=episode.episode_id,
                    kind="artifact.version",
                    created_at=int(row["created_at"]),
                    source_id=sources.get(str(row["version_id"])),
                    payload={
                        "version_id": row["version_id"],
                        "artifact_id": row["artifact_id"],
                        "filename": row["filename"],
                        "content_type": row["content_type"],
                        "size_bytes": row["size_bytes"],
                        "checksum": row["checksum"],
                        "producing_cell_id": row["producing_cell_id"],
                        "frame_id": row["frame_id"],
                        "source": "durable_artifact_projection",
                    },
                )
            )
        return result

    def _compute(
        self, episode: Episode, job_ids: Iterable[str], known: set[str]
    ) -> list[ExecutionEvent]:
        ids = tuple(dict.fromkeys(job_ids))
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = self._connection.execute(
            "SELECT job_id,provider,status,receipt,input_versions,artifact_manifest,"
            "created_at,updated_at,submitted_at,terminal_at,termination_reason,reason "
            f"FROM compute_jobs WHERE job_id IN ({marks})",
            ids,
        ).fetchall()
        result: list[ExecutionEvent] = []
        for row in rows:
            job_id = str(row["job_id"])
            event = self._event(
                f"projected:compute:{job_id}",
                episode.episode_id,
                int(row["created_at"]),
                "compute.job",
                {
                    "job_id": job_id,
                    "provider": row["provider"],
                    "status": row["status"],
                    "receipt": row["receipt"],
                    "input_versions": _json_column(row["input_versions"], []),
                    "artifact_manifest": _json_column(row["artifact_manifest"], None),
                    "updated_at": row["updated_at"],
                    "submitted_at": row["submitted_at"],
                    "terminal_at": row["terminal_at"],
                    "termination_reason": row["termination_reason"],
                    "reason": row["reason"],
                    "source": "durable_compute_projection",
                },
                known,
            )
            if event.event_id not in known:
                result.append(event)
            event_rows = self._connection.execute(
                "SELECT seq,kind,at,payload FROM compute_job_events "
                "WHERE job_id=? ORDER BY seq",
                (job_id,),
            ).fetchall()
            for event_row in event_rows:
                event = self._event(
                    f"projected:compute:{job_id}:{event_row['seq']}",
                    episode.episode_id,
                    int(event_row["at"]),
                    "compute.job_event",
                    {
                        "job_id": job_id,
                        "seq": event_row["seq"],
                        "kind": event_row["kind"],
                        "payload": _json_column(event_row["payload"], None),
                        "source": "durable_compute_projection",
                    },
                    known,
                )
                if event.event_id not in known:
                    result.append(event)
        return result

    @staticmethod
    def _event(
        event_id: str,
        episode_id: str,
        created_at: int,
        kind: str,
        payload: Mapping[str, Any],
        known: set[str],
    ) -> ExecutionEvent:
        if event_id in known:
            # The caller filters these events before appending; returning a
            # deterministic duplicate keeps the helper total and simple.
            return ExecutionEvent(event_id, episode_id, kind, created_at, payload)
        return ExecutionEvent(event_id, episode_id, kind, created_at, payload)


__all__ = ["DurableFactProjector"]

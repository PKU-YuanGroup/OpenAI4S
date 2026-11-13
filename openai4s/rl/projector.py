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
        version_sources: dict[str, tuple[str | None, str | None, str | None]] = {}
        job_sources: dict[str, tuple[str | None, str | None, str | None]] = {}
        for event in base:
            for version_id in _version_ids(event.payload):
                version_sources.setdefault(
                    version_id,
                    (event.event_id, event.decision_id, event.segment_id),
                )
            for job_id in _job_ids(event.payload):
                job_sources.setdefault(
                    job_id,
                    (event.event_id, event.decision_id, event.segment_id),
                )
        reset = episode.metadata.get("reset")
        if isinstance(reset, Mapping):
            initial = reset.get("initial_artifacts")
            if isinstance(initial, Mapping):
                for value in initial.values():
                    if isinstance(value, str) and value.strip():
                        version_sources.setdefault(value, ("initial", None, None))

        # A terminal episode's projection must not change after it finished:
        # compute lifecycle facts recorded later stay out of its trajectory.
        cutoff = (
            max(
                (
                    event.created_at
                    for event in base
                    if event.kind == "episode.finished"
                ),
                default=None,
            )
            if episode.status != "running"
            else None
        )
        projected: list[ExecutionEvent] = []
        projected.extend(self._artifacts(episode, version_sources, known))
        projected.extend(self._compute(episode, job_sources, known, cutoff))
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
        sources: Mapping[str, tuple[str | None, str | None, str | None]],
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
            source_context = sources.get(str(row["version_id"]), (None, None, None))
            result.append(
                ExecutionEvent(
                    event_id=event_id,
                    episode_id=episode.episode_id,
                    kind="artifact.version",
                    created_at=int(row["created_at"]),
                    source_id=source_context[0],
                    decision_id=source_context[1],
                    segment_id=source_context[2],
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
        self,
        episode: Episode,
        job_sources: Mapping[str, tuple[str | None, str | None, str | None]],
        known: set[str],
        cutoff: int | None = None,
    ) -> list[ExecutionEvent]:
        ids = tuple(job_sources)
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = self._connection.execute(
            "SELECT job_id,provider,input_versions,created_at "
            f"FROM compute_jobs WHERE job_id IN ({marks})",
            ids,
        ).fetchall()
        result: list[ExecutionEvent] = []
        for row in rows:
            job_id = str(row["job_id"])
            # Only columns fixed at job creation: status, receipt, manifest
            # and timestamps keep changing, so they arrive as job events.
            event = self._event(
                f"projected:compute:{job_id}",
                episode.episode_id,
                int(row["created_at"]),
                "compute.job",
                {
                    "job_id": job_id,
                    "provider": row["provider"],
                    "input_versions": _json_column(row["input_versions"], []),
                    "source": "durable_compute_projection",
                },
                known,
                source_context=job_sources.get(job_id),
            )
            if event.event_id not in known:
                result.append(event)
            event_rows = self._connection.execute(
                "SELECT seq,kind,at,payload FROM compute_job_events "
                "WHERE job_id=? AND (? IS NULL OR at<=?) ORDER BY seq",
                (job_id, cutoff, cutoff),
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
                    source_context=job_sources.get(job_id),
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
        source_context: tuple[str | None, str | None, str | None] | None = None,
    ) -> ExecutionEvent:
        context = source_context or (None, None, None)
        return ExecutionEvent(
            event_id,
            episode_id,
            kind,
            created_at,
            payload,
            decision_id=context[1],
            segment_id=context[2],
            source_id=context[0],
        )


__all__ = ["DurableFactProjector"]

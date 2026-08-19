"""Immutable Evidence Snapshot construction for Stage 3 scientific review.

A snapshot is complete only when every required artifact version, adapter,
lineage edge, and truncation declaration is present. Filename-only coverage is
never complete. The snapshot never includes hidden Agent reasoning.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openai4s.server.evidence_adapters import adapt_artifact, classify_artifact

SNAPSHOT_SCHEMA_VERSION = 1


def canonical_snapshot_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def snapshot_digest(value: Any) -> str:
    return hashlib.sha256(canonical_snapshot_bytes(value)).hexdigest()


def _text(value: Any, limit: int = 20_000) -> str:
    return str(value or "")[:limit]


def _artifact_version_id(item: Mapping[str, Any]) -> str:
    return str(item.get("version_id") or item.get("latest_version_id") or "")


def _read_versions_for_cell(
    store: Any,
    root_frame_id: str,
    cell: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
) -> list[str]:
    existing = [str(item) for item in (cell.get("read_versions") or []) if item]
    if existing:
        return existing[:16]
    versions: list[str] = []
    output_ids = [
        _artifact_version_id(item)
        for item in artifacts
        if isinstance(item, Mapping) and _artifact_version_id(item)
    ]
    for output_id in output_ids:
        try:
            edges = store.lineage_inputs(
                output_id,
                producing_cell_id=str(
                    cell.get("cell_id") or cell.get("producing_cell_id") or ""
                )
                or None,
            )
        except Exception:  # noqa: BLE001
            edges = []
        for edge in edges or []:
            if not isinstance(edge, Mapping):
                continue
            version_id = str(
                edge.get("version_id") or edge.get("input_version_id") or ""
            )
            if version_id and version_id not in versions:
                versions.append(version_id)
    if versions:
        return versions[:16]
    try:
        listed = store.list_artifacts({"root_frame_id": root_frame_id})
    except Exception:  # noqa: BLE001
        listed = []
    catalog = [
        item for item in (*artifacts, *(listed or [])) if isinstance(item, Mapping)
    ]
    output_set = set(output_ids)
    for name in list(cell.get("files_read") or [])[:16]:
        hit = next(
            (item for item in catalog if str(item.get("filename") or "") == str(name)),
            None,
        )
        version_id = _artifact_version_id(hit or {})
        if version_id and version_id not in versions and version_id not in output_set:
            versions.append(version_id)
    return versions


def _ref(
    ref_id: str,
    kind: str,
    **fields: Any,
) -> dict[str, Any]:
    row = {"ref_id": ref_id, "kind": kind}
    for name, item in fields.items():
        if item is not None and item != "":
            row[name] = item
    return row


def freeze_evidence_snapshot(parts: Mapping[str, Any]) -> dict[str, Any]:
    """Canonically freeze caller-supplied snapshot parts.

    Production collection and golden fixtures both go through this function so
    tests exercise the same completeness and reference rules as the daemon.
    """

    identity = dict(parts.get("identity") or {})
    artifacts = [
        dict(item)
        for item in (parts.get("artifacts") or [])
        if isinstance(item, Mapping)
    ]
    cells = [
        dict(item) for item in (parts.get("cells") or []) if isinstance(item, Mapping)
    ]
    tools = [
        dict(item)
        for item in (parts.get("tool_ledger") or [])
        if isinstance(item, Mapping)
    ]
    lineage = [
        dict(item) for item in (parts.get("lineage") or []) if isinstance(item, Mapping)
    ]
    adapters = [
        dict(item)
        for item in (parts.get("adapters") or [])
        if isinstance(item, Mapping)
    ]
    truncation = dict(parts.get("truncation") or {})
    omissions: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = [
        _ref("source:user_request", "user_request"),
        _ref("source:candidate_answer", "completion"),
    ]
    if parts.get("plan") is not None:
        refs.append(_ref("plan:current", "plan"))
    if parts.get("environment"):
        refs.append(_ref("env:runtime", "environment"))

    reported = int(parts.get("changed_artifact_count") or len(artifacts))
    included = len(artifacts)
    omitted_count = max(
        0, int(parts.get("omitted_artifact_count") or 0), reported - included
    )
    if omitted_count:
        omissions.append(
            {
                "kind": "artifact_omitted",
                "count": omitted_count,
            }
        )
    for artifact in artifacts:
        version_id = _text(
            artifact.get("version_id") or artifact.get("latest_version_id"), 160
        )
        artifact_id = _text(artifact.get("artifact_id"), 160)
        if not version_id:
            omissions.append(
                {"kind": "artifact_version_missing", "artifact_id": artifact_id}
            )
            continue
        refs.append(
            _ref(
                f"art:{version_id}",
                "artifact_version",
                artifact_id=artifact_id,
                version_id=version_id,
                checksum=artifact.get("checksum"),
            )
        )
        if not artifact.get("checksum"):
            omissions.append(
                {
                    "kind": "checksum_missing",
                    "version_id": version_id,
                    "artifact_id": artifact_id,
                }
            )
        if artifact.get("exists") is False:
            omissions.append(
                {"kind": "artifact_bytes_missing", "version_id": version_id}
            )
        required = classify_artifact(
            str(artifact.get("filename") or ""),
            str(artifact.get("content_type") or ""),
        )
        if required:
            adapter = next(
                (
                    item
                    for item in adapters
                    if item.get("version_id") == version_id
                    and item.get("adapter") == required
                ),
                None,
            )
            if adapter is None or adapter.get("complete") is not True:
                omissions.append(
                    {
                        "kind": "adapter_incomplete",
                        "adapter": required,
                        "version_id": version_id,
                        "reason": (adapter or {}).get("omission_reason")
                        or "adapter_missing",
                    }
                )
            else:
                refs.append(
                    _ref(
                        f"adapter:{version_id}:{required}",
                        "adapter",
                        version_id=version_id,
                        artifact_id=artifact_id,
                        adapter=required,
                    )
                )
    for cell in cells:
        cell_id = _text(
            cell.get("cell_id") or cell.get("producing_cell_id") or cell.get("id"), 160
        )
        if cell_id:
            refs.append(_ref(f"cell:{cell_id}", "cell", cell_id=cell_id))
        for version_id in cell.get("read_versions") or []:
            version_id = _text(version_id, 160)
            if version_id:
                refs.append(
                    _ref(
                        f"art:{version_id}",
                        "artifact_version",
                        version_id=version_id,
                        role="input",
                    )
                )
    for edge in lineage:
        output_id = _text(edge.get("output_version_id"), 160)
        input_id = _text(edge.get("input_version_id"), 160)
        if output_id and input_id:
            refs.append(
                _ref(
                    f"lineage:{input_id}->{output_id}",
                    "lineage",
                    input_version_id=input_id,
                    output_version_id=output_id,
                )
            )
    # The prose fields are clipped below by `_text`. Recording that here is what
    # keeps the clip honest: `complete` is `not omissions`, and a snapshot that
    # silently dropped the tail of the answer still reported complete -- so a
    # false claim past the cut was invisible to the deterministic checks AND to
    # the reviewer packet (both read `snapshot["candidate_answer"]`), and the
    # turn promoted to Verified on evidence nobody had seen.
    for field, limit in (("candidate_answer", 24_000), ("user_request", 16_000)):
        if len(str(parts.get(field) or "")) > limit:
            truncation[field] = True
    if any(truncation.values()):
        omissions.append(
            {
                "kind": "truncated",
                "fields": sorted(k for k, v in truncation.items() if v),
            }
        )

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "frozen": True,
        "hidden_reasoning_excluded": True,
        "identity": {
            "root_frame_id": _text(identity.get("root_frame_id"), 160),
            "branch_id": _text(identity.get("branch_id"), 160),
            "turn_id": _text(identity.get("turn_id"), 160),
            "execution_id": _text(identity.get("execution_id"), 160),
        },
        "user_request": _text(parts.get("user_request"), 16_000),
        "plan": parts.get("plan"),
        "candidate_answer": _text(parts.get("candidate_answer"), 24_000),
        "structured_completion": parts.get("structured_completion"),
        "artifacts": artifacts,
        "changed_artifact_count": reported,
        "omitted_artifact_count": omitted_count,
        "cells": cells,
        "tool_ledger": tools,
        "lineage": lineage,
        "environment": dict(parts.get("environment") or {}),
        "source_metadata": dict(parts.get("source_metadata") or {}),
        "adapters": adapters,
        "truncation": truncation,
        "omissions": omissions,
        "evidence_refs": refs,
        "complete": not omissions,
    }
    snapshot["snapshot_sha256"] = snapshot_digest(snapshot)
    return snapshot


def resolve_evidence_ref(
    snapshot: Mapping[str, Any], ref_id: str
) -> dict[str, Any] | None:
    """Return the snapshot evidence_ref row, or None if the id is not present."""

    if not isinstance(snapshot, Mapping):
        return None
    wanted = str(ref_id or "")
    for row in snapshot.get("evidence_refs") or []:
        if isinstance(row, Mapping) and row.get("ref_id") == wanted:
            return dict(row)
    return None


def collect_turn_evidence(
    store: Any,
    *,
    root_frame_id: str,
    branch_id: str,
    turn_id: str,
    execution_id: str,
    user_request: str,
    candidate_answer: str,
    structured_completion: Any = None,
    artifact_versions_before: Mapping[str, Any] | None = None,
    cell_count_before: int = 0,
    step_count_before: int = 0,
) -> dict[str, Any]:
    """Gather one turn's evidence from Store and freeze it."""

    prior = dict(artifact_versions_before or {})
    artifacts: list[dict[str, Any]] = []
    adapters: list[dict[str, Any]] = []
    omitted = 0
    listed = store.list_artifacts({"root_frame_id": root_frame_id})
    for artifact in listed:
        artifact_id = str(artifact.get("artifact_id") or artifact.get("id") or "")
        if not artifact_id:
            continue
        latest = artifact.get("latest_version_id")
        if artifact_id in prior and prior[artifact_id] == latest:
            continue
        if len(artifacts) >= 64:
            omitted += 1
            continue
        meta = store.version_meta(str(latest)) if latest else None
        meta = meta if isinstance(meta, Mapping) else {}
        path = artifact.get("path") or store.resolve_artifact_path(artifact_id)
        version_path = meta.get("snapshot_path") or meta.get("path") or path
        item = {
            "artifact_id": artifact_id,
            "filename": artifact.get("filename"),
            "content_type": artifact.get("content_type"),
            "size_bytes": meta.get("size_bytes") or artifact.get("size_bytes"),
            "version_id": latest,
            "latest_version_id": latest,
            "checksum": meta.get("checksum") or meta.get("sha256"),
            "exists": bool(version_path and Path(str(version_path)).is_file()),
        }
        artifacts.append(item)
        adapter = adapt_artifact(
            version_path,
            filename=str(item.get("filename") or ""),
            content_type=str(item.get("content_type") or ""),
            version_id=str(latest or ""),
            artifact_id=artifact_id,
        )
        if adapter is not None:
            adapters.append(adapter)

    cells_raw = store.list_cells(root_frame_id, branch_id=branch_id)
    if not isinstance(cells_raw, Sequence):
        cells_raw = []
    new_cells = list(cells_raw)[int(cell_count_before) :]
    truncation = {"cells": len(new_cells) > 24, "tools": False}
    cells = []
    for cell in new_cells[-24:]:
        if not isinstance(cell, Mapping):
            continue
        cells.append(
            {
                "cell_id": cell.get("producing_cell_id")
                or cell.get("cell_id")
                or cell.get("id"),
                "cell_index": cell.get("cell_index"),
                "language": cell.get("language"),
                "status": cell.get("status"),
                "source": _text(cell.get("code") or cell.get("source"), 5_000),
                "stdout": _text(cell.get("stdout"), 4_000),
                "stderr": _text(cell.get("stderr"), 2_000),
                "error": _text(cell.get("error"), 2_000),
                "files_written": list(cell.get("files_written") or [])[:16],
                "files_read": list(cell.get("files_read") or [])[:16],
                "read_versions": _read_versions_for_cell(
                    store, root_frame_id, cell, artifacts
                ),
            }
        )

    tool_start = max(int(step_count_before), store.step_count(root_frame_id) - 200)
    tools = []
    steps = store.list_steps(root_frame_id, start=tool_start, limit=200)
    for step in list(steps)[-32:]:
        if not isinstance(step, Mapping) or step.get("kind") == "review":
            continue
        tools.append(
            {
                "kind": step.get("kind"),
                "title": step.get("title"),
                "status": step.get("status"),
                "summary": step.get("summary"),
            }
        )
    if len(steps) > 32:
        truncation["tools"] = True

    lineage = []
    for artifact in artifacts:
        version_id = artifact.get("version_id")
        if not version_id:
            continue
        try:
            edges = store.lineage_inputs(str(version_id))
        except Exception:  # noqa: BLE001 - missing lineage is an omission, not a crash
            edges = []
        for edge in edges or []:
            if not isinstance(edge, Mapping):
                continue
            input_id = edge.get("version_id") or edge.get("input_version_id")
            if not input_id:
                continue
            lineage.append(
                {
                    "input_version_id": input_id,
                    "output_version_id": version_id,
                    "filename": edge.get("filename"),
                }
            )

    environment: dict[str, Any] = {}
    try:
        latest = store.latest_kernel_generation(
            root_frame_id, "python", branch_id=branch_id
        )
        if isinstance(latest, Mapping):
            environment = {
                "language": latest.get("language") or "python",
                "environment_name": latest.get("environment_name")
                or latest.get("env_name"),
                "generation_id": latest.get("generation_id"),
                "interpreter": latest.get("interpreter"),
            }
    except Exception:  # noqa: BLE001
        environment = {}

    plan = None
    try:
        plan = store.get_plan_by_frame(root_frame_id)
    except Exception:  # noqa: BLE001
        plan = None

    source_metadata = {}
    for artifact in artifacts:
        version_id = artifact.get("version_id")
        if not version_id:
            continue
        try:
            meta = store.version_meta(str(version_id)) or {}
        except Exception:  # noqa: BLE001
            meta = {}
        retrieval = meta.get("retrieval") or meta.get("source")
        if retrieval:
            source_metadata[str(version_id)] = retrieval

    return freeze_evidence_snapshot(
        {
            "identity": {
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "turn_id": turn_id,
                "execution_id": execution_id,
            },
            "user_request": user_request,
            "plan": plan,
            "candidate_answer": candidate_answer,
            "structured_completion": structured_completion,
            "artifacts": artifacts,
            "changed_artifact_count": len(artifacts) + omitted,
            "omitted_artifact_count": omitted,
            "cells": cells,
            "tool_ledger": tools,
            "lineage": lineage,
            "environment": environment,
            "source_metadata": source_metadata,
            "adapters": adapters,
            "truncation": truncation,
        }
    )

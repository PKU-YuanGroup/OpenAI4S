"""Flattened, deterministic, import-compatible Session share snapshots.

A share snapshot is NOT the full Session package.  It is a *flattened* view of
one active branch's final logical state, rebuilt onto a single synthetic root
branch with zero checkpoints.  This deliberately sidesteps the reference-closure
hazards of exporting a partial branch/checkpoint DAG (revert-to-sibling, cursor
remapping, dangling metadata) — see docs/webshare.md and the plan.

Two artefacts are produced from ONE immutable :class:`ShareProjection`:

* a **bundle** — a ZIP in the exact ``SessionPackageService.import_bytes`` wire
  format (same manifest, same twelve required documents, same secret gate), so a
  recipient imports it through the unchanged quarantine → restart_fresh path; and
* a **view** — a redacted read-only projection the static viewer renders.

The projection is frozen once (inside a FIFO execution ticket by the caller);
neither serializer re-reads the live Store, workspace, or artifact repository.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from openai4s.agent.ledger import branch_action_groups

# Reuse the on-disk package format primitives so a share bundle is byte-for-byte
# compatible with the existing untrusted importer.  These are intentionally the
# single source of truth for the wire format; do not re-implement them here.
from openai4s.server.session_package import (
    MAX_ARCHIVE_BYTES,
    MAX_ENTRY_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    PACKAGE_FORMAT,
    PACKAGE_SCHEMA_VERSION,
    SessionPackageError,
    SessionPackageService,
    _assert_secret_free,
    _canonical_json,
    _safe_artifact_filename,
    _safe_text,
    _sanitize,
    _sha256,
    _zip_bytes,
)
from openai4s.storage.branch_projection import project_branch_records
from openai4s.storage.snapshots import WorkspaceCAS

SHARE_VIEW_SCHEMA_VERSION = 1

# stdout/stderr shown in the read-only viewer are bounded; the full text always
# remains inside the downloadable bundle's notebook.json.
_VIEW_STREAM_CAP = 256 * 1024

WorkspaceResolver = Callable[[str, str], "str | Path"]
SecretValueProvider = Callable[[], "tuple[str, ...]"]


class ShareCancelled(RuntimeError):
    """The share snapshot was cancelled (execution ticket signalled)."""


@dataclass(frozen=True)
class ShareProjection:
    """One frozen, self-contained snapshot of a session's shareable state."""

    projection_id: str
    source_project_id: str
    source_root_frame_id: str
    frame_meta: Mapping[str, Any]
    project_meta: Mapping[str, Any]
    messages: tuple[Mapping[str, Any], ...]
    groups: tuple[Mapping[str, Any], ...]
    cells: tuple[Mapping[str, Any], ...]
    artifacts: tuple[Mapping[str, Any], ...]
    artifact_bytes: Mapping[str, bytes]
    env_snapshots: tuple[Mapping[str, Any], ...]
    lineage: tuple[Mapping[str, Any], ...]
    plans: tuple[Mapping[str, Any], ...]
    review: Mapping[str, Any]
    workspace: Mapping[str, Any]
    workspace_files: Mapping[str, bytes]
    excluded: Mapping[str, Any]
    counts: Mapping[str, int]


class ShareProjectionBuilder:
    """Build a frozen :class:`ShareProjection` and serialize it two ways."""

    def __init__(
        self,
        store: Any,
        *,
        data_dir: str | Path,
        workspace: WorkspaceResolver,
        cas: WorkspaceCAS,
        extra_secret_values: SecretValueProvider | None = None,
    ) -> None:
        self.store = store
        self.data_dir = Path(data_dir).expanduser().resolve()
        self._workspace = workspace
        self.cas = cas
        self._extra_secret_values = extra_secret_values or (lambda: ())
        # Reuse the packaging service purely for its workspace capture and
        # configured-secret byte scan; the share flow never calls its exporter.
        self._pkg = SessionPackageService(
            store, data_dir=data_dir, workspace=workspace, cas=cas
        )

    # ------------------------------------------------------------------ secret
    def _extra_secret_bytes(self) -> tuple[bytes, ...]:
        out: set[bytes] = set()
        for value in self._extra_secret_values() or ():
            if isinstance(value, str) and len(value) >= 8:
                out.add(value.encode("utf-8"))
        return tuple(out)

    def _contains_secret(self, data: bytes) -> bool:
        # Text/ASCII signature + configured-secret exact bytes (session_package),
        # plus any programmatically injected extra secrets (e.g. a share token
        # supplied via Config rather than os.environ).  Binary artifacts get only
        # the best-effort ASCII/known-byte scan — never a content-level guarantee.
        if self._pkg._contains_secret_bytes(data):
            return True
        return any(secret in data for secret in self._extra_secret_bytes())

    # ------------------------------------------------------------------- build
    def build(
        self,
        root_frame_id: str,
        active_branch: str,
        *,
        cancel_event: Any = None,
    ) -> ShareProjection:
        def _ck() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise ShareCancelled("share snapshot cancelled")

        frame = self.store.get_frame(root_frame_id)
        if frame is None:
            raise KeyError(f"unknown session {root_frame_id!r}")
        if (frame.get("root_frame_id") or root_frame_id) != root_frame_id:
            raise SessionPackageError("share requires a root frame")
        project_id = str(frame.get("project_id") or "default")
        project = self.store.get_project(project_id) or {}
        _ck()

        # 1. Single current-workspace tree.  ``checkpoints=[]`` makes the shared
        #    packaging helper capture exactly the active workspace and no history
        #    trees, then re-stamp the projection onto the synthetic root branch.
        workspace_files, workspace_projection = self._pkg._export_workspace(
            root_frame_id, active_branch=active_branch, checkpoints=[]
        )
        workspace_projection = dict(workspace_projection)
        workspace_projection["active_branch_id"] = root_frame_id
        workspace_names = self._workspace_basenames(
            workspace_projection, workspace_files
        )
        _ck()

        # 2. Projected logical history (folds fork inheritance + revert exclusion).
        messages = self._project_messages(root_frame_id, active_branch)
        groups = self._project_groups(root_frame_id, active_branch)
        cells = self._project_cells(root_frame_id, active_branch)
        _ck()

        retained_cell_ids = {
            str(cell.get("producing_cell_id"))
            for cell in cells
            if cell.get("producing_cell_id")
        }

        # 3. Artifact transitive closure + version trimming + byte capture.
        (
            artifacts,
            artifact_files,
            env_snapshots,
            lineage,
            excluded,
        ) = self._collect_artifacts(
            root_frame_id,
            retained_cell_ids=retained_cell_ids,
            referenced_names=self._referenced_names(cells),
            workspace_names=workspace_names,
        )
        _ck()

        # Resolve each notebook figure to an exact artifact byte hash for the
        # viewer.  Prefer the version produced by that cell, else the artifact's
        # latest available version.
        by_name, latest_sha_by_name = self._filename_index(artifacts)
        cells = tuple(
            {
                **cell,
                "figure_refs": self._figure_refs(cell, by_name, latest_sha_by_name),
            }
            for cell in cells
        )

        safe_artifact_ids = {str(item["artifact_id"]) for item in artifacts}
        plans = self._project_plans(root_frame_id, safe_artifact_ids)
        review = self._project_review(root_frame_id, safe_artifact_ids)
        _ck()

        frame_meta = {
            key: _sanitize(frame.get(key))
            for key in (
                "name",
                "task_summary",
                "model",
                "effort",
                "runtime_env",
                "created_at",
                "updated_at",
            )
        }
        project_meta = {
            "name": _safe_text(project.get("name") or "Shared research"),
            "description": _safe_text(project.get("description") or ""),
        }
        counts = {
            "messages": len(messages),
            "cells": len(cells),
            "artifacts": len(artifacts),
        }
        core = {
            "source_root_frame_id": root_frame_id,
            "frame": frame_meta,
            "project": project_meta,
            "messages": list(messages),
            "groups": list(groups),
            "cells": list(cells),
            "artifacts": list(artifacts),
            "env_snapshots": list(env_snapshots),
            "lineage": list(lineage),
            "plans": list(plans),
            "review": review,
            "workspace": workspace_projection,
            "excluded": excluded,
        }
        projection_id = _sha256(_canonical_json(core))
        return ShareProjection(
            projection_id=projection_id,
            source_project_id=project_id,
            source_root_frame_id=root_frame_id,
            frame_meta=frame_meta,
            project_meta=project_meta,
            messages=tuple(messages),
            groups=tuple(groups),
            cells=tuple(cells),
            artifacts=tuple(artifacts),
            artifact_bytes=dict(artifact_files),
            env_snapshots=tuple(env_snapshots),
            lineage=tuple(lineage),
            plans=tuple(plans),
            review=review,
            workspace=workspace_projection,
            workspace_files=dict(workspace_files),
            excluded=excluded,
            counts=counts,
        )

    # --------------------------------------------------------------- projection
    def _project_messages(
        self, root_frame_id: str, active_branch: str
    ) -> list[dict[str, Any]]:
        boundaries = self.store.list_branch_message_boundaries(
            root_frame_id, branch_id=active_branch, limit=None
        )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in boundaries:
            mid = str(item.get("message_id") or "")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            seq = len(out)
            role = str(item.get("role") or "assistant")
            if role not in {"user", "assistant", "system"}:
                role = "assistant"
            out.append(
                {
                    "message_id": item.get("message_id"),
                    "branch_id": root_frame_id,
                    "seq": seq,
                    "role": role,
                    "content": _safe_text(item.get("content") or ""),
                    "metadata": None,
                    "created_at": item.get("created_at"),
                }
            )
        return out

    def _project_groups(
        self, root_frame_id: str, active_branch: str
    ) -> list[dict[str, Any]]:
        projected = branch_action_groups(
            self.store, root_frame_id, branch_id=active_branch
        )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in projected:
            gid = str(group.get("group_id") or "")
            if gid and gid in seen:
                continue
            if gid:
                seen.add(gid)
            safe = SessionPackageService._safe_group(group)
            safe["root_frame_id"] = root_frame_id
            safe["branch_id"] = root_frame_id
            safe["ordinal"] = len(out)
            out.append(safe)
        return out

    def _branch_cells(self, root_frame_id: str, branch_id: str) -> list[dict[str, Any]]:
        return project_branch_records(
            self.store,
            root_frame_id,
            branch_id,
            list_local=lambda selected: self.store.list_cells(
                root_frame_id, branch_id=selected
            ),
            record_position=lambda cell: int(
                cell.get("state_revision") or cell.get("cell_index") or 0
            ),
            cursor_key="cell_cursor",
        )

    def _project_cells(
        self, root_frame_id: str, active_branch: str
    ) -> list[dict[str, Any]]:
        summaries = self._branch_cells(root_frame_id, active_branch)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for summary in summaries:
            cell_id = str(summary.get("producing_cell_id") or "")
            if cell_id and cell_id in seen:
                continue
            if cell_id:
                seen.add(cell_id)
            revision = len(out) + 1
            detail = self.store.cell_detail(cell_id) or summary
            safe = _sanitize(detail)
            safe.pop("project_id", None)
            safe.pop("root_frame_id", None)
            safe.pop("frame_id", None)
            language = str(safe.get("language") or "python").lower()
            if language not in {"python", "r"}:
                language = "python"
            safe["language"] = language
            safe["state_revision"] = revision
            safe["cell_index"] = revision
            out.append(safe)
        return out

    # ---------------------------------------------------------------- artifacts
    def _collect_artifacts(
        self,
        root_frame_id: str,
        *,
        retained_cell_ids: set[str],
        referenced_names: set[str],
        workspace_names: set[str],
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, bytes],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        all_artifacts = self.store.list_artifacts({"root_frame_id": root_frame_id})
        artifacts_by_id = {str(a["artifact_id"]): a for a in all_artifacts}
        versions_by_artifact: dict[str, list[dict[str, Any]]] = {}
        version_owner: dict[str, str] = {}
        for a in all_artifacts:
            aid = str(a["artifact_id"])
            versions = self.store.list_versions(aid)
            versions_by_artifact[aid] = versions
            for v in versions:
                version_owner[str(v["version_id"])] = aid

        # ---- retention set (rules 1-4) ----
        retained: set[str] = set()
        for a in all_artifacts:
            aid = str(a["artifact_id"])
            base = PurePosixPath(str(a.get("filename") or "")).name
            keep = bool(a.get("is_user_upload"))
            latest = a.get("latest_version_id")
            if latest:
                latest_meta = self.store.version_meta(str(latest))
                if (
                    latest_meta
                    and str(latest_meta.get("producing_cell_id") or "")
                    in retained_cell_ids
                ):
                    keep = True
            if base and (base in referenced_names or base in workspace_names):
                keep = True
            if keep:
                retained.add(aid)

        # ---- version selection ----
        kept_versions: set[str] = set()
        for aid in retained:
            for v in versions_by_artifact.get(aid, ()):
                vid = str(v["version_id"])
                if (
                    v.get("is_latest")
                    or str(v.get("producing_cell_id") or "") in retained_cell_ids
                ):
                    kept_versions.add(vid)
            latest = artifacts_by_id[aid].get("latest_version_id")
            if latest:
                kept_versions.add(str(latest))

        # ---- lineage ancestor closure (rule 5) ----
        worklist = list(kept_versions)
        while worklist:
            vid = worklist.pop()
            for raw in self.store.lineage_edges_for(vid, "up"):
                inp = str(raw)
                if inp in kept_versions:
                    continue
                owner = version_owner.get(inp)
                if not owner:
                    continue
                retained.add(owner)
                kept_versions.add(inp)
                worklist.append(inp)

        # ---- serialize retained artifacts + capture bytes ----
        files: dict[str, bytes] = {}
        artifacts: list[dict[str, Any]] = []
        env_by_id: dict[str, dict[str, Any]] = {}
        excluded: list[dict[str, str]] = []
        versions_trimmed = 0
        used_names: set[str] = set()

        for aid in sorted(retained):
            source = artifacts_by_id[aid]
            try:
                filename = _safe_artifact_filename(str(source.get("filename") or ""))
            except SessionPackageError:
                excluded.append({"artifact_id": aid, "reason": "secret_filename"})
                continue
            filename = self._dedupe_name(filename, used_names)

            records: list[dict[str, Any]] = []
            all_versions = versions_by_artifact.get(aid, ())
            versions_trimmed += sum(
                1 for v in all_versions if str(v["version_id"]) not in kept_versions
            )
            ordered = sorted(
                (v for v in all_versions if str(v["version_id"]) in kept_versions),
                key=lambda item: (
                    int(item.get("created_at") or 0),
                    str(item.get("version_id") or ""),
                ),
            )
            for v in ordered:
                vid = str(v["version_id"])
                meta = self.store.version_meta(vid) or v
                record = {
                    key: _sanitize(meta.get(key))
                    for key in (
                        "version_id",
                        "content_type",
                        "size_bytes",
                        "checksum",
                        "created_at",
                    )
                }
                producing = str(meta.get("producing_cell_id") or "")
                record["producing_cell_id"] = (
                    producing if producing in retained_cell_ids else None
                )
                record["frame_id"] = None
                record["filename"] = filename
                # Only reference an env snapshot we actually captured, so the
                # bundle never carries a dangling env_snapshot_id (import rejects
                # unknown references).
                record["env_snapshot_id"] = None
                candidate = meta.get("snapshot_path") or meta.get("path")
                data: bytes | None = None
                if candidate:
                    try:
                        data = Path(str(candidate)).read_bytes()
                    except OSError:
                        data = None
                if (
                    data is None
                    or len(data) > MAX_ENTRY_BYTES
                    or self._contains_secret(data)
                    or (meta.get("checksum") and _sha256(data) != meta.get("checksum"))
                ):
                    record.update({"available": False, "snapshot_sha256": None})
                else:
                    digest = _sha256(data)
                    files[f"artifact-data/{digest}"] = data
                    record.update(
                        {
                            "available": True,
                            "snapshot_sha256": digest,
                            "size_bytes": len(data),
                            "checksum": digest,
                        }
                    )
                    snapshot = self.store.env_snapshot_for_artifact(aid, vid)
                    if snapshot and snapshot.get("snapshot_id"):
                        snapshot_id = str(snapshot["snapshot_id"])
                        env_by_id[snapshot_id] = _sanitize(snapshot)
                        record["env_snapshot_id"] = snapshot_id
                records.append(record)

            available = [r for r in records if r.get("available")]
            if not available:
                excluded.append({"artifact_id": aid, "reason": "no_importable_version"})
                used_names.discard(filename.casefold())
                continue
            latest_version_id = source.get("latest_version_id")
            if not any(r.get("version_id") == latest_version_id for r in available):
                latest_version_id = available[-1].get("version_id")
            artifacts.append(
                {
                    "artifact_id": aid,
                    "content_type": _sanitize(source.get("content_type")),
                    "is_user_upload": _sanitize(source.get("is_user_upload")),
                    "priority": _sanitize(source.get("priority")),
                    "created_at": _sanitize(source.get("created_at")),
                    "filename": filename,
                    "latest_version_id": latest_version_id,
                    "versions": records,
                }
            )

        available_version_ids = {
            str(v["version_id"])
            for a in artifacts
            for v in a["versions"]
            if v.get("available") and v.get("version_id")
        }
        lineage = self._lineage_edges(available_version_ids, retained_cell_ids)
        excluded_summary = {
            "workspace_skipped": None,  # filled by serializer from workspace tree
            "artifacts_excluded": self._group_excluded(excluded),
            "versions_trimmed": versions_trimmed,
        }
        env_snapshots = [env_by_id[key] for key in sorted(env_by_id)]
        return artifacts, files, env_snapshots, lineage, excluded_summary

    def _lineage_edges(
        self, version_ids: set[str], retained_cell_ids: set[str]
    ) -> list[dict[str, Any]]:
        edges: list[dict[str, Any]] = []
        for source in sorted(version_ids):
            for raw in self.store.lineage_edges_for(source, "down"):
                target = str(raw)
                if target not in version_ids:
                    continue
                producing = self.store.producing_cell_for_version(target) or {}
                pid = str(producing.get("producing_cell_id") or "")
                edges.append(
                    {
                        "input_version_id": source,
                        "output_version_id": target,
                        "producing_cell_id": pid if pid in retained_cell_ids else None,
                    }
                )
        edges.sort(
            key=lambda item: (item["input_version_id"], item["output_version_id"])
        )
        return edges

    # -------------------------------------------------------------- plan/review
    def _project_plans(
        self, root_frame_id: str, safe_artifact_ids: set[str]
    ) -> list[dict[str, Any]]:
        plans = self.store.list_plans(root_frame_id, limit=5000)
        return [
            {
                **_sanitize(plan),
                "artifact_id": (
                    plan.get("artifact_id")
                    if str(plan.get("artifact_id") or "") in safe_artifact_ids
                    else None
                ),
            }
            for plan in plans
        ]

    def _project_review(
        self, root_frame_id: str, safe_artifact_ids: set[str]
    ) -> dict[str, Any]:
        annotations = [
            _sanitize(item)
            for item in self.store.list_annotations(root_frame_id)
            if str(item.get("artifact_id") or "") in safe_artifact_ids
        ]
        steps = [
            _sanitize(item)
            for item in self.store.list_steps(root_frame_id, limit=25000)
            if str(item.get("kind") or "").casefold() in {"review", "review_settings"}
        ]
        return {
            "annotations": annotations,
            "activity_steps": steps,
            "settings": {
                "auto_review": None,
                "reviewer_model": None,
                "active_on_import": False,
            },
        }

    # --------------------------------------------------------------- serialize
    def serialize_package(self, projection: ShareProjection) -> dict[str, Any]:
        """Produce an import_bytes-compatible flattened bundle."""

        root = projection.source_root_frame_id
        workspace = dict(projection.workspace)
        skipped = 0
        active_tree = str(workspace.get("active_source_tree_id") or "")
        safe_tree_id = str((workspace.get("tree_map") or {}).get(active_tree) or "")
        tree_doc = projection.workspace_files.get(
            f"workspace/trees/{safe_tree_id}.json"
        )
        if tree_doc:
            try:
                import json as _json

                skipped = len(_json.loads(tree_doc).get("skipped") or [])
            except (TypeError, ValueError):
                skipped = 0
        excluded = dict(projection.excluded)
        excluded["workspace_skipped"] = skipped

        branch = {
            "branch_id": root,
            "root_frame_id": root,
            "parent_branch_id": None,
            "base_checkpoint_id": None,
            "head_checkpoint_id": None,
            "name": None,
            "created_at": projection.frame_meta.get("created_at"),
            "updated_at": projection.frame_meta.get("updated_at")
            or projection.frame_meta.get("created_at"),
        }
        files: dict[str, bytes] = {
            "session.json": _canonical_json(
                {
                    "schema_version": PACKAGE_SCHEMA_VERSION,
                    "source": {
                        "project_id": projection.source_project_id,
                        "root_frame_id": root,
                        "active_branch_id": root,
                    },
                    "project": dict(projection.project_meta),
                    "frame": dict(projection.frame_meta),
                    "messages": list(projection.messages),
                    "share_view_schema_version": SHARE_VIEW_SCHEMA_VERSION,
                    "projection_id": projection.projection_id,
                }
            ),
            "ledger.json": _canonical_json(
                {"groups": list(projection.groups), "execution_attempts": []}
            ),
            "notebook.json": _canonical_json(
                {"cells": [self._package_cell(cell) for cell in projection.cells]}
            ),
            "snapshots.json": _canonical_json(
                {
                    "branches": [branch],
                    "checkpoints": [],
                    "checkpoint_states": [],
                    "operations": [],
                    "recovery_journal": [],
                    "workspace": workspace,
                }
            ),
            "artifacts.json": _canonical_json(
                {
                    "artifacts": list(projection.artifacts),
                    # Import ignores ``excluded``; keep the grouped summary for
                    # auditing without expanding it back to per-item rows.
                    "excluded": list(excluded.get("artifacts_excluded") or []),
                }
            ),
            "environment.json": _canonical_json(
                {
                    "generations": [],
                    "artifact_environment_snapshots": list(projection.env_snapshots),
                }
            ),
            "lineage.json": _canonical_json({"edges": list(projection.lineage)}),
            "plans.json": _canonical_json({"plans": list(projection.plans)}),
            "review.json": _canonical_json(dict(projection.review)),
            "memory.json": _canonical_json({"memories": []}),
            "permissions.json": _canonical_json(
                {
                    "policy": "share snapshots never export policy state",
                    "project": [],
                    "conversation": [],
                }
            ),
            "capabilities.json": _canonical_json({"states": []}),
            **dict(projection.workspace_files),
            **dict(projection.artifact_bytes),
        }

        # Residual secret gate (fail closed) — recursive on every JSON document
        # and byte-level on every file, mirroring the full exporter.
        import json as _json

        for name in (
            "session.json",
            "ledger.json",
            "notebook.json",
            "snapshots.json",
            "artifacts.json",
            "environment.json",
            "lineage.json",
            "plans.json",
            "review.json",
            "memory.json",
            "permissions.json",
            "capabilities.json",
        ):
            _assert_secret_free(_json.loads(files[name]), path=name)
        for name, payload in files.items():
            if self._contains_secret(payload):
                raise SessionPackageError(
                    f"share snapshot still contains secret material: {name}"
                )

        manifest_body = {
            "format": PACKAGE_FORMAT,
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "files": [
                {"path": name, "size": len(data), "sha256": _sha256(data)}
                for name, data in sorted(files.items())
            ],
        }
        manifest = {
            **manifest_body,
            "manifest_sha256": _sha256(_canonical_json(manifest_body)),
        }
        files["manifest.json"] = _canonical_json(manifest)
        if sum(len(payload) for payload in files.values()) > MAX_UNCOMPRESSED_BYTES:
            raise SessionPackageError("share snapshot expands beyond its limit")
        data = _zip_bytes(files)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise SessionPackageError("share snapshot archive exceeds its limit")
        stem = "".join(ch for ch in root if ch.isalnum() or ch in "-_") or "session"
        return {
            "filename": f"{stem}.openai4s-session.zip",
            "content_type": "application/vnd.openai4s.session+zip",
            "data": data,
            "size_bytes": len(data),
            "sha256": _sha256(data),
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "projection_id": projection.projection_id,
            "excluded": excluded,
            "immutable": True,
        }

    def serialize_view(
        self, projection: ShareProjection, *, bundle: Mapping[str, Any] | None = None
    ) -> bytes:
        """Produce the redacted read-only viewer document (view.json)."""

        hidden = 0
        cells: list[dict[str, Any]] = []
        for cell in projection.cells:
            if str(cell.get("visibility") or "scientific") != "scientific":
                hidden += 1
                continue
            stdout, stdout_trunc = self._clip(cell.get("stdout"))
            stderr, stderr_trunc = self._clip(cell.get("stderr"))
            cells.append(
                {
                    "cell_index": cell.get("cell_index"),
                    "state_revision": cell.get("state_revision"),
                    "language": cell.get("language"),
                    "kernel_id": cell.get("kernel_id"),
                    "status": cell.get("status"),
                    "source": cell.get("code"),
                    "stdout": stdout,
                    "stdout_truncated": stdout_trunc,
                    "stderr": stderr,
                    "stderr_truncated": stderr_trunc,
                    "error": cell.get("error"),
                    "figure_refs": cell.get("figure_refs") or [],
                    "files_written": list(cell.get("files_written") or ()),
                    "created_at": cell.get("created_at"),
                }
            )

        view_artifacts: list[dict[str, Any]] = []
        by_filename: dict[str, str] = {}
        for a in projection.artifacts:
            latest_sha = self._latest_available_sha(a)
            if latest_sha is None:
                continue
            view_artifacts.append(
                {
                    "artifact_id": a.get("artifact_id"),
                    "filename": a.get("filename"),
                    "content_type": a.get("content_type"),
                    "size_bytes": self._latest_available_size(a),
                    "sha256": latest_sha,
                    "created_at": a.get("created_at"),
                }
            )
            by_filename[str(a.get("filename") or "")] = latest_sha

        document = {
            "schema_version": SHARE_VIEW_SCHEMA_VERSION,
            "projection_id": projection.projection_id,
            "session": {
                "name": projection.frame_meta.get("name"),
                "task_summary": projection.frame_meta.get("task_summary"),
                "model": projection.frame_meta.get("model"),
                "project_name": projection.project_meta.get("name"),
                "created_at": projection.frame_meta.get("created_at"),
                "updated_at": projection.frame_meta.get("updated_at"),
            },
            "messages": [
                {
                    "seq": m.get("seq"),
                    "role": m.get("role"),
                    "content": m.get("content"),
                    "created_at": m.get("created_at"),
                }
                for m in projection.messages
                if m.get("role") in {"user", "assistant"}
            ],
            "cells": cells,
            "hidden_cell_count": hidden,
            "artifacts": view_artifacts,
            "by_filename": by_filename,
            "counts": dict(projection.counts),
            "excluded": dict(projection.excluded),
            "bundle": dict(bundle or {}),
        }
        _assert_secret_free(document, path="view.json")
        payload = _canonical_json(document)
        if self._contains_secret(payload):
            raise SessionPackageError("share view still contains secret material")
        return payload

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _package_cell(cell: Mapping[str, Any]) -> dict[str, Any]:
        out = {key: value for key, value in cell.items() if key != "figure_refs"}
        return out

    @staticmethod
    def _clip(value: Any) -> tuple[str, bool]:
        text = str(value or "")
        if len(text) > _VIEW_STREAM_CAP:
            return text[:_VIEW_STREAM_CAP], True
        return text, False

    @staticmethod
    def _dedupe_name(filename: str, used: set[str]) -> str:
        folded = filename.casefold()
        if folded not in used:
            used.add(folded)
            return filename
        path = PurePosixPath(filename)
        stem, suffix = path.stem, path.suffix
        counter = 2
        while True:
            candidate = f"{stem}-{counter}{suffix}"
            if candidate.casefold() not in used:
                used.add(candidate.casefold())
                return candidate
            counter += 1

    @staticmethod
    def _referenced_names(cells: tuple[Mapping[str, Any], ...]) -> set[str]:
        names: set[str] = set()
        for cell in cells:
            for group in ("figures", "files_written"):
                for item in cell.get(group) or ():
                    if isinstance(item, str) and item:
                        names.add(PurePosixPath(item).name)
        return names

    @staticmethod
    def _workspace_basenames(
        workspace: Mapping[str, Any], workspace_files: Mapping[str, bytes]
    ) -> set[str]:
        import json as _json

        names: set[str] = set()
        active = str(workspace.get("active_source_tree_id") or "")
        safe_id = str((workspace.get("tree_map") or {}).get(active) or "")
        raw = workspace_files.get(f"workspace/trees/{safe_id}.json")
        if not raw:
            return names
        try:
            tree = _json.loads(raw)
        except (TypeError, ValueError):
            return names
        for entry in tree.get("entries") or ():
            path = entry.get("path")
            if isinstance(path, str) and path:
                names.add(PurePosixPath(path).name)
        return names

    @staticmethod
    def _filename_index(
        artifacts: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Map basename -> {producing_cell_id: sha} and basename -> latest sha."""

        by_name: dict[str, dict[str, str]] = {}
        latest: dict[str, str] = {}
        for a in artifacts:
            base = PurePosixPath(str(a.get("filename") or "")).name
            latest_sha = ShareProjectionBuilder._latest_available_sha(a)
            if latest_sha:
                latest[base] = latest_sha
            for v in a.get("versions") or ():
                if not v.get("available"):
                    continue
                pid = str(v.get("producing_cell_id") or "")
                sha = str(v.get("snapshot_sha256") or "")
                if pid and sha:
                    by_name.setdefault(base, {})[pid] = sha
        return by_name, latest

    @staticmethod
    def _figure_refs(
        cell: Mapping[str, Any],
        by_name: dict[str, dict[str, str]],
        latest_sha_by_name: dict[str, str],
    ) -> list[dict[str, str]]:
        cell_id = str(cell.get("producing_cell_id") or "")
        refs: list[dict[str, str]] = []
        for item in cell.get("figures") or ():
            if not isinstance(item, str) or not item:
                continue
            base = PurePosixPath(item).name
            sha = (by_name.get(base) or {}).get(cell_id) or latest_sha_by_name.get(base)
            if sha:
                refs.append({"filename": base, "sha256": sha})
        return refs

    @staticmethod
    def _latest_available_sha(artifact: Mapping[str, Any]) -> str | None:
        latest_id = artifact.get("latest_version_id")
        available = [v for v in artifact.get("versions") or () if v.get("available")]
        for v in available:
            if v.get("version_id") == latest_id:
                return str(v.get("snapshot_sha256"))
        return str(available[-1].get("snapshot_sha256")) if available else None

    @staticmethod
    def _latest_available_size(artifact: Mapping[str, Any]) -> int | None:
        latest_id = artifact.get("latest_version_id")
        available = [v for v in artifact.get("versions") or () if v.get("available")]
        for v in available:
            if v.get("version_id") == latest_id:
                return v.get("size_bytes")
        return available[-1].get("size_bytes") if available else None

    @staticmethod
    def _group_excluded(excluded: list[dict[str, str]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in excluded:
            counts[item["reason"]] = counts.get(item["reason"], 0) + 1
        return [
            {"reason": reason, "count": count}
            for reason, count in sorted(counts.items())
        ]


__all__ = [
    "SHARE_VIEW_SCHEMA_VERSION",
    "ShareCancelled",
    "ShareProjection",
    "ShareProjectionBuilder",
]

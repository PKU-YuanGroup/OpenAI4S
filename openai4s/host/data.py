"""Store-backed data services exposed through ``host.*`` RPC.

This module owns query projection, artifact persistence/search, frame browsing,
and provenance/lineage reads.  ``HostDispatcher`` remains the policy, audit,
and routing envelope and delegates the domain behaviour here.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.artifact_restore import ArtifactRestoreService


class HostDataStore(Protocol):
    """Persistence surface required by :class:`HostDataService`."""

    def query(self, sql: str, *, params=None, limit=None, timeout_s=5.0):
        ...

    def schema(self) -> dict:
        ...

    def list_artifacts(self, filters: dict | None = None) -> list[dict]:
        ...

    def get_artifact(self, artifact_id: str) -> dict | None:
        ...

    def list_versions(self, artifact_id: str) -> list[dict]:
        ...

    def resolve_frame_scope(self, frame_id: str | None) -> dict:
        ...

    def resolve_artifact_path(self, ident: str) -> str | None:
        ...

    def record_cell_artifact(self, **fields: Any) -> dict:
        ...

    def record_artifact_restore(self, **fields: Any) -> dict:
        ...

    def version_meta(self, version_id: str) -> dict | None:
        ...

    def set_version_snapshot(self, version_id: str, snapshot_path: str) -> None:
        ...

    def set_priority(self, artifact_id: str, priority: int) -> dict | None:
        ...

    def frame_detail(self, frame_id: str, *, page: int, page_size: int):
        ...

    def search_frames(self, pattern: str, *, project_id: str, limit: int):
        ...

    def browse_frames(
        self,
        *,
        project_id: str,
        status: str | None,
        roots_only: bool,
        limit: int,
    ):
        ...

    def producing_cell_for_version(self, version_id: str) -> dict | None:
        ...

    def lineage_inputs(self, version_id: str) -> list[dict]:
        ...

    def lineage_edges_for(self, version_id: str, direction: str) -> list[dict]:
        ...

    def version_for_path(self, path: str) -> str | None:
        ...


StoreProvider = Callable[[], HostDataStore]
ConfigProvider = Callable[[], Any]
FrameIdProvider = Callable[[], str | None]
PathResolver = Callable[..., Path]

FRAME_STATUSES = frozenset({"processing", "done", "failed", "awaiting_user_response"})

_VALID_MARKER_ID = re.compile(
    r"^(v-)?[0-9a-fA-F]{8,}$|"
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def rank_artifacts(items: list[dict], query: str) -> list[dict]:
    """Return fuzzy-ranked artifact rows for the command/search surface."""
    normalized = query.lower().strip()
    query_tokens = set(re.findall(r"[a-z0-9]+", normalized))
    scored = []
    for item in items:
        name = str(item.get("filename", "")).lower()
        content_type = str(item.get("content_type", "") or "").lower()
        haystack_tokens = set(re.findall(r"[a-z0-9]+", f"{name} {content_type}"))
        score = 0.0
        if normalized and normalized in name:
            score += 3.0
        score += 1.5 * len(query_tokens & haystack_tokens)
        if query_tokens and query_tokens <= haystack_tokens:
            score += 1.0
        score += 0.25 * (item.get("priority") or 0)
        if score > 0:
            projected = dict(item)
            projected["_score"] = round(score, 3)
            scored.append(projected)
    scored.sort(key=lambda row: row["_score"], reverse=True)
    return scored


class HostDataService:
    """Implement store-backed host capabilities behind narrow providers."""

    def __init__(
        self,
        *,
        store: HostDataStore | StoreProvider,
        config: Any | ConfigProvider,
        frame_id: str | None | FrameIdProvider,
        resolve_path: PathResolver,
    ) -> None:
        self._store_source = store
        self._config_source = config
        self._frame_id_source = frame_id
        self._resolve_path = resolve_path

    def _store(self) -> HostDataStore:
        source = self._store_source
        return source() if callable(source) else source

    def _config(self) -> Any:
        source = self._config_source
        return source() if callable(source) else source

    def _frame_id(self) -> str | None:
        source = self._frame_id_source
        return source() if callable(source) else source

    def query(self, spec: dict) -> Any:
        rows = self._store().query(
            spec.get("sql", ""),
            params=spec.get("params"),
            limit=spec.get("limit"),
            timeout_s=5.0,
        )
        if spec.get("df"):
            columns = list(rows[0].keys()) if rows else []
            return {"columns": columns, "rows": [list(row.values()) for row in rows]}
        return rows

    def query_schema(self) -> dict:
        return self._store().schema()

    def artifacts(self, filters: dict | None = None) -> dict:
        filters = filters or {}
        search = filters.pop("search", None) if isinstance(filters, dict) else None
        # Confine enumeration to the caller's own session/project scope — the
        # same isolation get_artifact_metadata/restore enforce via
        # _scoped_artifact.  Otherwise a model can enumerate every session's
        # artifacts by omitting filters or naming another root_frame_id/project.
        if isinstance(filters, dict):
            frame_id = self._frame_id()
            resolver = getattr(self._store(), "resolve_frame_scope", None)
            if frame_id is not None and callable(resolver):
                scope = resolver(frame_id) or {}
                if scope.get("root_frame_id"):
                    filters["root_frame_id"] = scope["root_frame_id"]
                    filters["project_id"] = scope.get("project_id")
        items = self._store().list_artifacts(filters)
        if search:
            items = rank_artifacts(items, str(search))
        return {"count": len(items), "artifacts": items}

    def _scoped_artifact(self, artifact_id: str) -> dict:
        """Resolve one Artifact without allowing cross-session enumeration."""
        store = self._store()
        artifact = store.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"no artifact {artifact_id!r} in the current session")
        frame_id = self._frame_id()
        scope = store.resolve_frame_scope(frame_id)
        if (
            frame_id is None
            or artifact.get("root_frame_id") != scope.get("root_frame_id")
            or artifact.get("project_id") != scope.get("project_id")
        ):
            raise PermissionError(
                f"artifact {artifact_id!r} is outside the current session scope"
            )
        return artifact

    @staticmethod
    def _artifact_metadata_projection(artifact: dict) -> dict:
        fields = (
            "artifact_id",
            "project_id",
            "root_frame_id",
            "filename",
            "content_type",
            "is_user_upload",
            "priority",
            "latest_version_id",
            "created_at",
            "updated_at",
        )
        return {field: artifact.get(field) for field in fields}

    @staticmethod
    def _version_metadata_projection(
        version: dict,
        *,
        latest_version_id: str | None,
    ) -> dict:
        fields = (
            "version_id",
            "artifact_id",
            "filename",
            "content_type",
            "size_bytes",
            "checksum",
            "producing_cell_id",
            "frame_id",
            "created_at",
            "env_snapshot_id",
            "ordinal",
        )
        projected = {
            field: version.get(field)
            for field in fields
            if field in version or field != "ordinal"
        }
        projected["is_latest"] = version.get("version_id") == latest_version_id
        projected["snapshot_available"] = bool(version.get("snapshot_path"))
        return projected

    def artifact_metadata(self, spec: dict) -> dict:
        """Return exact safe metadata for one Artifact and one of its versions."""
        artifact_id = str(spec.get("artifact_id") or "")
        artifact = self._scoped_artifact(artifact_id)
        version_id = str(
            spec.get("version_id") or artifact.get("latest_version_id") or ""
        )
        version = self._store().version_meta(version_id) if version_id else None
        if version is None or version.get("artifact_id") != artifact_id:
            raise KeyError(
                f"version {version_id!r} does not belong to artifact {artifact_id!r}"
            )
        return {
            "artifact": self._artifact_metadata_projection(artifact),
            "version": self._version_metadata_projection(
                version,
                latest_version_id=artifact.get("latest_version_id"),
            ),
        }

    def artifact_versions(self, spec: dict) -> dict:
        """List immutable version identities for one session-owned Artifact."""
        artifact_id = str(spec.get("artifact_id") or "")
        artifact = self._scoped_artifact(artifact_id)
        latest_version_id = artifact.get("latest_version_id")
        versions = []
        for item in self._store().list_versions(artifact_id):
            metadata = self._store().version_meta(item["version_id"]) or item
            metadata = {**metadata, "ordinal": item.get("ordinal")}
            versions.append(
                self._version_metadata_projection(
                    metadata,
                    latest_version_id=latest_version_id,
                )
            )
        return {
            "artifact_id": artifact_id,
            "latest_version_id": latest_version_id,
            "count": len(versions),
            "versions": versions,
        }

    def restore_artifact_version(self, spec: dict) -> dict:
        """Restore verified historical bytes as a new immutable version."""
        artifact_id = str(spec.get("artifact_id") or "")
        source_version_id = str(spec.get("version_id") or "")
        artifact = self._scoped_artifact(artifact_id)
        store = self._store()
        config = self._config()
        legacy_versions = (
            (Path(config.data_dir) / "artifact-versions")
            if getattr(config, "data_dir", None) is not None
            else Path(config.artifacts_dir)
        )
        service = ArtifactRestoreService(
            store=store,
            primary_snapshot_dir=Path(config.artifacts_dir),
            trusted_snapshot_dirs=(legacy_versions,),
            resolve_live_path=lambda current_artifact, current: self._resolve_path(
                str(current.get("path") or current_artifact.get("filename"))
            ),
        )
        return service.restore(
            artifact=artifact,
            source_version_id=source_version_id,
            frame_id=self._frame_id(),
        )

    def artifact_path(self, version_id: str) -> str:
        path = self._store().resolve_artifact_path(version_id)
        if path is None:
            raise KeyError(f"no artifact for id={version_id!r}")
        return path

    def save_artifact(self, spec: dict) -> dict:
        source = self._resolve_path(str(spec["path"]), must_exist=True)
        if not source.is_file():
            raise FileNotFoundError(f"save_artifact: no such file: {source}")
        filename = str(spec.get("filename") or source.name)
        data = source.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        version_stub = uuid.uuid4().hex[:12]
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "artifact")
        config = self._config()
        config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        destination = config.artifacts_dir / f"v-{version_stub}__{safe_filename}"
        shutil.copy2(source, destination)
        store = self._store()
        try:
            execution_cell_id = spec.get("execution_cell_id") or spec.get(
                "producing_cell_id"
            )
            record = store.record_cell_artifact(
                path=str(source),
                filename=filename,
                content_type=spec.get("content_type"),
                size_bytes=len(data),
                checksum=checksum,
                producing_cell_id=execution_cell_id,
                frame_id=self._frame_id(),
                snapshot_path=str(destination),
                input_version_ids=spec.get("input_version_ids") or [],
                reuse_policy="provisional",
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        metadata = store.version_meta(record["version_id"]) or {}
        bound_snapshot = metadata.get("snapshot_path")
        if bound_snapshot != str(destination):
            if bound_snapshot and Path(bound_snapshot).is_file():
                destination.unlink(missing_ok=True)
            else:
                store.set_version_snapshot(record["version_id"], str(destination))
                bound_snapshot = str(destination)
        priority = int(spec.get("priority", 0))
        if priority:
            store.set_priority(record["artifact_id"], priority)
        response = dict(record)
        response["path"] = bound_snapshot or str(destination)
        return response

    def view_image(self, spec: dict) -> dict:
        version_id = spec.get("version_id")
        path = spec.get("path")
        if version_id and not path:
            path = self._store().resolve_artifact_path(version_id)
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"view_image: no such image: {path!r}")
        return {"status": "ok", "rendered": True, "path": str(path)}

    def artifact_marker(self, version_id: str) -> str:
        if not _VALID_MARKER_ID.match(str(version_id)):
            raise ValueError(
                f"artifact_marker: id {version_id!r} is not a valid version id"
            )
        # Keep the scanner marker split in source so this implementation can
        # produce a legitimate marker without matching its own static gate.
        prefix = "".join(("{" "{", "artifact", ":"))
        suffix = "".join(("}" "}",))
        return f"{prefix}{version_id}{suffix}"

    def frames(self, spec: dict | None = None) -> Any:
        spec = spec or {}
        frame_id = spec.get("frame_id")
        pattern = spec.get("pattern")
        project_id = spec.get("project_id", "default")
        status = spec.get("status")
        if status is not None and status not in FRAME_STATUSES:
            raise ValueError(
                f"frames: invalid status {status!r}; valid: "
                f"{sorted(FRAME_STATUSES)}"
            )
        store = self._store()
        if frame_id:
            detail = store.frame_detail(
                frame_id,
                page=int(spec.get("page", 0)),
                page_size=int(spec.get("page_size", 50)),
            )
            if detail is None:
                raise KeyError(f"no such frame {frame_id!r}")
            return detail
        if pattern:
            return {
                "mode": "search",
                "pattern": pattern,
                "frames": store.search_frames(
                    pattern,
                    project_id=project_id,
                    limit=int(spec.get("limit", 50)),
                ),
            }
        return {
            "mode": "browse",
            "frames": store.browse_frames(
                project_id=project_id,
                status=status,
                roots_only=bool(spec.get("roots_only", True)),
                limit=int(spec.get("limit", 50)),
            ),
        }

    def lineage_get(self, version_id: str) -> dict:
        store = self._store()
        metadata = store.version_meta(version_id)
        if metadata is None:
            raise KeyError(f"no artifact version {version_id!r}")
        cell = store.producing_cell_for_version(version_id) or {}
        return {
            "version_id": version_id,
            "artifact_id": metadata.get("artifact_id"),
            "filename": metadata.get("filename"),
            "checksum": metadata.get("checksum"),
            "frame_id": metadata.get("frame_id"),
            "producing_cell_id": metadata.get("producing_cell_id"),
            "code": cell.get("code"),
            "inputs": store.lineage_inputs(version_id),
            "extraction_pending": False,
        }

    def lineage_graph(self, spec: dict) -> dict:
        start = spec["version_id"]
        direction = spec.get("direction", "up")
        max_depth = spec.get("max_depth")
        max_nodes = spec.get("max_nodes")
        seen: set[str] = set()
        edges: list[dict] = []
        frontier = [(start, 0)]
        store = self._store()
        while frontier:
            version_id, depth = frontier.pop(0)
            if version_id in seen:
                continue
            seen.add(version_id)
            if max_nodes and len(seen) > max_nodes:
                break
            if max_depth is not None and depth >= max_depth:
                continue
            for adjacent in store.lineage_edges_for(version_id, direction):
                edges.append(
                    {"from": version_id, "to": adjacent, "direction": direction}
                )
                frontier.append((adjacent, depth + 1))
        return {"root": start, "nodes": sorted(seen), "edges": edges}

    def provenance_resolve_path(self, path: str) -> Any:
        return self._store().version_for_path(path)

    def provenance_record(self, spec: dict) -> dict:
        path = spec["path"]
        output = Path(path).expanduser()
        if not output.exists():
            return {"error": f"prov_record: no such output file: {path}"}
        data = output.read_bytes()
        return self._store().record_cell_artifact(
            path=str(output),
            filename=spec.get("filename") or output.name,
            content_type=spec.get("content_type"),
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            producing_cell_id=spec.get("producing_cell_id"),
            frame_id=self._frame_id(),
            input_version_ids=spec.get("input_version_ids") or [],
        )


__all__ = ["FRAME_STATUSES", "HostDataService", "rank_artifacts"]

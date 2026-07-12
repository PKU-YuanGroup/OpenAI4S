"""Project-level Timeline and lineage views across scientific sessions."""

from __future__ import annotations

from typing import Any, Protocol

from openai4s.server.action_timeline import ActionTimelineService


class GlobalViewStore(Protocol):
    def browse_frames(self, **filters: Any) -> list[dict]:
        ...

    def list_artifacts(self, filters: dict | None = None) -> list[dict]:
        ...

    def list_versions(self, artifact_id: str) -> list[dict]:
        ...

    def lineage_edges_for(self, version_id: str, direction: str) -> list[str]:
        ...


class GlobalResearchViewService:
    """Compose bounded project-wide read models without exposing raw payloads."""

    def __init__(
        self,
        store: GlobalViewStore,
        timeline: ActionTimelineService,
    ) -> None:
        self.store = store
        self.timeline = timeline

    def timeline_view(self, project_id: str, *, limit: int = 500) -> dict[str, Any]:
        project_id = self._project(project_id)
        limit = self._limit(limit, maximum=2000)
        frames = self.store.browse_frames(
            project_id=project_id,
            roots_only=True,
            limit=500,
        )
        groups: list[dict[str, Any]] = []
        for frame in frames:
            root_frame_id = str(frame.get("frame_id") or "")
            if not root_frame_id:
                continue
            projection = self.timeline.get(root_frame_id, limit=500)
            for group in projection["groups"]:
                groups.append(
                    {
                        **group,
                        "session": {
                            "root_frame_id": root_frame_id,
                            "name": self._text(
                                frame.get("name") or frame.get("task_summary"), 160
                            ),
                        },
                    }
                )
        groups.sort(
            key=lambda group: (
                int(group.get("created_at") or 0),
                str(group.get("root_frame_id") or ""),
                int(group.get("ordinal") or 0),
            )
        )
        total_count = len(groups)
        groups = groups[-limit:]
        return {
            "project_id": project_id,
            "groups": groups,
            "count": len(groups),
            "total_count": total_count,
            "truncated": total_count > len(groups),
            "session_count": len(frames),
        }

    def lineage_view(self, project_id: str, *, limit: int = 2000) -> dict[str, Any]:
        project_id = self._project(project_id)
        limit = self._limit(limit, maximum=5000)
        artifacts = self.store.list_artifacts({"project_id": project_id})
        nodes: list[dict[str, Any]] = []
        versions: dict[str, dict[str, Any]] = {}
        for artifact in artifacts:
            artifact_id = str(artifact.get("artifact_id") or "")
            if not artifact_id:
                continue
            for version in self.store.list_versions(artifact_id):
                version_id = str(version.get("version_id") or "")
                if not version_id or len(nodes) >= limit:
                    continue
                node = {
                    "id": version_id,
                    "kind": "artifact_version",
                    "artifact_id": artifact_id,
                    "version_id": version_id,
                    "filename": self._text(
                        version.get("filename") or artifact.get("filename"), 240
                    ),
                    "root_frame_id": artifact.get("root_frame_id"),
                    "producing_cell_id": version.get("producing_cell_id"),
                    "created_at": version.get("created_at"),
                    "latest": bool(version.get("is_latest")),
                }
                nodes.append(node)
                versions[version_id] = node
        edges: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for version_id, node in versions.items():
            for output_id in self.store.lineage_edges_for(version_id, "down"):
                output_id = str(output_id or "")
                if output_id not in versions:
                    continue
                key = (version_id, output_id, "artifact_lineage")
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": version_id, "to": output_id, "kind": key[2]})
            cell_id = str(node.get("producing_cell_id") or "")
            if cell_id:
                key = (f"cell:{cell_id}", version_id, "produced")
                if key not in seen:
                    seen.add(key)
                    edges.append({"from": key[0], "to": version_id, "kind": key[2]})
        cell_ids = sorted(
            {
                str(node["producing_cell_id"])
                for node in nodes
                if node.get("producing_cell_id")
            }
        )
        nodes = [
            *(
                {"id": f"cell:{cell_id}", "kind": "cell", "cell_id": cell_id}
                for cell_id in cell_ids
            ),
            *nodes,
        ]
        return {
            "project_id": project_id,
            "nodes": nodes,
            "edges": edges[: limit * 2],
            "artifact_count": len(artifacts),
            "version_count": len(versions),
            "truncated": len(versions) >= limit or len(edges) > limit * 2,
        }

    @staticmethod
    def _project(project_id: str) -> str:
        value = str(project_id or "").strip()
        if not value:
            raise ValueError("project_id is required")
        return value

    @staticmethod
    def _limit(value: int, *, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("limit must be a positive integer")
        return min(value, maximum)

    @staticmethod
    def _text(value: Any, limit: int) -> str | None:
        if value in (None, ""):
            return None
        text = str(value)
        return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["GlobalResearchViewService", "GlobalViewStore"]

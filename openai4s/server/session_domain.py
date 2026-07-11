"""Composition service for checkpointed, inspectable scientific sessions.

Gateway should depend on this narrow API instead of assembling repositories,
CAS paths, timeline projections, notebook bytes, and renderer metadata inside
route handlers.  The service is still infrastructure-neutral: workspace lookup
and optional event delivery are injected, while Store supplies durable ports.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Protocol

from openai4s.server.action_timeline import ActionTimelineService
from openai4s.server.notebook_export import NotebookExportService
from openai4s.server.recovery_control import RecoveryControlService
from openai4s.server.renderers import RendererRegistry
from openai4s.server.session_branching import SessionBranchingService
from openai4s.storage.snapshots import WorkspaceCAS

WorkspaceResolver = Callable[[str, str], str | Path]
DomainEventSink = Callable[[dict[str, Any]], None]


class SessionDomainStore(Protocol):
    # Snapshot repository facade.
    def ensure_session_branch(self, **fields: Any) -> dict: ...

    def create_session_checkpoint(self, **fields: Any) -> dict: ...

    def fork_session_branch(self, **fields: Any) -> dict: ...

    def get_session_checkpoint(self, checkpoint_id: str) -> dict | None: ...

    def list_session_checkpoints(self, root_frame_id: str, **filters: Any) -> list[dict]: ...

    def get_session_branch(self, branch_id: str) -> dict | None: ...

    def list_session_branches(self, root_frame_id: str) -> list[dict]: ...

    def record_snapshot_operation(self, **fields: Any) -> dict: ...

    def list_snapshot_operations(self, root_frame_id: str, **filters: Any) -> list[dict]: ...

    def append_recovery_event(self, **fields: Any) -> dict: ...

    def list_recovery_events(self, **filters: Any) -> list[dict]: ...

    # Existing read models.
    def get_frame(self, frame_id: str) -> dict | None: ...

    def message_count(self, root_frame_id: str) -> int: ...

    def cell_count(self, root_frame_id: str) -> int: ...

    def list_cells(self, root_frame_id: str) -> list[dict]: ...

    def list_action_groups(self, root_frame_id: str, **filters: Any) -> list[dict]: ...

    def list_execution_attempts(self, **filters: Any) -> list[dict]: ...

    def append_action_group(self, **fields: Any) -> dict: ...

    def append_action_event(self, **fields: Any) -> dict: ...

    def list_artifacts(self, filters: dict | None = None) -> list[dict]: ...

    def get_artifact(self, artifact_id: str) -> dict | None: ...

    def version_meta(self, version_id: str) -> dict | None: ...

    def list_kernel_generations(self, root_frame_id: str, **filters: Any) -> list[dict]: ...

    def latest_kernel_generation(
        self, root_frame_id: str, language: str, *, branch_id: str | None = None
    ) -> dict | None: ...

    def list_permission_rules_for_frame(self, **filters: Any) -> dict: ...

    def list_explicit_capability_states(self, *args: Any, **kwargs: Any) -> list[dict]: ...

    def list_plans(self, frame_id: str, *, limit: int = 50) -> list[dict]: ...

    def list_memories(self, project_id: str | None = None, block: str | None = None) -> list[dict]: ...


class _SnapshotFacade:
    """Translate Store's explicit facade names to SessionBranching ports."""

    def __init__(self, store: SessionDomainStore) -> None:
        self.store = store

    def create_checkpoint(self, **fields: Any) -> dict:
        return self.store.create_session_checkpoint(**fields)

    def fork_branch(self, **fields: Any) -> dict:
        return self.store.fork_session_branch(**fields)

    def get_checkpoint(self, checkpoint_id: str) -> dict | None:
        return self.store.get_session_checkpoint(checkpoint_id)

    def list_checkpoints(self, root_frame_id: str, **filters: Any) -> list[dict]:
        return self.store.list_session_checkpoints(root_frame_id, **filters)

    def get_branch(self, branch_id: str) -> dict | None:
        return self.store.get_session_branch(branch_id)

    def list_branches(self, root_frame_id: str) -> list[dict]:
        return self.store.list_session_branches(root_frame_id)

    def record_operation(self, **fields: Any) -> dict:
        return self.store.record_snapshot_operation(**fields)


class SessionDomainService:
    """One route-friendly API over immutable session domain components."""

    def __init__(
        self,
        store: SessionDomainStore,
        *,
        data_dir: str | Path,
        workspace: WorkspaceResolver,
        event_sink: DomainEventSink | None = None,
        renderer_registry: RendererRegistry | None = None,
    ) -> None:
        self.store = store
        self._workspace = workspace
        self._event_sink = event_sink or (lambda _event: None)
        self.cas = WorkspaceCAS(Path(data_dir) / "workspace-cas")
        self.branching = SessionBranchingService(
            _SnapshotFacade(store),
            self.cas,
            workspace=workspace,
            read_state=self._checkpoint_state,
            event_sink=self._record_domain_event,
        )
        self.recovery = RecoveryControlService(
            store,
            workspace_tree_exists=self._workspace_tree_exists,
        )
        self.timeline = ActionTimelineService(store)
        self.notebooks = NotebookExportService(store)
        self.renderers = renderer_registry or RendererRegistry()

    # Checkpoints and branches -------------------------------------------------
    def checkpoints(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        branch_id = branch_id or root_frame_id
        return {
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "checkpoints": self.store.list_session_checkpoints(
                root_frame_id,
                branch_id=branch_id,
                limit=limit,
            ),
        }

    def create_checkpoint(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        reason: str = "manual",
        expected_head: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict:
        return self.branching.create_checkpoint(
            root_frame_id,
            branch_id=branch_id,
            reason=reason,
            expected_head=expected_head,
            metadata=metadata,
        )

    def branches(self, root_frame_id: str) -> dict[str, Any]:
        # Pure projection: do not create the root branch from a GET. The first
        # checkpoint call owns that mutation; until then the UI must still be
        # able to offer "Create checkpoint".
        frame = self.store.get_frame(root_frame_id)
        if frame is None:
            raise KeyError(f"unknown session {root_frame_id!r}")
        if (frame.get("root_frame_id") or root_frame_id) != root_frame_id:
            raise ValueError("branch operations require a root frame")
        projection = self.branching.projection(root_frame_id)
        branches = projection.get("branches") or []
        checkpoints = [
            checkpoint
            for branch in branches
            for checkpoint in (branch.get("checkpoints") or ())
        ]
        has_checkpoint = bool(checkpoints)
        projection.update(
            {
                "current_branch_id": root_frame_id,
                "capabilities": {
                    "checkpoint": {"enabled": True, "reason": None},
                    "fork": {
                        "enabled": has_checkpoint,
                        "reason": (
                            None
                            if has_checkpoint
                            else "create a checkpoint before forking"
                        ),
                        "source": "checkpoint",
                        "fork_from_cell": False,
                        "fork_from_cell_reason": (
                            "fork-from-cell is not yet supported; create a "
                            "checkpoint at the desired boundary"
                        ),
                    },
                    "revert_preview": {
                        "enabled": has_checkpoint,
                        "reason": (
                            None
                            if has_checkpoint
                            else "create a checkpoint before previewing a revert"
                        ),
                    },
                    "revert": {
                        "enabled": has_checkpoint,
                        "reason": (
                            None
                            if has_checkpoint
                            else "create a checkpoint before reverting"
                        ),
                    },
                },
            }
        )
        return projection

    def fork_branch(
        self,
        root_frame_id: str,
        *,
        from_checkpoint_id: str,
        branch_id: str | None = None,
        name: str | None = None,
    ) -> dict:
        return self.branching.fork(
            root_frame_id,
            from_checkpoint_id=from_checkpoint_id,
            branch_id=branch_id,
            name=name,
        )

    def revert_preview(
        self,
        root_frame_id: str,
        *,
        target_checkpoint_id: str,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        return self.branching.preview_revert(
            root_frame_id,
            branch_id=branch_id,
            target_checkpoint_id=target_checkpoint_id,
        )

    def revert_apply(
        self,
        root_frame_id: str,
        *,
        target_checkpoint_id: str,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        return self.branching.revert_and_continue(
            root_frame_id,
            branch_id=branch_id,
            target_checkpoint_id=target_checkpoint_id,
        )

    def revert_undo(
        self,
        root_frame_id: str,
        *,
        revert_checkpoint_id: str,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        return self.branching.undo_revert(
            root_frame_id,
            branch_id=branch_id,
            revert_checkpoint_id=revert_checkpoint_id,
        )

    def revert_operations(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self.store.list_snapshot_operations(
            root_frame_id,
            branch_id=branch_id,
            kind="revert",
            limit=limit,
        )

    # Read projections ---------------------------------------------------------
    def recovery_status(self, root_frame_id: str, **filters: Any) -> dict[str, Any]:
        return self.recovery.status(root_frame_id, **filters)

    def recovery_actions(self, root_frame_id: str, **filters: Any) -> dict[str, Any]:
        return self.recovery.actions(root_frame_id, **filters)

    def action_timeline(self, root_frame_id: str, **filters: Any) -> dict[str, Any]:
        return self.timeline.get(root_frame_id, **filters)

    def notebook_export(
        self, root_frame_id: str, *, language: str | None = None
    ) -> dict[str, Any]:
        return self.notebooks.export(root_frame_id, language=language)

    def artifact_renderer(
        self,
        artifact_id: str,
        *,
        version_id: str | None = None,
        root_frame_id: str | None = None,
    ) -> dict[str, Any]:
        artifact = self.store.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"unknown artifact {artifact_id!r}")
        if root_frame_id is not None and artifact.get("root_frame_id") != root_frame_id:
            raise PermissionError("artifact belongs to another session")
        selected = dict(artifact)
        selected["artifact_id"] = artifact_id
        if version_id is not None:
            version = self.store.version_meta(version_id)
            if version is None or version.get("artifact_id") != artifact_id:
                raise KeyError(f"unknown version {version_id!r} for artifact")
            selected.update(version)
        else:
            selected["version_id"] = artifact.get("latest_version_id")
            if selected["version_id"]:
                selected.update(self.store.version_meta(selected["version_id"]) or {})
        descriptor = self.renderers.select(selected)
        descriptor["immutable"] = {
            "checksum": selected.get("checksum"),
            "size_bytes": selected.get("size_bytes"),
            "created_at": selected.get("created_at"),
        }
        return descriptor

    def renderer_catalog(self) -> list[dict[str, Any]]:
        return self.renderers.catalog()

    # Composition internals ----------------------------------------------------
    def _checkpoint_state(self, root_frame_id: str, branch_id: str) -> dict[str, Any]:
        frame = self.store.get_frame(root_frame_id)
        if frame is None:
            raise KeyError(f"unknown session {root_frame_id!r}")
        if (frame.get("root_frame_id") or root_frame_id) != root_frame_id:
            raise ValueError("checkpoint operations require a root frame")
        project_id = str(frame.get("project_id") or "default")
        groups = self.store.list_action_groups(
            root_frame_id,
            branch_id=branch_id,
            include_events=False,
        )
        artifacts = self.store.list_artifacts({"root_frame_id": root_frame_id})
        generations = self.store.list_kernel_generations(
            root_frame_id,
            branch_id=branch_id,
        )
        latest: dict[str, dict] = {}
        for generation in generations:
            language = str(generation.get("language") or "")
            if language and (
                language not in latest
                or int(generation.get("ordinal") or 0)
                >= int(latest[language].get("ordinal") or 0)
            ):
                latest[language] = generation
        generation_refs = {
            language: {
                key: generation.get(key)
                for key in (
                    "generation_id",
                    "environment_manifest_id",
                    "bootstrap_manifest_id",
                    "environment",
                    "bootstrap",
                    "state",
                )
            }
            for language, generation in latest.items()
        }
        capability_state = self._capability_state(project_id, root_frame_id)
        permission_state = self.store.list_permission_rules_for_frame(
            root_frame_id=root_frame_id,
            project_id=project_id,
        )
        plans = [
            {
                key: plan.get(key)
                for key in ("plan_id", "status", "updated_at", "artifact_id")
            }
            for plan in self.store.list_plans(root_frame_id)
        ]
        memories = [
            {
                "memory_id": item.get("memory_id"),
                "block": item.get("block"),
                "sha256": hashlib.sha256(
                    str(item.get("content") or "").encode("utf-8")
                ).hexdigest(),
            }
            for item in self.store.list_memories(project_id=project_id)
        ]
        artifact_versions = sorted(
            str(item["latest_version_id"])
            for item in artifacts
            if item.get("latest_version_id")
        )
        artifact_hashes = {
            str(item.get("filename") or item.get("artifact_id")): str(
                item.get("checksum") or ""
            )
            for item in artifacts
            if item.get("checksum")
        }
        return {
            "action_cursor": max(
                (
                    int(group["ordinal"])
                    for group in groups
                    if group.get("ordinal") is not None
                ),
                default=None,
            ),
            "message_cursor": self.store.message_count(root_frame_id),
            "cell_cursor": self.store.cell_count(root_frame_id),
            "artifact_versions": artifact_versions,
            "environment_pins": {
                "python": frame.get("runtime_env"),
                **{
                    language: (generation.get("environment") or {}).get(
                        "environment_name"
                    )
                    for language, generation in latest.items()
                    if isinstance(generation.get("environment"), Mapping)
                },
            },
            "generation_refs": generation_refs,
            "capability_state": capability_state,
            "permission_state": permission_state,
            # No Cell is silently promoted to replay-safe here. Runtime code may
            # add explicitly classified steps before this checkpoint is captured.
            "recovery_recipe": {
                "version": 1,
                "steps": [],
                "required_symbols": {},
                "artifact_hashes": artifact_hashes,
                "environment_requirements": {},
            },
            "metadata": {
                "project_id": project_id,
                "plans": plans,
                "memories": memories,
                "state_source": "canonical_store_projection",
            },
        }

    def _workspace_tree_exists(self, tree_id: str) -> bool:
        try:
            self.cas.get_tree(tree_id)
            return True
        except (KeyError, ValueError):
            return False

    def _capability_state(self, project_id: str, root_frame_id: str) -> dict[str, Any]:
        rows = self.store.list_explicit_capability_states()
        return {
            "version": 1,
            "states": [
                row
                for row in rows
                if row.get("scope") == "global"
                or (
                    row.get("scope") == "project"
                    and row.get("scope_id") == project_id
                )
                or (
                    row.get("scope") == "session"
                    and row.get("scope_id") == root_frame_id
                )
            ],
        }

    def _record_domain_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "session_event")
        public = {
            key: event.get(key)
            for key in (
                "type",
                "root_frame_id",
                "branch_id",
                "checkpoint_id",
                "from_checkpoint_id",
                "target_checkpoint_id",
                "operation_id",
                "reason",
                "ok",
                "requires_kernel_recovery",
                "undo_checkpoint_id",
            )
            if event.get(key) is not None
        }
        # The browser event bus and durable timeline share the same explicit
        # projection.  Domain service return values may contain full workspace
        # previews or checkpoint records, none of which belong on WebSocket.
        try:
            self._event_sink(dict(public))
        except Exception:  # noqa: BLE001 - durable state already committed
            pass
        root_frame_id = str(public.get("root_frame_id") or "")
        if not root_frame_id:
            return
        group = self.store.append_action_group(
            root_frame_id=root_frame_id,
            branch_id=str(public.get("branch_id") or root_frame_id),
            turn_id=f"domain-{uuid.uuid4().hex[:16]}",
            kind=_event_kind(event_type),
        )
        self.store.append_action_event(
            group_id=group["group_id"],
            type=("failed" if "conflict" in event_type else "completed"),
            canonical_arguments=public,
            result={"recorded": True, "event": event_type},
            side_effect_class=(
                "workspace_mutation" if "revert" in event_type else "metadata_write"
            ),
            resource_keys=[
                f"session:{root_frame_id}",
                f"branch:{public.get('branch_id') or root_frame_id}",
            ],
        )


def _event_kind(event_type: str) -> str:
    if "revert" in event_type:
        return "revert"
    if "branch" in event_type:
        return "branch"
    if "checkpoint" in event_type:
        return "checkpoint"
    return "system"


__all__ = ["SessionDomainService", "SessionDomainStore"]

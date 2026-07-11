"""Checkpoint, branch, revert-preview, and undo orchestration.

This service is intentionally independent from the HTTP gateway.  It combines
the append-only checkpoint repository with :class:`WorkspaceCAS`, while every
piece of live session state is supplied by small callbacks.  The gateway may
therefore expose the same behaviour to Web and CLI without moving filesystem
or branching algorithms into a route facade.

A revert never rewrites an old checkpoint.  It first records a checkpoint of
the current state (the undo target), applies a conflict-checked workspace
transition, then appends a new checkpoint whose recovery cursors point at the
selected historical state.  If external files changed after the current head,
the operation is recorded as ``conflict`` and no bytes are modified.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from openai4s.storage.snapshots import WorkspaceCAS


class SnapshotRepository(Protocol):
    def create_checkpoint(self, **fields: Any) -> dict[str, Any]: ...

    def fork_branch(self, **fields: Any) -> dict[str, Any]: ...

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None: ...

    def get_branch(self, branch_id: str) -> dict[str, Any] | None: ...

    def list_branches(self, root_frame_id: str) -> list[dict[str, Any]]: ...

    def list_checkpoints(
        self, root_frame_id: str, *, branch_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def record_operation(self, **fields: Any) -> dict[str, Any]: ...


StateReader = Callable[[str, str], Mapping[str, Any]]
WorkspaceResolver = Callable[[str, str], str | Path]
OperationSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class CheckpointRequest:
    root_frame_id: str
    branch_id: str
    reason: str
    expected_head: str | None = None
    metadata: Mapping[str, Any] | None = None


class SessionBranchingService:
    """Create immutable checkpoints and safe, append-only branch transitions."""

    def __init__(
        self,
        repository: SnapshotRepository,
        cas: WorkspaceCAS,
        *,
        workspace: WorkspaceResolver,
        read_state: StateReader,
        event_sink: OperationSink | None = None,
    ) -> None:
        self.repository = repository
        self.cas = cas
        self._workspace = workspace
        self._read_state = read_state
        self._event_sink = event_sink or (lambda _event: None)

    def create_checkpoint(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        reason: str = "manual",
        expected_head: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        branch_id = branch_id or root_frame_id
        request = CheckpointRequest(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            reason=reason,
            expected_head=expected_head,
            metadata=metadata,
        )
        return self._capture_checkpoint(request)

    def fork(
        self,
        root_frame_id: str,
        *,
        from_checkpoint_id: str,
        branch_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        source = self._checkpoint(root_frame_id, from_checkpoint_id)
        branch_id = branch_id or f"br-{uuid.uuid4().hex[:16]}"
        materialized = self._materialize_fork_workspace(
            root_frame_id,
            source_branch_id=str(source["branch_id"]),
            branch_id=branch_id,
            tree_id=source.get("workspace_tree_id"),
        )
        created = self.repository.fork_branch(
            root_frame_id=root_frame_id,
            from_checkpoint_id=source["checkpoint_id"],
            branch_id=branch_id,
            name=name,
        )
        created = {
            **created,
            "workspace_tree_id": source.get("workspace_tree_id"),
            **materialized,
        }
        self._emit(
            {
                "type": "branch_created",
                "root_frame_id": root_frame_id,
                "branch_id": created["branch_id"],
                "from_checkpoint_id": from_checkpoint_id,
            }
        )
        return created

    def _materialize_fork_workspace(
        self,
        root_frame_id: str,
        *,
        source_branch_id: str,
        branch_id: str,
        tree_id: str | None,
    ) -> dict[str, Any]:
        source = Path(self._workspace(root_frame_id, source_branch_id)).resolve()
        destination = Path(self._workspace(root_frame_id, branch_id)).resolve()
        if destination == source:
            return {
                "workspace_isolated": False,
                "workspace_materialized": False,
            }
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeError("fork workspace already exists and is not empty")
        destination.mkdir(parents=True, exist_ok=True)
        if not tree_id:
            return {
                "workspace_isolated": True,
                "workspace_materialized": False,
            }
        restored = self.cas.restore(tree_id, destination)
        if not restored.get("applied"):
            raise RuntimeError("fork workspace could not be materialized safely")
        return {
            "workspace_isolated": True,
            "workspace_materialized": True,
        }

    def preview_revert(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None,
        target_checkpoint_id: str,
    ) -> dict[str, Any]:
        branch_id = branch_id or root_frame_id
        branch = self._branch(root_frame_id, branch_id)
        target = self._checkpoint(root_frame_id, target_checkpoint_id)
        current = self._checkpoint(root_frame_id, branch.get("head_checkpoint_id"))
        workspace = self._workspace(root_frame_id, branch_id)

        target_tree = target.get("workspace_tree_id")
        current_tree = current.get("workspace_tree_id")
        if not target_tree:
            workspace_diff: dict[str, Any] = {
                "writes": [],
                "deletes": [],
                "conflicts": [],
                "unchanged": [],
                "preserved_untracked": [],
                "unavailable": "target checkpoint has no workspace tree",
            }
        else:
            workspace_diff = self.cas.preview_restore(
                target_tree,
                workspace,
                baseline_tree_id=current_tree,
            )

        return {
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "current_checkpoint_id": current["checkpoint_id"],
            "target_checkpoint_id": target["checkpoint_id"],
            "workspace": workspace_diff,
            "messages": self._cursor_diff(current, target, "message_cursor"),
            "actions": self._cursor_diff(current, target, "action_cursor"),
            "notebook": self._cursor_diff(current, target, "cell_cursor"),
            "artifacts": self._set_diff(
                current.get("artifact_versions"), target.get("artifact_versions")
            ),
            "environment": self._mapping_diff(
                current.get("environment_pins"), target.get("environment_pins")
            ),
            "capabilities": self._mapping_diff(
                current.get("capability_state"), target.get("capability_state")
            ),
            "permissions": self._mapping_diff(
                current.get("permission_state"), target.get("permission_state")
            ),
            "can_apply": bool(target_tree) and not workspace_diff.get("conflicts"),
        }

    def revert_and_continue(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None,
        target_checkpoint_id: str,
    ) -> dict[str, Any]:
        """Append an undo checkpoint, restore safely, then append the revert."""

        branch_id = branch_id or root_frame_id
        operation_id = f"so-{uuid.uuid4().hex[:16]}"
        preview = self.preview_revert(
            root_frame_id,
            branch_id=branch_id,
            target_checkpoint_id=target_checkpoint_id,
        )
        if not preview["can_apply"]:
            operation = self.repository.record_operation(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                kind="revert",
                source_checkpoint_id=preview["current_checkpoint_id"],
                target_checkpoint_id=target_checkpoint_id,
                status="conflict",
                preview=preview,
                error=(
                    "workspace conflicts require review"
                    if preview["workspace"].get("conflicts")
                    else preview["workspace"].get("unavailable")
                ),
                finished=True,
            )
            result = {"ok": False, "operation": operation, "preview": preview}
            self._emit(
                {
                    "type": "branch_revert_conflict",
                    "root_frame_id": root_frame_id,
                    "branch_id": branch_id,
                    "operation_id": operation_id,
                    "target_checkpoint_id": target_checkpoint_id,
                    "reason": operation.get("error"),
                }
            )
            return result

        current_id = preview["current_checkpoint_id"]
        # Capturing at operation time catches an edit that raced the preview.
        undo = self._capture_checkpoint(
            CheckpointRequest(
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                reason="before_revert",
                expected_head=current_id,
                metadata={
                    "operation_id": operation_id,
                    "undo_for_target": target_checkpoint_id,
                },
            )
        )
        target = self._checkpoint(root_frame_id, target_checkpoint_id)
        current = self._checkpoint(root_frame_id, current_id)
        workspace = self._workspace(root_frame_id, branch_id)
        race_preview = self.cas.preview_restore(
            target["workspace_tree_id"],
            workspace,
            baseline_tree_id=current.get("workspace_tree_id"),
        )
        if race_preview.get("conflicts"):
            operation = self.repository.record_operation(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                kind="revert",
                source_checkpoint_id=undo["checkpoint_id"],
                target_checkpoint_id=target_checkpoint_id,
                status="conflict",
                preview={**preview, "workspace_after_undo_capture": race_preview},
                error="workspace changed while preparing revert",
                finished=True,
            )
            result = {"ok": False, "operation": operation, "preview": preview}
            self._emit(
                {
                    "type": "branch_revert_conflict",
                    "root_frame_id": root_frame_id,
                    "branch_id": branch_id,
                    "operation_id": operation_id,
                    "target_checkpoint_id": target_checkpoint_id,
                    "reason": operation.get("error"),
                }
            )
            return result
        applied = self.cas.restore(
            target["workspace_tree_id"],
            workspace,
            # Compare against the branch head again inside ``restore``.  New
            # untracked files are preserved, while an edit to a managed file
            # between preview and apply becomes a conflict.
            baseline_tree_id=current.get("workspace_tree_id"),
        )
        if not applied.get("applied"):
            operation = self.repository.record_operation(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                kind="revert",
                source_checkpoint_id=undo["checkpoint_id"],
                target_checkpoint_id=target_checkpoint_id,
                status="conflict",
                preview={**preview, "workspace_after_undo_capture": applied},
                error="workspace changed while applying revert",
                finished=True,
            )
            result = {"ok": False, "operation": operation, "preview": preview}
            self._emit(
                {
                    "type": "branch_revert_conflict",
                    "root_frame_id": root_frame_id,
                    "branch_id": branch_id,
                    "operation_id": operation_id,
                    "target_checkpoint_id": target_checkpoint_id,
                    "reason": operation.get("error"),
                }
            )
            return result

        reverted = self.repository.create_checkpoint(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            reason="revert_continue",
            workspace_tree_id=target["workspace_tree_id"],
            action_cursor=target.get("action_cursor"),
            message_cursor=target.get("message_cursor"),
            cell_cursor=target.get("cell_cursor"),
            artifact_versions=target.get("artifact_versions") or [],
            environment_pins=target.get("environment_pins") or {},
            generation_refs=target.get("generation_refs") or {},
            capability_state=target.get("capability_state") or {},
            permission_state=target.get("permission_state") or {},
            recovery_recipe=target.get("recovery_recipe") or {},
            metadata={
                "operation_id": operation_id,
                "reverted_to": target_checkpoint_id,
                "undo_checkpoint_id": undo["checkpoint_id"],
                "requires_kernel_recovery": True,
            },
            expected_head=undo["checkpoint_id"],
        )
        operation = self.repository.record_operation(
            operation_id=operation_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            kind="revert",
            source_checkpoint_id=undo["checkpoint_id"],
            target_checkpoint_id=target_checkpoint_id,
            status="completed",
            preview={**preview, "applied_workspace": applied},
            finished=True,
        )
        result = {
            "ok": True,
            "operation": operation,
            "checkpoint": reverted,
            "undo_checkpoint_id": undo["checkpoint_id"],
            "requires_kernel_recovery": True,
        }
        # Emit only the stable public identity of the mutation.  ``result``
        # intentionally contains the full operation/preview/checkpoint records
        # for the direct HTTP response; those records can include workspace
        # diffs and must never be copied wholesale onto the session event bus.
        self._emit(
            {
                "type": "branch_reverted",
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "operation_id": operation_id,
                "target_checkpoint_id": target_checkpoint_id,
                "checkpoint_id": reverted["checkpoint_id"],
                "undo_checkpoint_id": undo["checkpoint_id"],
                "ok": True,
                "requires_kernel_recovery": True,
            }
        )
        return result

    def undo_revert(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None,
        revert_checkpoint_id: str,
    ) -> dict[str, Any]:
        checkpoint = self._checkpoint(root_frame_id, revert_checkpoint_id)
        metadata = checkpoint.get("metadata") or {}
        undo_id = metadata.get("undo_checkpoint_id")
        if not isinstance(undo_id, str) or not undo_id:
            raise ValueError("checkpoint is not an undoable revert")
        return self.revert_and_continue(
            root_frame_id,
            branch_id=branch_id,
            target_checkpoint_id=undo_id,
        )

    def projection(self, root_frame_id: str) -> dict[str, Any]:
        branches = self.repository.list_branches(root_frame_id)
        return {
            "root_frame_id": root_frame_id,
            "branches": [
                {
                    **branch,
                    "checkpoints": self.repository.list_checkpoints(
                        root_frame_id,
                        branch_id=branch["branch_id"],
                        limit=100,
                    ),
                }
                for branch in branches
            ],
        }

    def _capture_checkpoint(self, request: CheckpointRequest) -> dict[str, Any]:
        state = dict(self._read_state(request.root_frame_id, request.branch_id) or {})
        workspace = self._workspace(request.root_frame_id, request.branch_id)
        tree = self.cas.capture(workspace, exclude=state.get("snapshot_exclude") or ())
        metadata = dict(state.get("metadata") or {})
        metadata.update(dict(request.metadata or {}))
        if tree.get("skipped"):
            metadata["workspace_skipped"] = tree["skipped"]
        recovery_recipe = self._checkpoint_recipe(
            state.get("recovery_recipe"),
            tree_id=tree["tree_id"],
            artifact_versions=state.get("artifact_versions") or [],
        )
        checkpoint = self.repository.create_checkpoint(
            root_frame_id=request.root_frame_id,
            branch_id=request.branch_id,
            reason=request.reason,
            workspace_tree_id=tree["tree_id"],
            action_cursor=state.get("action_cursor"),
            message_cursor=state.get("message_cursor"),
            cell_cursor=state.get("cell_cursor"),
            artifact_versions=state.get("artifact_versions") or [],
            environment_pins=state.get("environment_pins") or {},
            generation_refs=state.get("generation_refs") or {},
            capability_state=state.get("capability_state") or {},
            permission_state=state.get("permission_state") or {},
            recovery_recipe=recovery_recipe,
            metadata=metadata,
            expected_head=request.expected_head,
        )
        self._emit(
            {
                "type": "checkpoint_created",
                "root_frame_id": request.root_frame_id,
                "branch_id": request.branch_id,
                "checkpoint_id": checkpoint["checkpoint_id"],
                "reason": request.reason,
            }
        )
        return checkpoint

    @staticmethod
    def _checkpoint_recipe(
        value: Any,
        *,
        tree_id: str,
        artifact_versions: list[Any],
    ) -> dict[str, Any]:
        """Bind hydration inputs to this exact immutable checkpoint.

        Existing replay steps are retained but never upgraded to replay-safe;
        the recovery orchestrator still applies its own fail-closed classifier.
        """

        recipe = dict(value) if isinstance(value, Mapping) else {}
        original_steps = [
            dict(step)
            for step in (recipe.get("steps") or ())
            if isinstance(step, Mapping)
            and step.get("kind") not in {"hydrate_workspace", "hydrate_artifact"}
        ]
        hydration = [
            {
                "kind": "hydrate_workspace",
                "payload": {"tree_id": tree_id},
                "replay_policy": "never",
            }
        ]
        hydration.extend(
            {
                "kind": "hydrate_artifact",
                "payload": {"version_id": str(version_id)},
                "replay_policy": "never",
            }
            for version_id in artifact_versions
        )
        recipe["version"] = 1
        recipe["steps"] = hydration + original_steps
        recipe.setdefault("required_symbols", {})
        recipe.setdefault("artifact_hashes", {})
        recipe.setdefault("environment_requirements", {})
        return recipe

    def _branch(self, root_frame_id: str, branch_id: str) -> dict[str, Any]:
        branch = self.repository.get_branch(branch_id)
        if branch is None or branch.get("root_frame_id") != root_frame_id:
            raise KeyError(f"unknown branch {branch_id!r} for {root_frame_id!r}")
        return branch

    def _checkpoint(
        self, root_frame_id: str, checkpoint_id: str | None
    ) -> dict[str, Any]:
        if not checkpoint_id:
            raise ValueError("branch has no checkpoint")
        checkpoint = self.repository.get_checkpoint(checkpoint_id)
        if checkpoint is None or checkpoint.get("root_frame_id") != root_frame_id:
            raise KeyError(
                f"unknown checkpoint {checkpoint_id!r} for {root_frame_id!r}"
            )
        return checkpoint

    @staticmethod
    def _cursor_diff(
        current: Mapping[str, Any], target: Mapping[str, Any], key: str
    ) -> dict[str, Any]:
        before = current.get(key)
        after = target.get(key)
        return {
            "from": before,
            "to": after,
            "delta": (
                after - before
                if isinstance(before, int) and isinstance(after, int)
                else None
            ),
        }

    @staticmethod
    def _set_diff(current: Any, target: Any) -> dict[str, list[Any]]:
        before = {str(value) for value in (current or [])}
        after = {str(value) for value in (target or [])}
        return {"added": sorted(after - before), "removed": sorted(before - after)}

    @staticmethod
    def _mapping_diff(current: Any, target: Any) -> dict[str, Any]:
        before = dict(current) if isinstance(current, Mapping) else {}
        after = dict(target) if isinstance(target, Mapping) else {}
        changed = {
            key: {"from": before.get(key), "to": after.get(key)}
            for key in sorted(set(before) | set(after))
            if before.get(key) != after.get(key)
        }
        return {"changed": changed, "has_changes": bool(changed)}

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self._event_sink(event)
        except Exception:  # noqa: BLE001 — projection cannot roll back persistence
            pass


__all__ = [
    "CheckpointRequest",
    "SessionBranchingService",
    "SnapshotRepository",
]

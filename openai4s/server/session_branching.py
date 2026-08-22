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

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from openai4s.storage.snapshots import WorkspaceCAS, revert_recovery_setting_key


class SnapshotRepository(Protocol):
    def create_checkpoint(self, **fields: Any) -> dict[str, Any]: ...

    def fork_branch(self, **fields: Any) -> dict[str, Any]: ...

    def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None: ...

    def get_checkpoint_for_source(
        self, root_frame_id: str, *, source_kind: str, source_id: str
    ) -> dict[str, Any] | None: ...

    def get_branch(self, branch_id: str) -> dict[str, Any] | None: ...

    def list_branches(self, root_frame_id: str) -> list[dict[str, Any]]: ...

    def list_checkpoints(
        self, root_frame_id: str, *, branch_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def record_operation(self, **fields: Any) -> dict[str, Any]: ...

    def get_operation(self, operation_id: str) -> dict[str, Any] | None: ...


StateReader = Callable[[str, str], Mapping[str, Any]]
WorkspaceResolver = Callable[[str, str], str | Path]
OperationSink = Callable[[dict[str, Any]], None]
SettingReader = Callable[[str], str | None]
SettingWriter = Callable[[str, str], None]
SettingDeleter = Callable[[str], None]
SettingCompareDeleter = Callable[[str, str], bool]


class RevertRecoveryRequiredError(RuntimeError):
    """A revert could not be completed or safely compensated."""


@dataclass(frozen=True)
class CheckpointRequest:
    root_frame_id: str
    branch_id: str
    reason: str
    expected_head: str | None = None
    metadata: Mapping[str, Any] | None = None
    source_kind: str | None = None
    source_id: str | None = None
    internal: bool = False


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
        get_setting: SettingReader | None = None,
        set_setting: SettingWriter | None = None,
        delete_setting: SettingDeleter | None = None,
        delete_setting_if_value: SettingCompareDeleter | None = None,
        recovery_event_sink: OperationSink | None = None,
        defer_revert_unlock: bool = False,
    ) -> None:
        self.repository = repository
        self.cas = cas
        self._workspace = workspace
        self._read_state = read_state
        self._event_sink = event_sink or (lambda _event: None)
        self._get_setting = get_setting
        self._set_setting = set_setting
        self._delete_setting = delete_setting
        self._delete_setting_if_value = delete_setting_if_value
        self._recovery_event_sink = recovery_event_sink
        self._defer_revert_unlock = bool(defer_revert_unlock)
        self._volatile_revert_state: dict[str, str] = {}
        self._revert_reconcile_lock = threading.RLock()

    def create_checkpoint(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        reason: str = "manual",
        expected_head: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        internal: bool = False,
    ) -> dict[str, Any]:
        branch_id = branch_id or root_frame_id
        request = CheckpointRequest(
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            reason=reason,
            expected_head=expected_head,
            metadata=metadata,
            source_kind=source_kind,
            source_id=source_id,
            internal=internal,
        )
        return self._capture_checkpoint(request)

    def fork(
        self,
        root_frame_id: str,
        *,
        from_checkpoint_id: str,
        branch_id: str | None = None,
        name: str | None = None,
        source_kind: str = "checkpoint",
        source_id: str | None = None,
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
                "source_kind": source_kind,
                "source_id": source_id or from_checkpoint_id,
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
            raise RuntimeError("fork workspace must be isolated from its source")
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
            "auto_mode_events": self._cursor_diff(current, target, "auto_event_cursor"),
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
        preparing_state = {
            "schema_version": 1,
            "state": "preparing",
            "operation_id": operation_id,
            "branch_id": branch_id,
            "current_checkpoint_id": current_id,
            "target_checkpoint_id": target_checkpoint_id,
        }
        # Publish the barrier before the undo checkpoint. If the process dies
        # between those commits, startup/admission still sees an unfinished
        # revert rather than treating the new head as ordinary history.
        self._set_revert_recovery_state(root_frame_id, preparing_state)
        try:
            self._record_revert_recovery(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                status="started",
                detail=preparing_state,
            )
        except Exception as error:
            raise RevertRecoveryRequiredError(
                "revert audit intent could not be recorded; the Session is locked"
            ) from error
        # Capturing at operation time catches an edit that raced the preview.
        try:
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
                    internal=True,
                )
            )
        except Exception as error:
            try:
                self._record_revert_recovery(
                    operation_id=operation_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    status="cancelled",
                    detail={
                        "target_checkpoint_id": target_checkpoint_id,
                        "workspace_mutated": False,
                        "error": self._error_summary(error),
                    },
                )
                self._clear_revert_recovery_state(
                    root_frame_id, operation_id=operation_id
                )
            except Exception as audit_error:
                raise RevertRecoveryRequiredError(
                    "revert preparation failed without a durable terminal audit; "
                    "the Session is locked"
                ) from audit_error
            raise
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
            self._record_revert_recovery(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                status="cancelled",
                detail={
                    "target_checkpoint_id": target_checkpoint_id,
                    "undo_checkpoint_id": undo["checkpoint_id"],
                    "reason": "workspace_changed_during_prepare",
                },
            )
            self._clear_revert_recovery_state(root_frame_id, operation_id=operation_id)
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
        revert_checkpoint_id = f"cp-{uuid.uuid4().hex[:16]}"
        recovery_state = {
            "schema_version": 1,
            "state": "reverting",
            "operation_id": operation_id,
            "branch_id": branch_id,
            "target_checkpoint_id": target_checkpoint_id,
            "undo_checkpoint_id": undo["checkpoint_id"],
            "revert_checkpoint_id": revert_checkpoint_id,
            "target_tree_id": target["workspace_tree_id"],
            "undo_tree_id": undo["workspace_tree_id"],
        }
        # Enrich the already-durable pre-undo marker with both recovery trees.
        self._set_revert_recovery_state(root_frame_id, recovery_state)

        try:
            applied = self.cas.restore(
                target["workspace_tree_id"],
                workspace,
                # Compare against the branch head again inside ``restore``. New
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
                self._record_revert_recovery(
                    operation_id=operation_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    status="cancelled",
                    detail={
                        "target_checkpoint_id": target_checkpoint_id,
                        "undo_checkpoint_id": undo["checkpoint_id"],
                        "reason": "workspace_conflict",
                    },
                )
                self._clear_revert_recovery_state(
                    root_frame_id, operation_id=operation_id
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
                checkpoint_id=revert_checkpoint_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                reason="revert_continue",
                workspace_tree_id=target["workspace_tree_id"],
                action_cursor=target.get("action_cursor"),
                message_cursor=target.get("message_cursor"),
                cell_cursor=target.get("cell_cursor"),
                auto_event_cursor=target.get("auto_event_cursor"),
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
                    # Public cursors describe the restored target; physical
                    # cursors say where append-only history resumes.
                    "history_projection": {
                        "version": 1,
                        "base_checkpoint_id": target_checkpoint_id,
                        "resume_cursors": {
                            key: undo.get(key)
                            for key in (
                                "action_cursor",
                                "message_cursor",
                                "cell_cursor",
                                "auto_event_cursor",
                            )
                        },
                    },
                },
                expected_head=undo["checkpoint_id"],
            )
        except Exception as error:
            # A connection wrapper can raise after SQLite committed but before
            # returning the row. Resolve that outcome from the preallocated
            # checkpoint identity; compensating a committed head would create
            # the inverse split-brain (undo bytes under target cursors).
            try:
                committed = self.repository.get_checkpoint(revert_checkpoint_id)
                authoritative_branch = self.repository.get_branch(branch_id)
            except Exception as outcome_error:
                try:
                    self._set_revert_recovery_state(
                        root_frame_id,
                        {
                            **recovery_state,
                            "state": "recovery_required",
                            "error": self._error_summary(error),
                            "outcome_error": self._error_summary(outcome_error),
                        },
                    )
                except Exception:
                    pass
                raise RevertRecoveryRequiredError(
                    "revert commit outcome is unknown; the Session is locked"
                ) from error
            if (
                committed is not None
                and authoritative_branch is not None
                and authoritative_branch.get("head_checkpoint_id")
                == revert_checkpoint_id
            ):
                reverted = committed
            elif (
                committed is None
                and authoritative_branch is not None
                and authoritative_branch.get("head_checkpoint_id")
                == undo["checkpoint_id"]
            ):
                self._compensate_revert_failure(
                    error=error,
                    operation_id=operation_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    target_checkpoint_id=target_checkpoint_id,
                    undo=undo,
                    target=target,
                    workspace=workspace,
                    preview=preview,
                )
                raise
            else:
                try:
                    self._set_revert_recovery_state(
                        root_frame_id,
                        {
                            **recovery_state,
                            "state": "recovery_required",
                            "error": self._error_summary(error),
                        },
                    )
                except Exception:
                    pass
                raise RevertRecoveryRequiredError(
                    "revert commit outcome is inconsistent; the Session is locked"
                ) from error

        # The revert checkpoint is the commit point: its transaction also
        # abandons the active Auto run. Audit publication after this point may
        # keep the write barrier engaged, but must never compensate a committed
        # branch head back to the undo tree.
        audit_error: Exception | None = None
        try:
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
            self._record_revert_recovery(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                status="completed",
                detail={
                    "target_checkpoint_id": target_checkpoint_id,
                    "undo_checkpoint_id": undo["checkpoint_id"],
                    "revert_checkpoint_id": reverted["checkpoint_id"],
                },
            )
        except Exception as error:
            audit_error = error
            operation = {
                "operation_id": operation_id,
                "status": "recovery_required",
                "error": self._error_summary(error),
            }
        result = {
            "ok": True,
            "operation": operation,
            "checkpoint": reverted,
            "undo_checkpoint_id": undo["checkpoint_id"],
            "requires_kernel_recovery": True,
            "explicit_recovery_required": audit_error is not None,
            "recovery_barrier_key": revert_recovery_setting_key(root_frame_id),
        }
        # Emit only the stable public identity of the mutation.  ``result``
        # intentionally contains the full operation/preview/checkpoint records
        # for the direct HTTP response; those records can include workspace
        # diffs and must never be copied wholesale onto the session event bus.
        domain_event = {
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
        result["domain_event"] = domain_event
        if not self._defer_revert_unlock and audit_error is None:
            self._clear_revert_recovery_state(root_frame_id, operation_id=operation_id)
            self._emit(domain_event)
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

    def reconcile_revert(self, root_frame_id: str) -> dict[str, Any]:
        """Resolve a crash-interrupted revert from durable head + CAS truth.

        A preparing marker predates all workspace mutation and can be cancelled.
        Once the marker carries undo/target trees, the authoritative branch head
        selects exactly one idempotent direction: compensate to undo, or finish
        the committed target. Unknown identities and third-party edits retain a
        fail-closed ``recovery_required`` barrier.
        """

        with self._revert_reconcile_lock:
            raw = self._revert_recovery_raw(root_frame_id)
            if raw is None:
                return {"resolved": True, "state": "none"}
            marker: Any = {}
            try:
                marker = json.loads(raw)
                if not isinstance(marker, Mapping):
                    raise ValueError("revert marker must be an object")
                if marker.get("schema_version") != 1:
                    raise ValueError("unsupported revert marker version")
                operation_id = self._required_marker_text(marker, "operation_id")
                branch_id = self._required_marker_text(marker, "branch_id")
                state = self._required_marker_text(marker, "state")
                branch = self._branch(root_frame_id, branch_id)
                head = self._checkpoint(
                    root_frame_id, str(branch.get("head_checkpoint_id") or "")
                )
                if state == "preparing":
                    return self._reconcile_preparing_revert(
                        root_frame_id=root_frame_id,
                        operation_id=operation_id,
                        branch_id=branch_id,
                        marker=marker,
                        head=head,
                    )
                if state not in {
                    "reverting",
                    "recovery_required",
                    "committed_reconciled",
                }:
                    raise ValueError("unknown revert recovery state")
                return self._reconcile_materialized_revert(
                    root_frame_id=root_frame_id,
                    operation_id=operation_id,
                    branch_id=branch_id,
                    marker=marker,
                    head=head,
                )
            except Exception as error:  # noqa: BLE001 - reconciliation fails closed
                return self._retain_revert_recovery(
                    root_frame_id,
                    marker if isinstance(marker, Mapping) else {},
                    error,
                )

    def _reconcile_preparing_revert(
        self,
        *,
        root_frame_id: str,
        operation_id: str,
        branch_id: str,
        marker: Mapping[str, Any],
        head: Mapping[str, Any],
    ) -> dict[str, Any]:
        current_id = self._required_marker_text(marker, "current_checkpoint_id")
        target_id = self._required_marker_text(marker, "target_checkpoint_id")
        self._checkpoint(root_frame_id, target_id)
        head_id = str(head.get("checkpoint_id") or "")
        if head_id != current_id:
            metadata = head.get("metadata") or {}
            if not (
                head.get("reason") == "before_revert"
                and isinstance(metadata, Mapping)
                and metadata.get("operation_id") == operation_id
                and metadata.get("undo_for_target") == target_id
            ):
                raise RuntimeError("preparing revert branch head is inconsistent")
        conflict = self._existing_revert_conflict(
            operation_id=operation_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            source_checkpoint_id=head_id,
            target_checkpoint_id=target_id,
        )
        if conflict is None:
            self._ensure_reconciled_operation(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                source_checkpoint_id=head_id,
                target_checkpoint_id=target_id,
                status="cancelled",
                outcome="restart_before_workspace_restore",
            )
        reason = (
            "restart_replayed_existing_conflict"
            if conflict is not None
            else "restart_before_workspace_restore"
        )
        self._record_revert_recovery(
            operation_id=operation_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            status="cancelled",
            detail={
                "target_checkpoint_id": target_id,
                "head_checkpoint_id": head_id,
                "reason": reason,
                "operation_status": (
                    "conflict" if conflict is not None else "cancelled"
                ),
            },
        )
        self._clear_revert_recovery_state(root_frame_id, operation_id=operation_id)
        return {
            "resolved": True,
            "state": "conflict" if conflict is not None else "cancelled",
            "operation_id": operation_id,
            "branch_id": branch_id,
            "checkpoint": dict(head),
        }

    def _reconcile_materialized_revert(
        self,
        *,
        root_frame_id: str,
        operation_id: str,
        branch_id: str,
        marker: Mapping[str, Any],
        head: Mapping[str, Any],
    ) -> dict[str, Any]:
        target_id = self._required_marker_text(marker, "target_checkpoint_id")
        undo_id = self._required_marker_text(marker, "undo_checkpoint_id")
        target_tree = self._required_marker_text(marker, "target_tree_id")
        undo_tree = self._required_marker_text(marker, "undo_tree_id")
        target = self._checkpoint(root_frame_id, target_id)
        undo = self._checkpoint(root_frame_id, undo_id)
        undo_metadata = undo.get("metadata") or {}
        if (
            str(target.get("workspace_tree_id") or "") != target_tree
            or str(undo.get("workspace_tree_id") or "") != undo_tree
            or undo.get("branch_id") != branch_id
            or undo.get("reason") != "before_revert"
            or not isinstance(undo_metadata, Mapping)
            or undo_metadata.get("operation_id") != operation_id
            or undo_metadata.get("undo_for_target") != target_id
        ):
            raise RuntimeError("revert recovery checkpoint binding is inconsistent")
        workspace = self._workspace(root_frame_id, branch_id)
        head_id = str(head.get("checkpoint_id") or "")
        if head_id == undo_id:
            # Both conflict exits persist their immutable operation before the
            # recovery audit and exact marker delete.  If either later step lost
            # its response, restart must replay that terminal publication rather
            # than reinterpret the rejected revert as a failed mutation and try
            # to rewrite a workspace that the conflict path intentionally left
            # untouched.
            conflict = self._existing_revert_conflict(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                source_checkpoint_id=undo_id,
                target_checkpoint_id=target_id,
            )
            if conflict is not None:
                self._record_revert_recovery(
                    operation_id=operation_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    status="cancelled",
                    detail={
                        "target_checkpoint_id": target_id,
                        "undo_checkpoint_id": undo_id,
                        "compensated": False,
                        "reason": "restart_replayed_existing_conflict",
                        "operation_status": "conflict",
                    },
                )
                self._clear_revert_recovery_state(
                    root_frame_id, operation_id=operation_id
                )
                return {
                    "resolved": True,
                    "state": "conflict",
                    "operation_id": operation_id,
                    "branch_id": branch_id,
                    "checkpoint": dict(undo),
                }
            restored = self.cas.restore(
                undo_tree,
                workspace,
                baseline_tree_id=target_tree,
            )
            if not restored.get("applied"):
                raise RuntimeError("undo reconciliation conflicts with workspace")
            self._ensure_reconciled_operation(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                source_checkpoint_id=undo_id,
                target_checkpoint_id=target_id,
                status="failed_compensated",
                outcome="restart_compensated_to_undo",
            )
            self._record_revert_recovery(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                status="cancelled",
                detail={
                    "target_checkpoint_id": target_id,
                    "undo_checkpoint_id": undo_id,
                    "compensated": True,
                    "reason": "restart_reconciliation",
                },
            )
            self._clear_revert_recovery_state(root_frame_id, operation_id=operation_id)
            return {
                "resolved": True,
                "state": "compensated",
                "operation_id": operation_id,
                "branch_id": branch_id,
                "checkpoint": dict(undo),
            }
        revert_id = self._required_marker_text(marker, "revert_checkpoint_id")
        if head_id != revert_id:
            raise RuntimeError("revert recovery branch head is inconsistent")
        reverted = self._checkpoint(root_frame_id, revert_id)
        reverted_metadata = reverted.get("metadata") or {}
        if (
            reverted.get("branch_id") != branch_id
            or str(reverted.get("workspace_tree_id") or "") != target_tree
            or not isinstance(reverted_metadata, Mapping)
            or reverted_metadata.get("operation_id") != operation_id
            or reverted_metadata.get("reverted_to") != target_id
            or reverted_metadata.get("undo_checkpoint_id") != undo_id
        ):
            raise RuntimeError("committed revert checkpoint binding is inconsistent")
        restored = self.cas.restore(
            target_tree,
            workspace,
            baseline_tree_id=undo_tree,
        )
        if not restored.get("applied"):
            raise RuntimeError("committed revert conflicts with workspace")
        self._ensure_reconciled_operation(
            operation_id=operation_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            source_checkpoint_id=undo_id,
            target_checkpoint_id=target_id,
            status="completed",
            outcome="restart_completed_committed_revert",
        )
        self._record_revert_recovery(
            operation_id=operation_id,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            status="completed",
            detail={
                "target_checkpoint_id": target_id,
                "undo_checkpoint_id": undo_id,
                "revert_checkpoint_id": revert_id,
                "reason": "restart_reconciliation",
            },
        )
        self._set_revert_recovery_state(
            root_frame_id,
            {**dict(marker), "state": "committed_reconciled"},
        )
        return {
            "resolved": True,
            "state": "committed",
            "operation_id": operation_id,
            "branch_id": branch_id,
            "checkpoint": dict(reverted),
            "domain_event": {
                "type": "branch_reverted",
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "operation_id": operation_id,
                "target_checkpoint_id": target_id,
                "checkpoint_id": revert_id,
                "undo_checkpoint_id": undo_id,
                "ok": True,
                "requires_kernel_recovery": True,
            },
        }

    def _ensure_reconciled_operation(
        self,
        *,
        operation_id: str,
        root_frame_id: str,
        branch_id: str,
        source_checkpoint_id: str,
        target_checkpoint_id: str,
        status: str,
        outcome: str,
    ) -> dict[str, Any]:
        existing = self.repository.get_operation(operation_id)
        if existing is not None:
            return self._validate_revert_operation(
                existing,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                source_checkpoint_id=source_checkpoint_id,
                target_checkpoint_id=target_checkpoint_id,
                statuses=(status,),
            )
        try:
            return self.repository.record_operation(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                kind="revert",
                source_checkpoint_id=source_checkpoint_id,
                target_checkpoint_id=target_checkpoint_id,
                status=status,
                preview={"reconciliation": {"outcome": outcome}},
                finished=True,
            )
        except Exception:
            # Treat an after-commit response loss as success only when the exact
            # immutable operation can now be re-read and validated.
            existing = self.repository.get_operation(operation_id)
            if existing is None:
                raise
            return self._validate_revert_operation(
                existing,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                source_checkpoint_id=source_checkpoint_id,
                target_checkpoint_id=target_checkpoint_id,
                statuses=(status,),
            )

    def _existing_revert_conflict(
        self,
        *,
        operation_id: str,
        root_frame_id: str,
        branch_id: str,
        source_checkpoint_id: str,
        target_checkpoint_id: str,
    ) -> dict[str, Any] | None:
        existing = self.repository.get_operation(operation_id)
        if existing is None or existing.get("status") != "conflict":
            return None
        return self._validate_revert_operation(
            existing,
            root_frame_id=root_frame_id,
            branch_id=branch_id,
            source_checkpoint_id=source_checkpoint_id,
            target_checkpoint_id=target_checkpoint_id,
            statuses=("conflict",),
        )

    @staticmethod
    def _validate_revert_operation(
        existing: Mapping[str, Any],
        *,
        root_frame_id: str,
        branch_id: str,
        source_checkpoint_id: str,
        target_checkpoint_id: str,
        statuses: tuple[str, ...],
    ) -> dict[str, Any]:
        if (
            any(
                existing.get(key) != value
                for key, value in (
                    ("root_frame_id", root_frame_id),
                    ("branch_id", branch_id),
                    ("kind", "revert"),
                    ("source_checkpoint_id", source_checkpoint_id),
                    ("target_checkpoint_id", target_checkpoint_id),
                )
            )
            or existing.get("status") not in statuses
            or existing.get("finished_at") is None
        ):
            raise RuntimeError("revert operation audit is inconsistent")
        return dict(existing)

    def _retain_revert_recovery(
        self,
        root_frame_id: str,
        marker: Mapping[str, Any],
        error: BaseException,
    ) -> dict[str, Any]:
        allowed = {
            key: marker.get(key)
            for key in (
                "schema_version",
                "operation_id",
                "branch_id",
                "current_checkpoint_id",
                "target_checkpoint_id",
                "undo_checkpoint_id",
                "revert_checkpoint_id",
                "target_tree_id",
                "undo_tree_id",
            )
            if marker.get(key) is not None
        }
        locked = {
            **allowed,
            "schema_version": 1,
            "state": "recovery_required",
            "error": self._error_summary(error),
        }
        try:
            self._set_revert_recovery_state(root_frame_id, locked)
        except Exception:
            pass
        operation_id = locked.get("operation_id")
        branch_id = locked.get("branch_id")
        if isinstance(operation_id, str) and isinstance(branch_id, str):
            try:
                self._record_revert_recovery(
                    operation_id=operation_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    status="failed",
                    detail=locked,
                )
            except Exception:
                pass
        return {
            "resolved": False,
            "state": "recovery_required",
            "error": locked["error"],
        }

    @staticmethod
    def _required_marker_text(marker: Mapping[str, Any], key: str) -> str:
        value = marker.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"revert marker missing {key}")
        return value

    def _capture_checkpoint(self, request: CheckpointRequest) -> dict[str, Any]:
        state = dict(self._read_state(request.root_frame_id, request.branch_id) or {})
        workspace = self._workspace(request.root_frame_id, request.branch_id)
        # The CAS lock spans both sides of the CAS -> SQLite publication
        # boundary.  Session deletion takes the same lock before refreshing
        # surviving checkpoint references, preventing an in-flight capture
        # from losing its tree between these two operations.
        with self.cas.locked():
            tree = self.cas.capture(
                workspace, exclude=state.get("snapshot_exclude") or ()
            )
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
                auto_event_cursor=state.get("auto_event_cursor"),
                artifact_versions=state.get("artifact_versions") or [],
                environment_pins=state.get("environment_pins") or {},
                generation_refs=state.get("generation_refs") or {},
                capability_state=state.get("capability_state") or {},
                permission_state=state.get("permission_state") or {},
                recovery_recipe=recovery_recipe,
                metadata=metadata,
                source_kind=request.source_kind,
                source_id=request.source_id,
                internal=request.internal,
                expected_head=request.expected_head,
            )
        # Cursor checkpoints are implementation history.  Their durable row is
        # the audit proof; emitting one Timeline group per Cell/message would
        # drown the scientific actions they protect.
        if not request.internal:
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

    def _set_revert_recovery_state(
        self, root_frame_id: str, value: Mapping[str, Any]
    ) -> None:
        key = revert_recovery_setting_key(root_frame_id)
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if self._set_setting is None:
            self._volatile_revert_state[key] = encoded
            return
        self._set_setting(key, encoded)

    def _revert_recovery_raw(self, root_frame_id: str) -> str | None:
        key = revert_recovery_setting_key(root_frame_id)
        if self._get_setting is None:
            return self._volatile_revert_state.get(key)
        return self._get_setting(key)

    def _clear_revert_recovery_state(
        self,
        root_frame_id: str,
        *,
        operation_id: str | None = None,
    ) -> None:
        key = revert_recovery_setting_key(root_frame_id)
        encoded = self._revert_recovery_raw(root_frame_id)
        if encoded is None:
            return
        if operation_id is not None:
            try:
                value = json.loads(encoded)
            except (TypeError, ValueError) as error:
                raise RevertRecoveryRequiredError(
                    "revert recovery marker is corrupt; the Session remains locked"
                ) from error
            if (
                not isinstance(value, Mapping)
                or value.get("operation_id") != operation_id
            ):
                raise RevertRecoveryRequiredError(
                    "revert recovery marker changed before unlock"
                )
        if self._delete_setting is None:
            if self._volatile_revert_state.get(key) != encoded:
                raise RevertRecoveryRequiredError(
                    "revert recovery marker changed before unlock"
                )
            self._volatile_revert_state.pop(key, None)
            return
        if self._delete_setting_if_value is not None:
            if not self._delete_setting_if_value(key, encoded):
                raise RevertRecoveryRequiredError(
                    "revert recovery marker changed before unlock"
                )
            return
        # Compatibility callbacks used by focused tests are protected by this
        # service's reconcile lock. Production Store supplies compare-delete.
        if self._get_setting is not None and self._get_setting(key) != encoded:
            raise RevertRecoveryRequiredError(
                "revert recovery marker changed before unlock"
            )
        self._delete_setting(key)

    def finalize_revert_unlock(self, root_frame_id: str, *, operation_id: str) -> None:
        """Release one exact committed revert after runtime invalidation."""

        with self._revert_reconcile_lock:
            self._clear_revert_recovery_state(root_frame_id, operation_id=operation_id)

    def release_revert_barrier_after_recovery(self, root_frame_id: str) -> bool:
        """Release the current barrier after a verified restore/retry pipeline."""

        with self._revert_reconcile_lock:
            raw = self._revert_recovery_raw(root_frame_id)
            if raw is None:
                return False
            try:
                marker = json.loads(raw)
            except (TypeError, ValueError) as error:
                raise RevertRecoveryRequiredError(
                    "revert recovery marker is corrupt; the Session remains locked"
                ) from error
            if not isinstance(marker, Mapping) or not marker.get("operation_id"):
                raise RevertRecoveryRequiredError(
                    "revert recovery marker is invalid; the Session remains locked"
                )
            self._clear_revert_recovery_state(
                root_frame_id, operation_id=str(marker["operation_id"])
            )
            return True

    def _record_revert_recovery(
        self,
        *,
        operation_id: str,
        root_frame_id: str,
        branch_id: str,
        status: str,
        detail: Mapping[str, Any],
    ) -> None:
        if self._recovery_event_sink is None:
            return
        self._recovery_event_sink(
            {
                "recovery_id": f"revert-{operation_id}",
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "phase": "revert_workspace",
                "status": status,
                "detail": dict(detail),
            }
        )

    @staticmethod
    def _error_summary(error: BaseException) -> str:
        message_digest = hashlib.sha256(str(error).encode("utf-8")).hexdigest()[:16]
        return f"{type(error).__name__}:{message_digest}"

    def _compensate_revert_failure(
        self,
        *,
        error: BaseException,
        operation_id: str,
        root_frame_id: str,
        branch_id: str,
        target_checkpoint_id: str,
        undo: Mapping[str, Any],
        target: Mapping[str, Any],
        workspace: str | Path,
        preview: Mapping[str, Any],
    ) -> None:
        """Restore the undo tree, or retain the durable write barrier."""

        undo_tree = str(undo.get("workspace_tree_id") or "")
        target_tree = str(target.get("workspace_tree_id") or "")
        failure = self._error_summary(error)
        try:
            compensated = self.cas.restore(
                undo_tree,
                workspace,
                baseline_tree_id=target_tree,
            )
            if not compensated.get("applied"):
                raise RuntimeError("undo-tree compensation reported conflicts")
            self.repository.record_operation(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                kind="revert",
                source_checkpoint_id=str(undo["checkpoint_id"]),
                target_checkpoint_id=target_checkpoint_id,
                status="failed_compensated",
                preview={
                    **dict(preview),
                    "compensation": {
                        "applied": True,
                        "target_tree_id": undo_tree,
                    },
                },
                error=failure,
                finished=True,
            )
            self._record_revert_recovery(
                operation_id=operation_id,
                root_frame_id=root_frame_id,
                branch_id=branch_id,
                status="cancelled",
                detail={
                    "target_checkpoint_id": target_checkpoint_id,
                    "undo_checkpoint_id": undo["checkpoint_id"],
                    "compensated": True,
                    "error": failure,
                },
            )
            # Clearing the barrier is itself part of the terminal publication.
            # If it fails, callers receive recovery-required and the existing
            # marker continues to deny writes.
            self._clear_revert_recovery_state(root_frame_id, operation_id=operation_id)
        except Exception as compensation_error:
            locked = {
                "schema_version": 1,
                "state": "recovery_required",
                "operation_id": operation_id,
                "branch_id": branch_id,
                "target_checkpoint_id": target_checkpoint_id,
                "undo_checkpoint_id": undo.get("checkpoint_id"),
                "target_tree_id": target_tree,
                "undo_tree_id": undo_tree,
                "error": failure,
                "compensation_error": self._error_summary(compensation_error),
            }
            try:
                self._set_revert_recovery_state(root_frame_id, locked)
            except Exception:
                # The original marker was written before any workspace byte;
                # never clear it merely because enriching its detail failed.
                pass
            try:
                self._record_revert_recovery(
                    operation_id=operation_id,
                    root_frame_id=root_frame_id,
                    branch_id=branch_id,
                    status="failed",
                    detail=locked,
                )
            except Exception:
                pass
            raise RevertRecoveryRequiredError(
                "workspace revert failed and could not be safely compensated; "
                "the Session is locked for recovery"
            ) from error

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self._event_sink(event)
        except Exception:  # noqa: BLE001 — projection cannot roll back persistence
            pass


__all__ = [
    "CheckpointRequest",
    "RevertRecoveryRequiredError",
    "SessionBranchingService",
    "SnapshotRepository",
]

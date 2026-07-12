"""Focused session-orchestration behavior for native control tools.

The model is always bound to the dispatcher's current root session.  It cannot
name an arbitrary ``root_frame_id`` and thereby inspect or mutate another
conversation.  Durable metadata comes from Store; filesystem-aware checkpoint,
fork, and revert-preview operations are delegated to the gateway-owned
``SessionDomainService`` when that runtime is attached.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol


class SessionControlStore(Protocol):
    """The narrow durable Store projection required by this service."""

    def resolve_frame_scope(self, frame_id: str | None, **kwargs: Any) -> dict:
        ...

    def get_frame(self, frame_id: str) -> dict | None:
        ...

    def list_session_branches(self, root_frame_id: str) -> list[dict]:
        ...

    def list_session_checkpoints(
        self, root_frame_id: str, **filters: Any
    ) -> list[dict]:
        ...

    def list_permission_requests(
        self, *, root_frame_id: str | None = None, state: str | None = None
    ) -> list[dict]:
        ...


class SessionDomain(Protocol):
    """Filesystem-aware session-domain API supplied by the Web runtime."""

    def branches(self, root_frame_id: str) -> dict[str, Any]:
        ...

    def create_checkpoint(self, root_frame_id: str, **options: Any) -> dict:
        ...

    def fork_branch(self, root_frame_id: str, **options: Any) -> dict:
        ...

    def revert_preview(self, root_frame_id: str, **options: Any) -> dict:
        ...

    def recovery_status(self, root_frame_id: str, **filters: Any) -> dict:
        ...


DomainProvider = Callable[[], SessionDomain | None]

_BRANCH_FIELDS = (
    "branch_id",
    "name",
    "head_checkpoint_id",
    "base_checkpoint_id",
    "created_at",
    "updated_at",
)
_CHECKPOINT_FIELDS = (
    "checkpoint_id",
    "branch_id",
    "reason",
    "action_cursor",
    "message_cursor",
    "cell_cursor",
    "source_kind",
    "source_id",
    "created_at",
)
_PERMISSION_FIELDS = (
    "decision_id",
    "tool",
    "target",
    "state",
    "side_effect_class",
    "resource_keys",
    "action_group_id",
    "action_id",
    "tool_call_id",
    "created_at",
    "expires_at",
)


class SessionControlService:
    """Safe current-session projection plus callback-driven mutations."""

    def __init__(
        self,
        store: SessionControlStore,
        *,
        frame_id: Callable[[], str | None],
        domain: DomainProvider | None = None,
    ) -> None:
        self.store = store
        self._frame_id = frame_id
        self._domain = domain or (lambda: None)

    def set_domain(self, domain: SessionDomain | None) -> None:
        """Attach the gateway's shared domain service without rebuilding Host."""

        self._domain = lambda: domain

    def status(self, spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return a compact, path-free projection of the current session."""

        limit = _bounded_limit((spec or {}).get("checkpoint_limit"), default=20)
        try:
            root, project_id, frame = self._current()
            domain = self._domain()
            if domain is not None:
                projection = domain.branches(root)
                raw_branches = projection.get("branches") or []
                capabilities = projection.get("capabilities") or {}
                current_branch_id = str(projection.get("current_branch_id") or root)
                recovery = domain.recovery_status(
                    root, branch_id=current_branch_id, limit=20
                )
            else:
                raw_branches = self.store.list_session_branches(root)
                capabilities = self._fallback_capabilities(bool(raw_branches))
                current_branch_id = root
                recovery = {"state": "unavailable"}
            checkpoints = self.store.list_session_checkpoints(root, limit=limit)
            permissions = self.store.list_permission_requests(
                root_frame_id=root,
                state="pending",
            )
            return {
                "root_frame_id": root,
                "project_id": project_id,
                "status": frame.get("status"),
                "name": frame.get("name"),
                "model": frame.get("model"),
                "current_branch_id": current_branch_id,
                "branches": [_select(item, _BRANCH_FIELDS) for item in raw_branches],
                "checkpoints": [
                    _select(item, _CHECKPOINT_FIELDS) for item in checkpoints[:limit]
                ],
                "checkpoint_count": len(checkpoints),
                "pending_permission_count": len(permissions),
                "recovery": {
                    "state": recovery.get("state"),
                    "recovery_id": (recovery.get("current") or {}).get("recovery_id"),
                    "updated_at": (recovery.get("current") or {}).get("updated_at"),
                },
                "capabilities": capabilities,
            }
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            return {"error": f"session status failed: {error}"}

    def create_checkpoint(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        try:
            root, _project_id, _frame = self._current()
            domain = self._required_domain()
            checkpoint = domain.create_checkpoint(
                root,
                reason=str(spec.get("reason") or "agent"),
                expected_head=(
                    str(spec["expected_head"]) if spec.get("expected_head") else None
                ),
            )
            return {
                "ok": True,
                "checkpoint": _select(checkpoint, _CHECKPOINT_FIELDS),
            }
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            return {"error": f"checkpoint creation failed: {error}"}

    def fork_session(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        try:
            root, _project_id, _frame = self._current()
            domain = self._required_domain()
            result = domain.fork_branch(
                root,
                from_checkpoint_id=_optional_string(spec, "from_checkpoint_id"),
                from_cell_id=_optional_string(spec, "from_cell_id"),
                from_message_id=_optional_string(spec, "from_message_id"),
                name=_optional_string(spec, "name"),
            )
            return {
                "ok": True,
                "branch": _select(
                    result,
                    (*_BRANCH_FIELDS, "from_checkpoint_id", "source_kind", "source_id"),
                ),
                "active": bool(result.get("active")),
                "view_only": bool(result.get("view_only", True)),
                "workspace_isolated": bool(result.get("workspace_isolated")),
                "workspace_materialized": bool(result.get("workspace_materialized")),
            }
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            return {"error": f"session fork failed: {error}"}

    def revert_preview(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        try:
            root, _project_id, _frame = self._current()
            domain = self._required_domain()
            return {
                "preview": domain.revert_preview(
                    root,
                    target_checkpoint_id=str(spec["checkpoint_id"]),
                )
            }
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            return {"error": f"revert preview failed: {error}"}

    def pending_permissions(
        self, spec: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """List identities and audit metadata, never stored approval inputs."""

        limit = _bounded_limit((spec or {}).get("limit"), default=50)
        try:
            root, _project_id, _frame = self._current()
            rows = self.store.list_permission_requests(
                root_frame_id=root,
                state="pending",
            )
            public = []
            for row in rows[:limit]:
                item = _select(row, _PERMISSION_FIELDS)
                payload = row.get("payload") or {}
                if isinstance(payload, Mapping):
                    item.update(
                        {
                            "kind": payload.get("kind"),
                            "title": payload.get("title"),
                            "sub_agent": bool(payload.get("sub_agent")),
                        }
                    )
                public.append(item)
            return {
                "root_frame_id": root,
                "count": len(rows),
                "pending": public,
                "truncated": len(rows) > limit,
            }
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            return {"error": f"pending permission lookup failed: {error}"}

    def _current(self) -> tuple[str, str, dict]:
        frame_id = str(self._frame_id() or "").strip()
        if not frame_id:
            raise RuntimeError("no current session is bound")
        scope = self.store.resolve_frame_scope(frame_id)
        root = str(scope.get("root_frame_id") or frame_id).strip()
        frame = self.store.get_frame(root)
        if frame is None:
            raise KeyError(f"unknown current session {root!r}")
        project_id = str(
            frame.get("project_id") or scope.get("project_id") or "default"
        )
        return root, project_id, frame

    def _required_domain(self) -> SessionDomain:
        domain = self._domain()
        if domain is None:
            raise RuntimeError(
                "filesystem-aware session orchestration is unavailable on this runtime"
            )
        return domain

    @staticmethod
    def _fallback_capabilities(has_checkpoint: bool) -> dict[str, Any]:
        reason = None if has_checkpoint else "create a checkpoint first"
        return {
            "checkpoint": {"enabled": False, "reason": "runtime is read-only"},
            "fork": {"enabled": False, "reason": reason or "runtime is read-only"},
            "revert_preview": {
                "enabled": False,
                "reason": reason or "runtime is read-only",
            },
        }


def _select(source: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields if field in source}


def _optional_string(spec: Mapping[str, Any], key: str) -> str | None:
    value = spec.get(key)
    return str(value) if value not in (None, "") else None


def _bounded_limit(value: Any, *, default: int) -> int:
    if value is None:
        return default
    return max(1, min(100, int(value)))


__all__ = ["SessionControlService", "SessionControlStore", "SessionDomain"]

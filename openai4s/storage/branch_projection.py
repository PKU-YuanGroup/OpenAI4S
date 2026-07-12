"""Branch-aware read projections over append-only session history.

Rows written after a fork or revert are never deleted.  A branch checkpoint
therefore defines a *logical* history which can differ from the physical rows
stored for that branch.  This module reconstructs that logical history from
immutable checkpoints and then appends rows written after the current head.

The projector is deliberately record-agnostic.  Action groups, visible
messages, and execution Cells supply only their local-row reader, record
position, and checkpoint cursor key.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, TypeVar


class BranchProjectionStore(Protocol):
    def get_session_branch(self, branch_id: str) -> dict[str, Any] | None:
        ...

    def get_session_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        ...


Record = TypeVar("Record", bound=Mapping[str, Any])
LocalReader = Callable[[str], Sequence[Record]]
PositionReader = Callable[[Record], int]
CursorNormalizer = Callable[[Any], int | None]


def inclusive_cursor(value: Any) -> int | None:
    """Normalize an inclusive ordinal/revision checkpoint cursor."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("checkpoint cursor must be an integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("checkpoint cursor must be non-negative")
    return parsed


def count_cursor(value: Any) -> int | None:
    """Convert a row-count boundary into the last included zero-based seq."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("checkpoint cursor must be an integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("checkpoint cursor must be non-negative")
    return parsed - 1


def project_branch_records(
    store: BranchProjectionStore,
    root_frame_id: str,
    branch_id: str,
    *,
    list_local: LocalReader[Record],
    record_position: PositionReader[Record],
    cursor_key: str,
    normalize_cursor: CursorNormalizer = inclusive_cursor,
) -> list[Record]:
    """Return one branch's inherited/reverted prefix plus current local tail.

    A normal checkpoint extends its parent with rows through its cursor.  A
    revert checkpoint instead projects the selected target checkpoint and
    records the physical pre-revert cursor from which later rows resume.  This
    keeps abandoned rows auditable while excluding them from provider/UI state.
    """

    root_frame_id = _required_text("root_frame_id", root_frame_id)
    branch_id = _required_text("branch_id", branch_id)
    local_cache: dict[str, list[Record]] = {}
    checkpoint_cache: dict[str, tuple[list[Record], int | None]] = {}
    get_branch = getattr(store, "get_session_branch", None)
    get_checkpoint = getattr(store, "get_session_checkpoint", None)

    def local(selected: str) -> list[Record]:
        rows = local_cache.get(selected)
        if rows is None:
            # Owning repositories already expose their canonical append order.
            # Preserve it so equal/legacy positions retain deterministic source
            # order instead of being silently reordered by this projection.
            rows = list(list_local(selected))
            local_cache[selected] = rows
        return rows

    def segment(
        selected: str,
        *,
        after: int | None,
        upto: int | None,
    ) -> list[Record]:
        return [
            row
            for row in local(selected)
            if (after is None or record_position(row) > after)
            and (upto is None or record_position(row) <= upto)
        ]

    def checkpoint_projection(
        checkpoint_id: str,
        visiting: frozenset[str],
    ) -> tuple[list[Record], int | None]:
        cached = checkpoint_cache.get(checkpoint_id)
        if cached is not None:
            return (list(cached[0]), cached[1])
        if checkpoint_id in visiting:
            raise ValueError("session checkpoint projection contains a cycle")
        checkpoint = get_checkpoint(checkpoint_id)
        if checkpoint is None or checkpoint.get("root_frame_id") != root_frame_id:
            raise ValueError("branch has no valid checkpoint projection")
        selected = _required_text("checkpoint branch_id", checkpoint.get("branch_id"))
        metadata = checkpoint.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        projection = metadata.get("history_projection")
        projection = projection if isinstance(projection, Mapping) else {}
        target_id = projection.get("base_checkpoint_id") or metadata.get("reverted_to")
        if isinstance(target_id, str) and target_id:
            base, _target_resume = checkpoint_projection(
                target_id,
                visiting | {checkpoint_id},
            )
            resume_values = projection.get("resume_cursors")
            resume_values = resume_values if isinstance(resume_values, Mapping) else {}
            raw_resume = resume_values.get(cursor_key)
            if raw_resume is None:
                undo_id = metadata.get("undo_checkpoint_id")
                undo = (
                    get_checkpoint(undo_id)
                    if isinstance(undo_id, str) and undo_id
                    else None
                )
                raw_resume = undo.get(cursor_key) if undo else None
            result = (list(base), normalize_cursor(raw_resume))
            checkpoint_cache[checkpoint_id] = result
            return (list(result[0]), result[1])

        parent_id = checkpoint.get("parent_checkpoint_id")
        parent = (
            get_checkpoint(parent_id)
            if isinstance(parent_id, str) and parent_id
            else None
        )
        if parent_id and (
            parent is None or parent.get("root_frame_id") != root_frame_id
        ):
            raise ValueError("checkpoint parent is unavailable")
        if parent is None:
            base: list[Record] = []
            resume = None
        else:
            base, parent_resume = checkpoint_projection(
                str(parent_id),
                visiting | {checkpoint_id},
            )
            # A child branch begins with the exact parent checkpoint prefix but
            # has no local rows yet.  Same-branch checkpoints extend from their
            # parent's physical continuation cursor.
            resume = (
                parent_resume
                if str(parent.get("branch_id") or "") == selected
                else None
            )
        current = normalize_cursor(checkpoint.get(cursor_key))
        result = ([*base, *segment(selected, after=resume, upto=current)], current)
        checkpoint_cache[checkpoint_id] = result
        return (list(result[0]), result[1])

    if not callable(get_branch) or not callable(get_checkpoint):
        if branch_id != root_frame_id:
            raise ValueError("branch projection requires checkpoint repository access")
        return list(local(branch_id))
    branch = get_branch(branch_id)
    if branch is None:
        if branch_id == root_frame_id:
            return list(local(branch_id))
        raise KeyError(f"unknown branch {branch_id!r} for this session")
    if branch.get("root_frame_id") != root_frame_id:
        raise KeyError(f"unknown branch {branch_id!r} for this session")
    head_id = branch.get("head_checkpoint_id")
    if not isinstance(head_id, str) or not head_id:
        return list(local(branch_id))
    head = get_checkpoint(head_id)
    if head is None or head.get("root_frame_id") != root_frame_id:
        raise ValueError("branch head checkpoint is unavailable")
    base, head_resume = checkpoint_projection(head_id, frozenset())
    resume = head_resume if str(head.get("branch_id") or "") == branch_id else None
    return [*base, *segment(branch_id, after=resume, upto=None)]


def _required_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


__all__ = [
    "BranchProjectionStore",
    "count_cursor",
    "inclusive_cursor",
    "project_branch_records",
]

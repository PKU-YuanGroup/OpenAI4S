"""Cross-session read-only "needs attention" aggregator.

The dashboard needs one place that answers "what is waiting on a person"
across every Session the caller may see. Each source already has a
projection — workbench security (pending approvals), recovery
status/actions, compute `owner_tasks`, the delegation tree, the in-memory
execution FIFO — but none of them is allowed to become a second write
path. This module only reads those projections.

Hard constraints, because each one is a way this view would be worse than
absent:

* GET is side-effect free. Nothing here starts a kernel, talks to a
  provider, retries, approves, or harvests. Workspace paths are computed
  without ``mkdir``.
* Team visibility is applied to the session set *before* aggregation,
  sort, or limit. Filtering after the fact would let ``limit`` and the
  cursor leak that hidden sessions exist.
* ``target.surface`` / ``target.dock`` are closed sets. The client builds
  navigation locally; the server never returns an arbitrary URL.
* Cards are one fact each, keyed by ``source_kind+source_id``. The same
  fact arriving from two projections keeps the higher-severity row.
* There is no materialized table. Scan budget is a session cap, not an
  index. Performance that misses the p95 bar is a later, reversible
  partial index — not a new writer on this path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from openai4s.server import compute_tasks
from openai4s.server.errors import GatewayError

#: How many visible root sessions one GET may inspect. Past this the
#: remainder wait for a later request rather than an unbounded N+1.
MAX_SCAN_SESSIONS = 200

DEFAULT_LIMIT = 50
MAX_LIMIT = 100

SOURCE_KINDS = frozenset(
    {"running", "queued", "approval", "recovery", "blocked", "compute"}
)
SURFACES = frozenset({"session"})
DOCKS = frozenset({"timeline", "recovery", "security", "compute"})
SEVERITIES = frozenset({"high", "medium", "low"})

#: Execution-owner kinds that count as "the session is running". Recovery
#: occupancy is not "running" attention — it has its own recovery card —
#: which is also what lets a queued-behind-recovery fixture emit exactly
#: one queued card.
_RUNNING_OWNERS = frozenset({"agent", "user_repl"})

_DOCK = {
    "running": "timeline",
    "queued": "timeline",
    "approval": "security",
    "recovery": "recovery",
    "blocked": "recovery",
    "compute": "compute",
}
_HINT = {
    "running": "watch",
    "queued": "watch",
    "approval": "approve",
    "recovery": "restore",
    "blocked": "inspect",
    "compute": "inspect",
}
_SEVERITY = {
    "running": "medium",
    "queued": "low",
    "approval": "high",
    "recovery": "high",
    "blocked": "medium",
    "compute": "medium",
}
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

_ITEM_KEYS = (
    "id",
    "source_kind",
    "source_id",
    "state",
    "severity",
    "frame_id",
    "project_id",
    "title",
    "updated_at",
    "target",
    "action_hint",
)

_SECRET_RE = re.compile(
    r"(?i)(?:Bearer\s+\S+|(?:sk|ark|ghp|github_pat|hf|xox[baprs])-"
    r"[A-Za-z0-9_.-]{8,}|(?:api[_-]?key|token|password|secret)\s*[=:]\s*\S+)"
)
_URL_RE = re.compile(r"(?i)\b(?:https?|mailto):")
_ABS_PATH_RE = re.compile(r"(?:^|[\s\"'])(?:/[A-Za-z]|[A-Za-z]:\\)")

RecoveryStatus = Callable[..., Mapping[str, Any]]
RecoveryActions = Callable[..., Mapping[str, Any]]
ExecutionSnapshot = Callable[[str], Mapping[str, Any]]
WorkspaceKey = Callable[[str], str]


class AttentionService:
    """Compose one page of attention cards from existing read projections."""

    def __init__(
        self,
        store: Any,
        *,
        workbench: Any,
        recovery_status: RecoveryStatus,
        recovery_actions: RecoveryActions,
        execution_snapshot: ExecutionSnapshot,
        workspace_key: WorkspaceKey,
    ) -> None:
        self.store = store
        self.workbench = workbench
        self._recovery_status = recovery_status
        self._recovery_actions = recovery_actions
        self._execution_snapshot = execution_snapshot
        self._workspace_key = workspace_key

    def list(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
        visible_to_user_id: str | None = None,
    ) -> dict[str, Any]:
        limit = _parse_limit(limit)
        fingerprint = _scope_fingerprint(visible_to_user_id)
        cursor_key = _decode_cursor(cursor, fingerprint)
        sessions = self._visible_sessions(visible_to_user_id)
        items = self._collect(sessions)
        items.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
        if cursor_key is not None:
            cursor_t, cursor_id = cursor_key
            items = [
                item
                for item in items
                if (item["updated_at"], item["id"]) < (cursor_t, cursor_id)
            ]
        page = items[:limit]
        has_more = len(items) > limit
        next_cursor = None
        if has_more and page:
            tail = page[-1]
            next_cursor = _encode_cursor(tail["updated_at"], tail["id"], fingerprint)
        return {
            "items": page,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def _visible_sessions(self, visible_to_user_id: str | None) -> list[dict]:
        """Newest-first roots the caller may see, capped by the scan budget.

        Visibility is the browse query itself — INV-13 for a cross-session
        read. A handler-level frame guard cannot see this fan-out.
        """
        out: list[dict] = []
        before: tuple[int, str] | None = None
        while len(out) < MAX_SCAN_SESSIONS:
            want = min(100, MAX_SCAN_SESSIONS - len(out))
            batch = self.store.browse_frames(
                project_id="all",
                roots_only=True,
                limit=want,
                before=before,
                visible_to_user_id=visible_to_user_id,
            )
            if not batch:
                break
            out.extend(batch)
            last = batch[-1]
            before = (int(last.get("created_at") or 0), str(last["frame_id"]))
            if len(batch) < want:
                break
        return out[:MAX_SCAN_SESSIONS]

    def _collect(self, sessions: list[dict]) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for frame in sessions:
            frame_id = str(frame.get("frame_id") or "")
            if not frame_id:
                continue
            try:
                snapshot = self._execution_snapshot(frame_id)
            except Exception:  # noqa: BLE001 — missing coordinator is idle
                snapshot = {}
            if not isinstance(snapshot, Mapping):
                snapshot = {}
            for item in self._items_for(frame, snapshot):
                key = (item["source_kind"], item["source_id"])
                previous = merged.get(key)
                if previous is None or _better(item, previous):
                    merged[key] = item
        return list(merged.values())

    def _items_for(
        self, frame: Mapping[str, Any], snapshot: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        frame_id = str(frame.get("frame_id") or "")
        items: list[dict[str, Any]] = []
        items.extend(self._execution_items(frame, snapshot))
        items.extend(self._approval_items(frame))
        items.extend(self._recovery_items(frame))
        items.extend(self._compute_items(frame))
        items.extend(self._delegation_items(frame))
        return [item for item in items if item.get("frame_id") == frame_id]

    def _execution_items(
        self, frame: Mapping[str, Any], snapshot: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        owner = snapshot.get("owner")
        if isinstance(owner, Mapping):
            kind = str((owner.get("owner") or {}).get("kind") or "")
            execution_id = str(owner.get("execution_id") or "")
            if kind in _RUNNING_OWNERS and execution_id:
                items.append(
                    self._card(
                        frame,
                        source_kind="running",
                        source_id=execution_id,
                        state="running",
                        updated_at=_as_ms(
                            owner.get("started_at") or owner.get("queued_at")
                        ),
                    )
                )
        for queued in snapshot.get("queue") or ():
            if not isinstance(queued, Mapping):
                continue
            kind = str((queued.get("owner") or {}).get("kind") or "")
            execution_id = str(queued.get("execution_id") or "")
            if kind not in _RUNNING_OWNERS or not execution_id:
                continue
            position = queued.get("queue_position")
            try:
                position_n = int(position)
            except (TypeError, ValueError):
                position_n = 1
            items.append(
                self._card(
                    frame,
                    source_kind="queued",
                    source_id=execution_id,
                    state="queued",
                    updated_at=_as_ms(queued.get("queued_at")),
                    action_hint=f"queue:{max(1, position_n)}",
                )
            )
        return items

    def _approval_items(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        frame_id = str(frame.get("frame_id") or "")
        pending_count = 0
        try:
            security = self.workbench.security(frame_id)
            permission = (
                security.get("permission") if isinstance(security, Mapping) else None
            )
            if isinstance(permission, Mapping):
                pending_count = int(permission.get("pending_count") or 0)
        except Exception:  # noqa: BLE001 — a broken projection is not a card
            pending_count = 0
        rows: list[Mapping[str, Any]] = []
        try:
            listed = self.store.list_permission_requests(root_frame_id=frame_id)
        except Exception:  # noqa: BLE001
            listed = []
        for row in listed:
            if isinstance(row, Mapping) and str(row.get("state") or "") == "pending":
                rows.append(row)
        items: list[dict[str, Any]] = []
        for row in rows:
            decision_id = str(row.get("decision_id") or "")
            if not decision_id:
                continue
            items.append(
                self._card(
                    frame,
                    source_kind="approval",
                    source_id=decision_id,
                    state="pending",
                    updated_at=_as_ms(row.get("created_at") or row.get("updated_at")),
                )
            )
        if pending_count > 0 and not items:
            items.append(
                self._card(
                    frame,
                    source_kind="approval",
                    source_id=frame_id,
                    state="pending",
                    updated_at=_as_ms(frame.get("updated_at")),
                )
            )
        return items

    def _recovery_items(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        frame_id = str(frame.get("frame_id") or "")
        try:
            actions = self._recovery_actions(frame_id)
        except Exception:  # noqa: BLE001
            try:
                actions = self._recovery_status(frame_id)
            except Exception:  # noqa: BLE001
                return []
        if not isinstance(actions, Mapping):
            return []
        view_only = bool(actions.get("view_only"))
        state = str(actions.get("state") or "")
        current = (
            actions.get("current")
            if isinstance(actions.get("current"), Mapping)
            else {}
        )
        updated_at = _as_ms(
            (current or {}).get("updated_at") or frame.get("updated_at")
        )
        items: list[dict[str, Any]] = []
        if view_only:
            items.append(
                self._card(
                    frame,
                    source_kind="blocked",
                    source_id=frame_id,
                    state="view_only",
                    updated_at=updated_at,
                )
            )
            return items
        if state in {"failed", "partial"}:
            items.append(
                self._card(
                    frame,
                    source_kind="recovery",
                    source_id=str((current or {}).get("recovery_id") or frame_id),
                    state=state,
                    updated_at=updated_at,
                )
            )
        return items

    def _compute_items(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        frame_id = str(frame.get("frame_id") or "")
        try:
            owner_key = self._workspace_key(frame_id)
        except Exception:  # noqa: BLE001
            return []
        try:
            listing = compute_tasks.owner_tasks(self.store, owner_key)
        except Exception:  # noqa: BLE001
            return []
        items: list[dict[str, Any]] = []
        for task in listing.get("tasks") or ():
            if not isinstance(task, Mapping) or not task.get("live"):
                continue
            job_id = str(task.get("job_id") or "")
            if not job_id:
                continue
            status = str(task.get("status") or "unknown")
            severity = "high" if status == "unknown" else "medium"
            items.append(
                self._card(
                    frame,
                    source_kind="compute",
                    source_id=job_id,
                    state=status,
                    updated_at=_as_ms(task.get("updated_at") or task.get("created_at")),
                    severity=severity,
                )
            )
        return items

    def _delegation_items(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        frame_id = str(frame.get("frame_id") or "")
        try:
            tree = self.workbench.delegation(frame_id)
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(tree, Mapping):
            return []
        items: list[dict[str, Any]] = []
        for child in tree.get("children") or ():
            if not isinstance(child, Mapping):
                continue
            child_id = str(child.get("child_id") or "")
            if not child_id:
                continue
            status = str(child.get("status") or "")
            updated_at = _as_ms(
                child.get("started_at")
                or child.get("created_at")
                or frame.get("updated_at")
            )
            if status == "running":
                items.append(
                    self._card(
                        frame,
                        source_kind="running",
                        source_id=child_id,
                        state="running",
                        updated_at=updated_at,
                    )
                )
            elif status == "pending":
                items.append(
                    self._card(
                        frame,
                        source_kind="queued",
                        source_id=child_id,
                        state="queued",
                        updated_at=updated_at,
                        action_hint="queue:1",
                    )
                )
        return items

    def _card(
        self,
        frame: Mapping[str, Any],
        *,
        source_kind: str,
        source_id: str,
        state: str,
        updated_at: int,
        action_hint: str | None = None,
        severity: str | None = None,
    ) -> dict[str, Any]:
        frame_id = str(frame.get("frame_id") or "")
        dock = _DOCK[source_kind]
        hint = action_hint or _HINT[source_kind]
        item = {
            "id": f"{source_kind}:{source_id}",
            "source_kind": source_kind,
            "source_id": source_id,
            "state": _safe_text(state, 40) or source_kind,
            "severity": severity or _SEVERITY[source_kind],
            "frame_id": frame_id,
            "project_id": str(frame.get("project_id") or "") or None,
            "title": _title(frame),
            "updated_at": int(updated_at or 0),
            "target": {
                "surface": "session",
                "dock": dock,
                "frame_id": frame_id,
            },
            "action_hint": _safe_text(hint, 40) or _HINT[source_kind],
        }
        return _public_item(item)


def workspace_key_for(runner: Any, root_frame_id: str) -> str:
    """The compute ``owner_key`` for a session, without creating the directory.

    ``SessionRunner.workspace_for`` mkdirs. A GET must not. The path formula
    is the same one the runner uses for the canonical branch, and the
    ``.branches`` layout for any other active branch.
    """
    ws_root = getattr(runner, "_ws_root", None)
    if ws_root is None:
        return root_frame_id
    store = getattr(runner, "store", None)
    branch_id = root_frame_id
    active = (
        getattr(store, "active_session_branch", None) if store is not None else None
    )
    if callable(active):
        try:
            branch_id = str(active(root_frame_id) or root_frame_id)
        except Exception:  # noqa: BLE001 — canonical path is the fallback
            branch_id = root_frame_id
    if branch_id == root_frame_id:
        return str(ws_root / root_frame_id)
    root_key = hashlib.sha256(root_frame_id.encode("utf-8")).hexdigest()[:24]
    branch_key = hashlib.sha256(branch_id.encode("utf-8")).hexdigest()[:24]
    return str(ws_root / ".branches" / root_key / branch_key)


def service_for(runner: Any) -> AttentionService:
    domain = getattr(runner, "session_domain", None)
    return AttentionService(
        runner.store,
        workbench=runner.workbench,
        recovery_status=domain.recovery_status,
        recovery_actions=domain.recovery_actions,
        execution_snapshot=runner.executions.snapshot,
        workspace_key=lambda frame_id: workspace_key_for(runner, frame_id),
    )


def _parse_limit(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise GatewayError(400, "limit must be an integer", "invalid_limit")
    try:
        limit = int(value)
    except (TypeError, ValueError) as error:
        raise GatewayError(400, "limit must be an integer", "invalid_limit") from error
    if limit < 1:
        raise GatewayError(400, "limit must be an integer", "invalid_limit")
    return min(MAX_LIMIT, limit)


def _scope_fingerprint(visible_to_user_id: str | None) -> str:
    scope = f"user:{visible_to_user_id}" if visible_to_user_id else "admin"
    raw = f"v1|{scope}|".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _encode_cursor(updated_at: int, item_id: str, fingerprint: str) -> str:
    raw = json.dumps(
        {"t": int(updated_at), "i": item_id, "f": fingerprint},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None, fingerprint: str) -> tuple[int, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("cursor is not an object")
        if str(payload.get("f") or "") != fingerprint:
            raise ValueError("cursor scope mismatch")
        item_id = str(payload.get("i") or "")
        if not item_id:
            raise ValueError("missing id")
        return (int(payload["t"]), item_id)
    except GatewayError:
        raise
    except Exception as error:  # noqa: BLE001 — unreadable is 400, not page one
        raise GatewayError(400, "invalid cursor", "invalid_cursor") from error


def _title(frame: Mapping[str, Any]) -> str:
    raw = frame.get("name") or frame.get("task_summary") or "Untitled session"
    return _safe_text(raw, 160) or "Untitled session"


def _safe_text(value: Any, limit: int) -> str | None:
    if value in (None, ""):
        return None
    text = _SECRET_RE.sub("<redacted>", str(value))
    text = _URL_RE.sub("", text)
    text = _ABS_PATH_RE.sub(" ", text)
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _as_ms(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if number > 10_000_000_000:
        return int(number)
    return int(number * 1000) if number else 0


def _better(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    rank = _SEVERITY_RANK.get(str(candidate.get("severity")), 0)
    current_rank = _SEVERITY_RANK.get(str(current.get("severity")), 0)
    if rank != current_rank:
        return rank > current_rank
    return int(candidate.get("updated_at") or 0) >= int(current.get("updated_at") or 0)


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(item.get("source_kind") or "")
    dock = str((item.get("target") or {}).get("dock") or "")
    surface = str((item.get("target") or {}).get("surface") or "")
    severity = str(item.get("severity") or "")
    if kind not in SOURCE_KINDS or dock not in DOCKS or surface not in SURFACES:
        raise RuntimeError("attention card escaped the closed target set")
    if severity not in SEVERITIES:
        raise RuntimeError("attention card escaped the closed severity set")
    target = item["target"]
    public = {key: item.get(key) for key in _ITEM_KEYS}
    public["target"] = {
        "surface": target["surface"],
        "dock": target["dock"],
        "frame_id": target["frame_id"],
    }
    return public


__all__ = [
    "DEFAULT_LIMIT",
    "DOCKS",
    "MAX_LIMIT",
    "MAX_SCAN_SESSIONS",
    "SOURCE_KINDS",
    "SURFACES",
    "AttentionService",
    "service_for",
    "workspace_key_for",
]

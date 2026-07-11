"""Read-only UI projection of the canonical Action Ledger.

The ledger keeps provider replay details and raw audit evidence.  The Web UI
needs a smaller, stable view: action identity, status, bounded redacted inputs,
results, resource keys, and execution milestones.  This service deliberately
omits provider ``wire_state`` and raw argument strings; callers cannot
accidentally turn a debugging endpoint into a credential or protocol dump.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class ActionTimelineStore(Protocol):
    def list_action_groups(self, root_frame_id: str, **filters: Any) -> list[dict]: ...

    def list_execution_attempts(self, **filters: Any) -> list[dict]: ...


class ActionTimelineService:
    """Project immutable ledger groups into an inspectable session timeline."""

    def __init__(self, store: ActionTimelineStore, *, payload_chars: int = 20_000):
        if payload_chars < 256:
            raise ValueError("payload_chars must be at least 256")
        self.store = store
        self.payload_chars = payload_chars

    def get(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        after_ordinal: int | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        if not str(root_frame_id or "").strip():
            raise ValueError("root_frame_id is required")
        filters: dict[str, Any] = {}
        if branch_id is not None:
            filters["branch_id"] = branch_id
        if after_ordinal is not None:
            if isinstance(after_ordinal, bool) or int(after_ordinal) < 0:
                raise ValueError("after_ordinal must be a non-negative integer")
            filters["after_ordinal"] = int(after_ordinal)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 500
        ):
            raise ValueError("limit must be an integer between 1 and 500")

        groups = self.store.list_action_groups(root_frame_id, **filters)
        total_count = len(groups)
        # Initial reads show the most recent research state. Cursor reads move
        # forward from their explicit ordinal and therefore keep the first page.
        groups = groups[:limit] if after_ordinal is not None else groups[-limit:]
        attempts = self.store.list_execution_attempts(root_frame_id=root_frame_id)
        attempts_by_group: dict[str, list[dict]] = defaultdict(list)
        for attempt in attempts:
            attempts_by_group[str(attempt.get("group_id") or "")].append(attempt)

        projected = [
            self._group(group, attempts_by_group.get(str(group.get("group_id")), []))
            for group in groups
        ]
        return {
            "root_frame_id": root_frame_id,
            "branch_id": branch_id or root_frame_id,
            "groups": projected,
            "count": len(projected),
            "total_count": total_count,
            "truncated": total_count > len(projected),
            "has_earlier": after_ordinal is None and total_count > len(projected),
            "has_more": after_ordinal is not None and total_count > len(projected),
            "first_ordinal": projected[0]["ordinal"] if projected else None,
            "last_ordinal": projected[-1]["ordinal"] if projected else None,
            "running": any(group["status"] == "running" for group in projected),
        }

    def _group(self, group: Mapping[str, Any], attempts: Sequence[dict]) -> dict:
        raw_events = list(group.get("events") or ())
        title_events = [
            {
                "type": event.get("type"),
                "arguments": event.get("canonical_arguments"),
                "result": event.get("result"),
            }
            for event in raw_events
        ]
        events = [self._event(event) for event in raw_events]
        public_attempts = [self._attempt(attempt) for attempt in attempts]
        public_attempts.sort(
            key=lambda item: (item["attempt_ordinal"], item["allocated_at"])
        )
        return {
            "group_id": group.get("group_id"),
            "root_frame_id": group.get("root_frame_id"),
            "branch_id": group.get("branch_id"),
            "turn_id": group.get("turn_id"),
            "ordinal": group.get("ordinal"),
            "kind": group.get("kind"),
            "provider": group.get("provider"),
            "model": group.get("model"),
            "title": _safe_text(_title(group, title_events), 160),
            "status": _status(group, events, public_attempts),
            "events": events,
            "attempts": public_attempts,
            "created_at": group.get("created_at"),
        }

    def _event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        arguments = event.get("canonical_arguments")
        result = event.get("result")
        name = None
        if isinstance(arguments, Mapping):
            name = arguments.get("name")
        if name is None and isinstance(result, Mapping):
            name = result.get("name")
        return {
            "event_id": event.get("event_id"),
            "sequence": event.get("sequence"),
            "type": event.get("type"),
            "name": _safe_text(name, 120),
            # Timeline is a researcher-facing projection, not a wire/debug
            # endpoint.  Canonical arguments, provider ids and raw tool
            # results remain in the ledger; exposing them here can leak a
            # command, URL token, credential, or private dataset value into
            # browser state.  Only outcome and artifact identities cross this
            # boundary.
            "is_error": bool(
                isinstance(result, Mapping) and result.get("is_error")
            ),
            "outcome": _public_outcome(result),
            "artifacts": _artifact_refs(result),
            "side_effect_class": event.get("side_effect_class"),
            "resource_keys": [
                _safe_text(value, 160)
                for value in list(event.get("resource_keys") or ())[:64]
            ],
            "created_at": event.get("created_at"),
        }

    def _attempt(self, attempt: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "attempt_id": attempt.get("attempt_id"),
            "producing_cell_id": attempt.get("producing_cell_id"),
            "attempt_ordinal": attempt.get("attempt_ordinal"),
            "generation_id": attempt.get("generation_id"),
            "allocated_at": attempt.get("allocated_at"),
            "started_at": attempt.get("started_at"),
            "response_at": attempt.get("response_at"),
            "capture_at": attempt.get("capture_at"),
            "finished_at": attempt.get("finished_at"),
            "terminal_state": attempt.get("terminal_state"),
            "error": _safe_text(attempt.get("error"), 500),
            "replayed_from_cell_id": attempt.get("replayed_from_cell_id"),
        }


_SECRET_RE = re.compile(
    r"(?i)(?:Bearer\s+\S+|(?:sk|ark|ghp|github_pat|hf|xox[baprs])-"
    r"[A-Za-z0-9_.-]{8,}|(?:api[_-]?key|token|password|secret)\s*[=:]\s*\S+)"
)


def _safe_text(value: Any, limit: int) -> str | None:
    if value in (None, ""):
        return None
    text = _SECRET_RE.sub("<redacted>", str(value))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _public_outcome(result: Any) -> str | None:
    if not isinstance(result, Mapping):
        return None
    if result.get("is_error"):
        return "error"
    status = str(result.get("status") or "").strip().lower()
    if status in {"failed", "error", "denied", "cancelled", "timed_out"}:
        return status
    return "ok"


def _artifact_refs(value: Any, *, limit: int = 32) -> list[str]:
    output: list[str] = []

    def visit(item: Any, depth: int = 0) -> None:
        if depth > 3 or len(output) >= limit:
            return
        if isinstance(item, Mapping):
            for key in ("artifact_id", "version_id", "filename"):
                ref = _safe_text(item.get(key), 200)
                if ref and ref not in output:
                    output.append(ref)
            for key in ("artifact", "artifacts", "files", "files_written"):
                if key in item:
                    visit(item[key], depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item[:limit]:
                visit(child, depth + 1)

    visit(value)
    return output


def _title(group: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> str:
    kind = str(group.get("kind") or "action")
    if kind == "user":
        message = group.get("assistant_message")
        content = message.get("content") if isinstance(message, Mapping) else None
        return _one_line(content) or "User message"
    if kind == "native_tools":
        names = []
        for event in events:
            if event.get("type") != "proposed":
                continue
            arguments = event.get("arguments")
            name = event.get("name")
            if name is None and isinstance(arguments, Mapping):
                name = arguments.get("name")
            if name and name not in names:
                names.append(str(name))
        return ", ".join(names) or "Control tools"
    if kind == "finalize":
        for event in events:
            arguments = event.get("arguments")
            if not isinstance(arguments, Mapping):
                continue
            payload = arguments.get("arguments")
            if isinstance(payload, Mapping):
                summary = _one_line(payload.get("summary"))
                if summary:
                    return summary
        return "Structured response"
    if kind in {"code", "execution"}:
        for event in events:
            arguments = event.get("arguments")
            if isinstance(arguments, Mapping) and arguments.get("code"):
                return _code_title(str(arguments["code"]))
        return "Scientific cell"
    if kind == "terminal":
        for event in events:
            result = event.get("result")
            if isinstance(result, Mapping) and result.get("reason"):
                return f"Run {result['reason']}"
        return "Run finished"
    return _one_line(group.get("assistant_content")) or kind.replace("_", " ").title()


def _status(
    group: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    attempts: Sequence[Mapping[str, Any]],
) -> str:
    if any(attempt.get("terminal_state") is None for attempt in attempts):
        return "running"
    terminal_states = [
        str(attempt.get("terminal_state"))
        for attempt in attempts
        if attempt.get("terminal_state")
    ]
    if terminal_states:
        if any(
            state in {"cancelled", "interrupted", "timed_out"}
            for state in terminal_states
        ):
            return "cancelled" if "cancelled" in terminal_states else "interrupted"
        return (
            "completed"
            if all(state == "completed" for state in terminal_states)
            else "failed"
        )
    if any(event.get("type") in {"failed", "denied", "timed_out"} for event in events):
        return "failed"
    if any(event.get("type") == "cancelled" for event in events):
        return "cancelled"
    results = [event for event in events if event.get("type") == "result"]
    if any(event.get("is_error") for event in results):
        return "failed"
    if str(group.get("kind") or "") in {"native_tools", "finalize"}:
        proposed = sum(event.get("type") == "proposed" for event in events)
        return "completed" if proposed and len(results) >= proposed else "pending"
    for event in reversed(events):
        event_type = event.get("type")
        if event_type in {"completed", "observation"}:
            return "completed"
        if event_type in {"proposed", "started"}:
            return "pending"
    return "recorded"


def _one_line(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _code_title(code: str) -> str:
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return _one_line(title)
        elif stripped:
            return _one_line(stripped)
    return "Scientific cell"


__all__ = ["ActionTimelineService"]

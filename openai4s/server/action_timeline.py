"""Read-only UI projection of the canonical Action Ledger.

The ledger keeps provider replay details and raw audit evidence.  The Web UI
needs a smaller, stable view: action identity, status, bounded redacted inputs,
results, resource keys, and execution milestones.  This service deliberately
omits provider ``wire_state`` and raw argument strings; callers cannot
accidentally turn a debugging endpoint into a credential or protocol dump.
"""

from __future__ import annotations

import hashlib
import json
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

        groups = self.store.list_action_groups(root_frame_id, **filters)
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
            "last_ordinal": projected[-1]["ordinal"] if projected else None,
            "running": any(group["status"] == "running" for group in projected),
        }

    def _group(self, group: Mapping[str, Any], attempts: Sequence[dict]) -> dict:
        events = [self._event(event) for event in group.get("events") or ()]
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
            "assistant_content": _bounded(
                group.get("assistant_content"), self.payload_chars
            ),
            "title": _title(group, events),
            "status": _status(group, events, public_attempts),
            "events": events,
            "attempts": public_attempts,
            "created_at": group.get("created_at"),
        }

    def _event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        arguments = _bounded(event.get("canonical_arguments"), self.payload_chars)
        result = _bounded(event.get("result"), self.payload_chars)
        name = None
        if isinstance(arguments, Mapping):
            name = arguments.get("name")
        if name is None and isinstance(result, Mapping):
            name = result.get("name")
        return {
            "event_id": event.get("event_id"),
            "sequence": event.get("sequence"),
            "type": event.get("type"),
            "action_id": event.get("action_id"),
            "tool_call_id": event.get("tool_call_id"),
            "wire_id": event.get("wire_id"),
            "name": name,
            "arguments": arguments,
            "result": result,
            "side_effect_class": event.get("side_effect_class"),
            "resource_keys": list(event.get("resource_keys") or ()),
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
            "error": _bounded(attempt.get("error"), self.payload_chars),
            "replayed_from_cell_id": attempt.get("replayed_from_cell_id"),
        }


def _bounded(value: Any, limit: int) -> Any:
    """Keep JSON values intact when small, otherwise return an auditable preview."""
    if value is None:
        return None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        encoded = repr(value)
    if len(encoded) <= limit:
        return value
    digest = hashlib.sha256(encoded.encode("utf-8", "replace")).hexdigest()
    return {
        "truncated": True,
        "sha256": digest,
        "original_chars": len(encoded),
        "preview": encoded[: limit - 1] + "…",
    }


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
    if any(
        isinstance(event.get("result"), Mapping)
        and event["result"].get("is_error")
        for event in results
    ):
        return "failed"
    if str(group.get("kind") or "") == "native_tools":
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

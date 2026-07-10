"""Stable trace normalization that preserves event and causal order."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .schema import EventEnvelope

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_ID_FIELDS = {"event_id", "parent_event_id", "run_id", "root_frame_id", "turn_id"}


def _plain_event(event: EventEnvelope | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(event, EventEnvelope):
        return event.to_dict()
    return dict(event)


def normalize_trace(
    events: Iterable[EventEnvelope | Mapping[str, Any]],
    *,
    replacements: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Normalize volatile identifiers and time without sorting event lists.

    Identifiers are assigned placeholders on first appearance.  Consequently,
    parent links remain meaningful and reversing two events changes the output.
    Mapping keys may later be sorted by the JSON encoder; list order never is.
    """

    source = [_plain_event(event) for event in events]
    first_ms = source[0].get("monotonic_ms", 0) if source else 0
    replacement_pairs = tuple((replacements or {}).items())
    for original, replacement in replacement_pairs:
        if not isinstance(original, str) or not original:
            raise ValueError("normalization replacement keys must be non-empty strings")
        if not isinstance(replacement, str):
            raise ValueError("normalization replacement values must be strings")
    id_maps: dict[str, dict[str, str]] = {
        "event_id": {},
        "run_id": {},
        "root_frame_id": {},
        "turn_id": {},
    }

    def normalized_id(field: str, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        namespace = "event_id" if field == "parent_event_id" else field
        mapping = id_maps[namespace]
        if value not in mapping:
            label = namespace.removesuffix("_id")
            mapping[value] = f"<{label}:{len(mapping) + 1}>"
        return mapping[value]

    def normalized_payload_uuid(value: str) -> str:
        # Preserve cross-field identity: a run/turn/frame id echoed inside a
        # payload keeps the placeholder its envelope field already received.
        for mapping in id_maps.values():
            if value in mapping:
                return mapping[value]
        return normalized_id("event_id", value)

    def walk(value: Any, field: str | None = None) -> Any:
        if field in _ID_FIELDS and (
            value is None or (isinstance(value, str) and _UUID_RE.fullmatch(value))
        ):
            # An id-NAMED payload key holding a non-UUID value (a label, "n/a")
            # falls through and is preserved verbatim instead of being aliased.
            return normalized_id(field, value)
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                coerced = str(key)
                if coerced in result:
                    raise ValueError(
                        f"normalization key collision after str(): {coerced!r}"
                    )
                result[coerced] = walk(item, coerced)
            return result
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return [walk(item) for item in value]
        if isinstance(value, Path):
            value = value.as_posix()
        if isinstance(value, str):
            # Replacements are explicit and applied in caller-provided order.
            # Typical rules cover workspace/data-dir prefixes and a selected
            # localhost endpoint such as 127.0.0.1:54321.
            for original, replacement in replacement_pairs:
                value = value.replace(original, replacement)
            if _UUID_RE.fullmatch(value):
                # UUIDs inside arbitrary payloads are normalized by first sight too.
                return normalized_payload_uuid(value)
        return value

    normalized: list[dict[str, Any]] = []
    for event in source:
        item = walk(event)
        ms = item.get("monotonic_ms")
        if isinstance(ms, (int, float)) and not isinstance(ms, bool):
            item["monotonic_ms"] = ms - first_ms
        normalized.append(item)
    return normalized


def normalized_trace_bytes(
    events: Iterable[EventEnvelope | Mapping[str, Any]],
    *,
    replacements: Mapping[str, str] | None = None,
) -> bytes:
    """Return the canonical UTF-8 encoding used for byte-level comparisons."""

    normalized = normalize_trace(events, replacements=replacements)
    return (
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")

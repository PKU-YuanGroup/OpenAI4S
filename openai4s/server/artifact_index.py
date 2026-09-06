"""Project-scoped Artifact index: keyset pages, opaque filter-bound cursors.

The Files dock needs to search and page hundreds of artifacts without
loading the whole project array the legacy ``GET /projects/{pid}/artifacts``
route still returns. This module owns the new page: query parsing, the
cursor that is invalid the moment the filter set changes, and the
``{artifacts, next_cursor, has_more}`` envelope.

The sort key is ``(created_at, artifact_id)`` descending. ``created_at`` is
a millisecond clock; two captures in the same millisecond are ordinary,
so the id tiebreaker is what makes a walk cover a tie instead of dropping
the rest of it. The cursor carries a fingerprint of
``project_id + q + content_type + origin + team scope``: a client that
changes any of those and reuses the previous cursor is refused with
``400 invalid_cursor`` rather than silently restarting at page one of a
different listing.

Filename search is an escaped substring ``LIKE`` issued by the repository,
never a glob and never a scan of path or content type. ``origin`` is
derived from the existing ``is_user_upload`` flag.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

from openai4s.server.errors import GatewayError

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
ALLOWED_ORIGINS = frozenset({"uploaded", "generated"})


def _iso(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    try:
        return (
            datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z"
        )
    except (ValueError, OSError, TypeError):
        return None


def artifact_row_json(row: dict) -> dict:
    """The same Artifact DTO the legacy array route already publishes."""
    return {
        "id": row["artifact_id"],
        "artifact_id": row["artifact_id"],
        "filename": row.get("filename"),
        "content_type": row.get("content_type"),
        "size_bytes": row.get("size_bytes"),
        "version_id": row.get("latest_version_id"),
        "checksum": row.get("checksum"),
        "project_id": row.get("project_id"),
        "root_frame_id": row.get("root_frame_id"),
        "priority": row.get("priority", 0),
        "created_at": _iso(row.get("created_at")),
        "is_user_upload": bool(row.get("is_user_upload", 0)),
    }


def filter_fingerprint(
    *,
    project_id: str,
    q: str,
    content_type: str,
    origin: str,
    team_scope: str,
) -> str:
    """Stable digest of the filter identity a cursor is bound to."""
    canonical = json.dumps(
        {
            "content_type": content_type,
            "origin": origin,
            "project_id": project_id,
            "q": q,
            "team_scope": team_scope,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_cursor(*, created_at: int, artifact_id: str, fingerprint: str) -> str:
    """Opaque keyset cursor. Opaque on purpose: a client that parses it
    becomes coupled to the sort key."""
    raw = json.dumps(
        {"fp": fingerprint, "id": artifact_id, "t": int(created_at)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str | None, *, fingerprint: str) -> tuple[int, str] | None:
    """Return the keyset tuple, or raise ``invalid_cursor``.

    A fingerprint mismatch is the same failure as a malformed payload: the
    caller changed ``q`` / ``content_type`` / ``origin`` / project / team
    scope, and reusing the old cursor must not walk a different listing.
    """
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("cursor is not an object")
        stored_fp = payload.get("fp")
        artifact_id = payload.get("id")
        created_at = payload.get("t")
        if stored_fp != fingerprint:
            raise ValueError("cursor filter mismatch")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("missing artifact id")
        return (int(created_at), artifact_id)
    except GatewayError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GatewayError(400, f"invalid cursor: {exc}", "invalid_cursor") from exc


def _first(q: dict, name: str) -> str:
    raw = (q.get(name) or [""])[0]
    return "" if raw is None else str(raw)


def parse_limit(raw: str) -> int:
    if raw == "":
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayError(400, "limit must be an integer", "invalid_limit") from exc
    if value < 1:
        raise GatewayError(400, "limit must be at least 1", "invalid_limit")
    return min(value, MAX_LIMIT)


def parse_origin(raw: str) -> str:
    origin = raw.strip()
    if not origin:
        return ""
    if origin not in ALLOWED_ORIGINS:
        raise GatewayError(
            400,
            "origin must be uploaded or generated",
            "invalid_origin",
        )
    return origin


def browse_index(
    store: Any,
    *,
    project_id: str,
    q: dict,
    visible_to_user_id: str | None,
) -> dict:
    """Assemble one keyset page for ``GET .../artifact-index``."""
    filename_query = _first(q, "q").strip()
    content_type = _first(q, "content_type").strip()
    origin = parse_origin(_first(q, "origin"))
    limit = parse_limit(_first(q, "limit"))
    team_scope = "" if visible_to_user_id is None else str(visible_to_user_id)
    fingerprint = filter_fingerprint(
        project_id=project_id,
        q=filename_query,
        content_type=content_type,
        origin=origin,
        team_scope=team_scope,
    )
    before = decode_cursor(_first(q, "cursor"), fingerprint=fingerprint)
    rows = store.browse_artifacts(
        project_id=project_id,
        filename_query=filename_query or None,
        content_type=content_type or None,
        origin=origin or None,
        before=before,
        limit=limit + 1,
        visible_to_user_id=visible_to_user_id,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        tail = page[-1]
        next_cursor = encode_cursor(
            created_at=int(tail["created_at"] or 0),
            artifact_id=str(tail["artifact_id"]),
            fingerprint=fingerprint,
        )
    return {
        "artifacts": [artifact_row_json(row) for row in page],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def project_id_from_path(raw: str) -> str:
    return unquote(raw)

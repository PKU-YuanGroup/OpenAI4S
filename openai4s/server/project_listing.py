"""``GET /projects``: parameters, paging, and the envelope, off the gateway.

The route has three listing modes and one closed-set failure table:

* no parameters -- the compatibility full dump, ordered by ``updated_at``;
* ``?cursor`` (official clients) -- opaque keyset paging on
  ``(last_active_at DESC NULLS LAST, project_id DESC)``, ``limit + 1`` to
  observe ``has_more`` without a second count;
* ``?offset`` -- honoured for one compatibility window, never combined with
  ``cursor``.

Team visibility is a WHERE conjunct on the listing SQL (INV-13), not a
post-filter after LIMIT: hidden rows must not occupy page slots or forge the
end of the list, and ``total`` is the exact count after that filter and
``q``. Every 400 carries a stable code (``invalid_q``, ``invalid_cursor``,
``invalid_limit``, ``invalid_offset``, ``bad_request``).

Rows come back as the repository's dicts; the gateway applies its own
``_project_json`` projection so this module needs nothing from ``gateway``.
"""

from __future__ import annotations

from typing import Any, Mapping

from openai4s.server.errors import GatewayError
from openai4s.storage.frames import (
    PROJECT_PAGE_DEFAULT,
    PROJECT_PAGE_MAX,
    PROJECT_Q_MAX_CODEPOINTS,
    decode_project_cursor,
    encode_project_cursor,
    project_filter_fingerprint,
)


def _first(query: Mapping[str, Any], key: str) -> str | None:
    """The first value of a ``parse_qs``-shaped parameter, or None if absent/empty."""
    values = query.get(key) or [None]
    value = values[0] if values else None
    return None if value in (None, "") else str(value)


def _int_param(raw: str, *, name: str, minimum: int, code: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayError(400, f"{name} must be an integer", code) from exc
    if value < minimum:
        raise GatewayError(400, f"{name} must be at least {minimum}", code)
    return value


def list_projects_page(
    store: Any,
    query: Mapping[str, Any],
    *,
    visible_to_user_id: str | None,
) -> dict[str, Any]:
    """The ``GET /projects`` envelope with raw repository rows under ``projects``.

    ``query`` is the ``parse_qs`` mapping (``{name: [value, ...]}``).
    ``visible_to_user_id`` is the team-mode principal, or None outside team
    mode / for an admin.
    """
    raw_q = _first(query, "q")
    raw_limit = _first(query, "limit")
    raw_cursor = _first(query, "cursor")
    raw_offset = _first(query, "offset")
    paging = any(
        value is not None for value in (raw_q, raw_limit, raw_cursor, raw_offset)
    )
    search = (raw_q or "").strip()
    if len(search) > PROJECT_Q_MAX_CODEPOINTS:
        raise GatewayError(
            400,
            "q must be at most 128 Unicode code points",
            "invalid_q",
        )
    if not paging:
        rows = store.list_projects(visible_to_user_id=visible_to_user_id)
        return {"projects": rows, "total": len(rows)}

    team_scope = "" if visible_to_user_id is None else str(visible_to_user_id)
    fingerprint = project_filter_fingerprint(q=search, team_scope=team_scope)
    if raw_cursor is not None and raw_offset is not None:
        raise GatewayError(400, "cursor and offset cannot be combined", "bad_request")
    before = None
    if raw_cursor is not None:
        try:
            before = decode_project_cursor(raw_cursor, fingerprint=fingerprint)
        except ValueError as exc:
            raise GatewayError(400, f"invalid cursor: {exc}", "invalid_cursor") from exc
    offset = None
    if raw_offset is not None:
        offset = _int_param(raw_offset, name="offset", minimum=0, code="invalid_offset")
    if raw_limit is None:
        limit = PROJECT_PAGE_DEFAULT
    else:
        limit = min(
            _int_param(raw_limit, name="limit", minimum=1, code="invalid_limit"),
            PROJECT_PAGE_MAX,
        )
    keyset = offset is None
    rows = store.list_projects(
        q=search or None,
        limit=limit + 1 if keyset else limit,
        offset=offset,
        before=before,
        visible_to_user_id=visible_to_user_id,
    )
    total = store.count_projects(
        q=search or None, visible_to_user_id=visible_to_user_id
    )
    next_cursor = None
    if keyset:
        has_more = len(rows) > limit
        page = rows[:limit]
        if has_more and page:
            tail = page[-1]
            next_cursor = encode_project_cursor(
                last_active_at=tail.get("last_active_raw"),
                project_id=str(tail["project_id"]),
                fingerprint=fingerprint,
            )
    else:
        page = rows
        has_more = (offset or 0) + len(page) < total
    return {
        "projects": page,
        "total": total,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


__all__ = ["list_projects_page"]

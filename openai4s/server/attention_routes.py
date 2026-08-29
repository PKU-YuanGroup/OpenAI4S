"""`GET /attention` — cross-session needs-attention cards (B-05).

Same shape as the other route groups: a validated ``RouteSpec`` table
shared with the contract inventory, and a tri-state ``handle()``.

The handler is a thin adapter. Visibility, sort, limit, and the closed
target set live in ``AttentionService``. Retry / approve / restore stay
on the existing mutation routes; this group has none.
"""

from __future__ import annotations

from typing import Any

from . import attention, contract

_LIST = contract.RouteSpec("attention.list", "GET", r"/attention", mutates=False)

ROUTES = contract.validate_routes((_LIST,))

_PATH = "/attention"


def handle(self: Any, method: str, sub: str, q: dict, runner: Any) -> bool:
    """Answer the attention route, or report that this group does not own it."""
    path = sub.split("?")[0]
    if path != _PATH:
        return False
    if not _LIST.match(method, path):
        return False
    raw_limit = (q.get("limit") or [str(attention.DEFAULT_LIMIT)])[0]
    cursor = (q.get("cursor") or [None])[0]
    visible_to = None
    filter_for = getattr(self, "_team_visibility_filter", None)
    if callable(filter_for):
        visible_to = filter_for()
    payload = attention.service_for(runner).list(
        limit=raw_limit,
        cursor=cursor if cursor not in (None, "") else None,
        visible_to_user_id=visible_to,
    )
    self._json(payload)
    return True

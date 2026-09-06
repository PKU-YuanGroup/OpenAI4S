"""HTTP adapter for ``GET /projects/{pid}/artifact-index``.

The legacy ``GET /projects/{pid}/artifacts`` array route is unchanged: this
module answers only the new path. ``RouteSpec`` feeds the contract inventory;
the handler is tri-state so the gateway chain continues to its 404 when the
path is not ours.
"""

from __future__ import annotations

from typing import Any

from . import artifact_index, contract, errors

_INDEX = contract.RouteSpec(
    "artifact.index",
    "GET",
    r"/projects/([^/]+)/artifact-index",
    mutates=False,
)

ROUTES = contract.validate_routes((_INDEX,))


def handle(self: Any, method: str, sub: str, q: dict, store: Any) -> bool:
    """Answer the Artifact index route, or report that this group does not own it."""
    matched = _INDEX.match(method, sub)
    if not matched:
        return False
    project_id = artifact_index.project_id_from_path(matched.group(1))
    visible_to = None
    visibility = getattr(self, "_team_visibility_filter", None)
    if callable(visibility):
        visible_to = visibility()
    try:
        self._json(
            artifact_index.browse_index(
                store,
                project_id=project_id,
                q=q,
                visible_to_user_id=visible_to,
            )
        )
    except errors.GatewayError:
        raise
    except ValueError as exc:
        raise errors.GatewayError(400, str(exc), "bad_request") from exc
    return True

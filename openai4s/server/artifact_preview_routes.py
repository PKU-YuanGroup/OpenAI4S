"""HTTP route for bounded table previews (ART-11).

Deliberately outside the Stage 9 workbench group: that group answers 403 while
its flag is off, and this route is what the default (flag-off) viewer uses for
a table too large to read in the browser, an ``.xlsx`` workbook, or a gzip
file. Team scope is enforced before dispatch by the gateway's path-based
artifact guard, and the version is resolved inside the Artifact it names.
"""

from __future__ import annotations

from typing import Any

from openai4s.server.artifact_workbench import WorkbenchError

from . import contract

_PREVIEW_TABLE = contract.RouteSpec(
    "artifact.preview_table",
    "GET",
    r"/artifacts/([^/]+)/preview-table",
    mutates=False,
)

ROUTES = contract.validate_routes((_PREVIEW_TABLE,))


def _one(q: dict, name: str) -> str | None:
    values = q.get(name) or [None]
    value = values[0]
    return str(value) if value else None


def handle(self: Any, method: str, sub: str, q: dict, runner: Any) -> bool:
    """Answer the preview route, or report that this group does not own it."""

    matched = _PREVIEW_TABLE.match(method, sub)
    if not matched:
        return False
    service = getattr(runner, "workbench_artifacts", None)
    if service is None:
        self._json(
            {"error": "artifact preview is unavailable", "code": "preview_unavailable"},
            503,
        )
        return True
    try:
        self._json(
            service.preview_table(
                matched.group(1),
                version_id=_one(q, "version_id"),
                sheet=_one(q, "sheet"),
            )
        )
    except WorkbenchError as error:
        self._json({"error": error.message, "code": error.code}, error.status)
    return True

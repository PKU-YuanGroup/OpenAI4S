"""HTTP routes for the Stage 9 Artifact workbench."""

from __future__ import annotations

from typing import Any

from openai4s.server.artifact_workbench import (
    WorkbenchError,
    official_workbench_enabled,
)

from . import contract, errors

_TABLE = contract.RouteSpec(
    "artifact.table",
    "GET",
    r"/artifacts/([^/]+)/table",
    mutates=False,
)
_DIFF = contract.RouteSpec(
    "artifact.diff",
    "GET",
    r"/artifacts/([^/]+)/diff",
    mutates=False,
)
_STRUCTURE = contract.RouteSpec(
    "artifact.structure",
    "POST",
    r"/artifacts/([^/]+)/structure",
    mutates=True,
)
_PDF_TEXT = contract.RouteSpec(
    "artifact.pdf_text",
    "GET",
    r"/artifacts/([^/]+)/pdf-text",
    mutates=False,
)
_HTML_OUTLINE = contract.RouteSpec(
    "artifact.html_outline",
    "GET",
    r"/artifacts/([^/]+)/html-outline",
    mutates=False,
)

ROUTES = contract.validate_routes((_TABLE, _DIFF, _STRUCTURE, _PDF_TEXT, _HTML_OUTLINE))


def _fail(self: Any, error: WorkbenchError) -> None:
    self._json({"error": error.message, "code": error.code}, error.status)


def handle(self: Any, method: str, sub: str, q: dict, runner: Any) -> bool:
    """Answer a workbench route, or report that this group does not own it."""

    specs = (_TABLE, _DIFF, _STRUCTURE, _PDF_TEXT, _HTML_OUTLINE)
    if not any(spec.match(method, sub) for spec in specs):
        return False
    if not official_workbench_enabled(runner.cfg):
        self._json(
            {"error": "artifact workbench is disabled", "code": "workbench_disabled"},
            403,
        )
        return True
    service = getattr(runner, "workbench_artifacts", None)
    if service is None:
        self._json(
            {
                "error": "artifact workbench is unavailable",
                "code": "workbench_unavailable",
            },
            503,
        )
        return True
    try:
        matched = _TABLE.match(method, sub)
        if matched:
            self._json(
                service.table(
                    matched.group(1),
                    sort=(q.get("sort") or [""])[0],
                    descending=(q.get("dir") or ["asc"])[0] == "desc",
                    filters={
                        key[2:]: values[0]
                        for key, values in q.items()
                        if key.startswith("q_") and values
                    },
                    offset=int((q.get("offset") or ["0"])[0] or 0),
                    limit=int((q.get("limit") or ["50"])[0] or 50),
                )
            )
            return True
        matched = _DIFF.match(method, sub)
        if matched:
            self._json(
                service.diff(
                    matched.group(1),
                    from_version=(q.get("from") or [None])[0],
                    to_version=(q.get("to") or [None])[0],
                )
            )
            return True
        matched = _STRUCTURE.match(method, sub)
        if matched:
            body = self._body()
            self._json(
                service.save_structure(
                    matched.group(1),
                    content=str(body.get("content") or ""),
                    fmt=str(body.get("format") or "mol"),
                )
            )
            return True
        matched = _PDF_TEXT.match(method, sub)
        if matched:
            self._json(service.pdf_text(matched.group(1)))
            return True
        matched = _HTML_OUTLINE.match(method, sub)
        if matched:
            self._json(service.html_outline(matched.group(1)))
            return True
    except WorkbenchError as error:
        _fail(self, error)
        return True
    except errors.GatewayError:
        raise
    return False

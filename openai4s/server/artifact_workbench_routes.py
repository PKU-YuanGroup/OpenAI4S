"""HTTP routes for the Stage 9 Artifact workbench."""

from __future__ import annotations

from typing import Any

from openai4s.server.artifact_workbench import (
    WorkbenchError,
    official_workbench_enabled,
)
from openai4s.server.table_profile import parse_table_query

from . import contract, errors

_TABLE = contract.RouteSpec(
    "artifact.table",
    "GET",
    r"/artifacts/([^/]+)/table",
    mutates=False,
)
_TABLE_PROFILE = contract.RouteSpec(
    "artifact.table_profile",
    "GET",
    r"/artifacts/([^/]+)/table/profile",
    mutates=False,
)
_TABLE_EXPORT = contract.RouteSpec(
    "artifact.table_export",
    "GET",
    r"/artifacts/([^/]+)/table/export\.csv",
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

ROUTES = contract.validate_routes(
    (
        _TABLE,
        _TABLE_PROFILE,
        _TABLE_EXPORT,
        _DIFF,
        _STRUCTURE,
        _PDF_TEXT,
        _HTML_OUTLINE,
    )
)


def _fail(self: Any, error: WorkbenchError) -> None:
    self._json({"error": error.message, "code": error.code}, error.status)


def handle(self: Any, method: str, sub: str, q: dict, runner: Any) -> bool:
    """Answer a workbench route, or report that this group does not own it."""

    specs = (
        _TABLE_PROFILE,
        _TABLE_EXPORT,
        _TABLE,
        _DIFF,
        _STRUCTURE,
        _PDF_TEXT,
        _HTML_OUTLINE,
    )
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
        matched = _TABLE_PROFILE.match(method, sub)
        if matched:
            parsed = parse_table_query(q, mode="profile")
            self._json(
                service.table_profile(
                    matched.group(1),
                    version_id=parsed.version_id,
                    filters=parsed.filters,
                )
            )
            return True
        matched = _TABLE_EXPORT.match(method, sub)
        if matched:
            parsed = parse_table_query(q, mode="export")
            exported = service.table_export(
                matched.group(1),
                version_id=parsed.version_id,
                sort=parsed.sort,
                descending=parsed.descending,
                filters=parsed.filters,
                spreadsheet_safe=parsed.spreadsheet_safe,
            )
            self._send(
                200,
                exported["body"],
                exported["content_type"],
                exported["headers"],
            )
            return True
        matched = _TABLE.match(method, sub)
        if matched:
            parsed = parse_table_query(q, mode="page")
            self._json(
                service.table(
                    matched.group(1),
                    sort=parsed.sort,
                    descending=parsed.descending,
                    filters=parsed.filters,
                    offset=parsed.offset,
                    limit=parsed.limit,
                    version_id=parsed.version_id or None,
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
                runner.save_artifact_structure(
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

"""Thin HTTP routes for Stage 2 Auto Mode durable projections.

Only configuration and read surfaces live here.  There is intentionally no
HTTP transition endpoint: Stage 2 must not let a caller invoke a Reviewer,
Repair Agent, or Permission Guardian through this adapter.
"""

from __future__ import annotations

from typing import Any

from . import contract
from .auto_mode import AutoModeError

_GET_AUTO_MODE = contract.RouteSpec(
    "auto_mode.get", "GET", r"/frames/([^/]+)/auto-mode", mutates=False
)
_PATCH_AUTO_MODE = contract.RouteSpec(
    "auto_mode.patch", "PATCH", r"/frames/([^/]+)/auto-mode", mutates=True
)
_GET_AUTO_AUDITS = contract.RouteSpec(
    "auto_mode.audits", "GET", r"/frames/([^/]+)/auto-audits", mutates=False
)

ROUTES = contract.validate_routes((_GET_AUTO_MODE, _PATCH_AUTO_MODE, _GET_AUTO_AUDITS))

_PATH_PREFIX = "/frames/"


def _answer_error(self: Any, error: AutoModeError) -> None:
    self._json({"error": str(error), "code": error.code}, error.status)


def handle(
    self: Any,
    method: str,
    sub: str,
    q: dict,
    runner: Any,
) -> bool:
    """Answer an Auto Mode route, or report that this group does not own it."""

    if not sub.startswith(_PATH_PREFIX):
        return False
    service = getattr(runner, "auto_mode", None)

    m = _GET_AUTO_MODE.match(method, sub)
    if m:
        if service is None:
            self._json(
                {
                    "error": "Auto Mode storage is unavailable",
                    "code": "auto_mode_storage_unavailable",
                },
                503,
            )
            return True
        try:
            self._json(service.get(m.group(1)))
        except AutoModeError as error:
            _answer_error(self, error)
        return True

    m = _PATCH_AUTO_MODE.match(method, sub)
    if m:
        if service is None:
            self._json(
                {
                    "error": "Auto Mode storage is unavailable",
                    "code": "auto_mode_storage_unavailable",
                },
                503,
            )
            return True
        try:
            self._json(service.patch(m.group(1), self._body()))
        except AutoModeError as error:
            _answer_error(self, error)
        return True

    m = _GET_AUTO_AUDITS.match(method, sub)
    if m:
        if service is None:
            self._json(
                {
                    "error": "Auto Mode storage is unavailable",
                    "code": "auto_mode_storage_unavailable",
                },
                503,
            )
            return True
        try:
            limit = contract.int_param(
                q.get("limit"), 100, name="limit", minimum=1, maximum=500
            )
        except contract.QueryParamError as error:
            self._json({"error": str(error), "code": "invalid_limit"}, 400)
            return True
        subject_kind = (q.get("subject_kind") or [None])[0]
        before = (q.get("before") or [None])[0]
        try:
            self._json(
                service.list_audits(
                    m.group(1),
                    subject_kind=subject_kind,
                    before=before,
                    limit=limit if limit is not None else 100,
                )
            )
        except AutoModeError as error:
            _answer_error(self, error)
        return True

    return False


__all__ = ["ROUTES", "handle"]

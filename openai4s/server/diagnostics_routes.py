"""Web Diagnostics: passive status, explicit checks, redacted bundle.

Three routes, all admin-only in team mode, all `Cache-Control: no-store`:

* ``GET  /diagnostics/status`` — security posture. No network, no child
  process, no Store/config/bundle write.
* ``POST /diagnostics/checks`` — the full ``doctor.report()``, side effects
  included, because the operator asked.
* ``POST /diagnostics/bundle`` — deny-by-default zip, streamed off a temp
  file that is unlinked on every exit (complete, disconnect, exception).
  One in-flight generation per process; one generation per principal per
  60 seconds; 32 MiB cap. The client cannot name the output path.

Members are refused with the same 403 as every other daemon-operation
surface, *before* the limiter or the temp file. 404 would hide a management
surface this product does not hide; a 403-vs-429-vs-size split would tell a
member whether an admin bundle is in flight.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from openai4s import doctor
from openai4s.diagnostics import (
    BUNDLE_FILENAME,
    BUNDLE_MAX_BYTES,
    BUNDLE_TEMP_PREFIX,
    BundleTooLarge,
    write_bundle_file,
)
from openai4s.server import contract, team_policy
from openai4s.server.errors import public_failure

_STATUS = contract.RouteSpec(
    "diagnostics.status", "GET", r"/diagnostics/status", mutates=False
)
_CHECKS = contract.RouteSpec(
    "diagnostics.checks", "POST", r"/diagnostics/checks", mutates=True
)
_BUNDLE = contract.RouteSpec(
    "diagnostics.bundle", "POST", r"/diagnostics/bundle", mutates=True
)

ROUTES = contract.validate_routes((_STATUS, _CHECKS, _BUNDLE))

_JSON_TYPE = "application/json; charset=utf-8"
_ZIP_TYPE = "application/zip"
_DISPOSITION = f'attachment; filename="{BUNDLE_FILENAME}"'
_COOLDOWN_S = 60.0

_OWNED = frozenset(
    {
        "/diagnostics/status",
        "/diagnostics/checks",
        "/diagnostics/bundle",
    }
)


class _BundleGate:
    """Process-wide single-flight plus per-principal cooldown."""

    def __init__(self, cooldown_s: float = _COOLDOWN_S) -> None:
        self._lock = threading.Lock()
        self._in_flight = False
        self._last: dict[str, float] = {}
        self._cooldown_s = cooldown_s
        self.clock = time.monotonic

    def try_begin(self, principal: str) -> str:
        """``ok``, ``busy``, or ``rate``. ``ok`` must be paired with ``end``."""
        now = self.clock()
        with self._lock:
            if self._in_flight:
                return "busy"
            last = self._last.get(principal)
            if last is not None and (now - last) < self._cooldown_s:
                return "rate"
            self._in_flight = True
            self._last[principal] = now
            return "ok"

    def end(self) -> None:
        with self._lock:
            self._in_flight = False

    def reset(self) -> None:
        with self._lock:
            self._in_flight = False
            self._last.clear()


_GATE = _BundleGate()


def reset_bundle_gate_for_tests() -> None:
    _GATE.reset()


def _principal_id(handler: Any) -> str:
    identity = getattr(handler, "_team_identity", None)
    if identity is not None:
        uid = str(getattr(identity, "user_id", "") or "")
        if uid:
            return f"user:{uid}"
    return "local"


def _request_id(handler: Any) -> str:
    return str(getattr(handler, "_correlation_id", "") or "")


def _reply_json(handler: Any, payload: Any, status: int = 200) -> None:
    """JSON with a single ``Cache-Control: no-store``.

    ``Handler._json`` hard-wires ``no-cache``. Diagnostics must not be
    stored, so this goes through ``_send_static_bytes`` when present and
    falls back to ``_json`` for unit doubles that only stub that method.
    """
    sender = getattr(handler, "_send_static_bytes", None)
    if callable(sender):
        body_obj = public_failure(payload, status, _request_id(handler))
        body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        sender(
            status,
            body,
            _JSON_TYPE,
            {"Cache-Control": "no-store"},
            None,
        )
        return
    json_fn = getattr(handler, "_json", None)
    if callable(json_fn):
        json_fn(payload, status)
        return
    raise RuntimeError("handler cannot send JSON")


def _refuse_member(handler: Any) -> bool:
    """True if a 403 was sent. Members never reach generation or the limiter."""
    if team_policy.may_change_instance_config(handler):
        return False
    _reply_json(handler, {"error": "admin only", "code": "admin_only"}, 403)
    return True


def _status(handler: Any, cfg: Any) -> None:
    payload = doctor.passive_status(cfg)
    payload["request_id"] = _request_id(handler)
    _reply_json(handler, payload, 200)


def _checks(handler: Any, cfg: Any) -> None:
    payload = doctor.report(cfg)
    payload["request_id"] = _request_id(handler)
    _reply_json(handler, payload, 200)


def _unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _refuse_limit(handler: Any, status: int, message: str, code: str) -> None:
    """429/413 through the same no-store writer as 200. Members never reach here."""
    _reply_json(handler, {"error": message, "code": code}, status)


def _bundle(handler: Any, cfg: Any) -> None:
    # The request body is ignored: a client-supplied output path is a non-goal.
    principal = _principal_id(handler)
    decision = _GATE.try_begin(principal)
    if decision == "busy":
        _refuse_limit(
            handler,
            429,
            "a diagnostic bundle is already in progress",
            "rate_limited",
        )
        return
    if decision == "rate":
        _refuse_limit(
            handler,
            429,
            "wait 60 seconds between diagnostic bundle downloads",
            "rate_limited",
        )
        return
    tmp_path: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=BUNDLE_TEMP_PREFIX, suffix=".zip")
        os.close(fd)
        tmp_path = Path(name)
        try:
            write_bundle_file(cfg, tmp_path, max_bytes=BUNDLE_MAX_BYTES)
        except BundleTooLarge:
            _refuse_limit(
                handler,
                413,
                "diagnostic bundle exceeds 32 MiB",
                "payload_too_large",
            )
            return
        stream = getattr(handler, "_stream_file", None)
        if not callable(stream):
            raise RuntimeError("handler cannot stream a file")
        extra = {
            "Cache-Control": "no-store",
            "Content-Disposition": _DISPOSITION,
        }
        request_id = _request_id(handler)
        if request_id:
            extra["X-Request-Id"] = request_id
        stream(tmp_path, _ZIP_TYPE, extra)
    finally:
        _unlink(tmp_path)
        _GATE.end()


def handle(self: Any, method: str, sub: str, *, cfg: Any) -> bool:
    """Answer a diagnostics route, or report that this group does not own it."""
    path = sub.split("?")[0]
    if path not in _OWNED:
        return False
    if _STATUS.match(method, path):
        if _refuse_member(self):
            return True
        _status(self, cfg)
        return True
    if _CHECKS.match(method, path):
        if _refuse_member(self):
            return True
        _checks(self, cfg)
        return True
    if _BUNDLE.match(method, path):
        if _refuse_member(self):
            return True
        _bundle(self, cfg)
        return True
    return False

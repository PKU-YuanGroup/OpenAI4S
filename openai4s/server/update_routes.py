"""Explicit update checks: read-only status, and a check the operator pressed.

Two routes, one feature, and a boundary that is the whole point of the feature:

* ``GET  /updates/status`` — the local version, the detected channel id, and
  whatever the check cache already holds. It never calls the discovery check,
  never writes the cache, and opens no socket. No record says "not checked yet"
  rather than an empty success.
* ``POST /updates/check`` — the explicit action. It accepts exactly
  ``{"refresh": boolean}`` and uses the discovery module's fixed server-side
  index; a client cannot name an index, a host, a version, a command or a
  write location. The answer is still only ``unknown`` / ``update_available`` /
  ``up_to_date``; a failure never becomes "up to date".

Both are admin-only in team mode, and that refusal happens before the cache is
read or a socket is opened. Both carry ``Cache-Control: no-store``. Nothing here
installs, stops, replaces or rolls back anything, and nothing is reachable from
an Agent Tool, Host capability or Skill: this version only reports and points at
the documented manual upgrade path.
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Any

from openai4s.server import contract, team_policy
from openai4s.server.errors import GatewayError, public_failure

_STATUS = contract.RouteSpec("updates.status", "GET", r"/updates/status", mutates=False)
_CHECK = contract.RouteSpec("updates.check", "POST", r"/updates/check", mutates=True)

ROUTES = contract.validate_routes((_STATUS, _CHECK))

_OWNED = frozenset({"/updates/status", "/updates/check"})
_JSON_TYPE = "application/json; charset=utf-8"

#: One forced check per principal per minute, and one instance-wide check at a
#: time. A member can press the button repeatedly; the release index does not
#: need to be dialled for every press.
_CHECK_COOLDOWN_S = 60.0
_BUSY_RETRY_S = 5

#: The route-level reason for "no check has ever been recorded". It is not a
#: discovery reason: the discovery module's frozen vocabulary describes what a
#: *check* concluded, and no check happened. The UI must be able to say
#: "not checked yet" instead of showing an empty success.
REASON_NOT_CHECKED = "not_checked"

#: The only trust fields a client may see. `witnesses` itself carries digest
#: maps; the projection is the whitelist, so a shape change in verify cannot
#: leak a value here.
_TRUST_FIELDS = ("integrity", "authorship", "authorship_detail")
_MAX_TRUST_CHARS = 600
_MAX_VERSION_CHARS = 64


class _CheckGate:
    """Process-wide single-flight, plus a per-principal forced-refresh cooldown.

    The stamp is written when the check *begins*, so two clicks cannot both
    pass the cooldown; one check in flight is the instance-wide cap, because
    the release index and the cache are both instance-global.
    """

    def __init__(self, cooldown_s: float = _CHECK_COOLDOWN_S) -> None:
        self._lock = threading.Lock()
        self._in_flight = False
        self._last_forced: dict[str, float] = {}
        self._cooldown_s = float(cooldown_s)
        self.clock = time.monotonic

    def try_begin(self, principal: str, *, force: bool) -> tuple[str, int]:
        """``("ok", 0)``, ``("busy", secs)`` or ``("rate", secs)``."""
        now = self.clock()
        with self._lock:
            if self._in_flight:
                return ("busy", _BUSY_RETRY_S)
            if force:
                last = self._last_forced.get(principal)
                if last is not None and (now - last) < self._cooldown_s:
                    remaining = int(math.ceil(self._cooldown_s - (now - last)))
                    return ("rate", max(1, min(remaining, int(self._cooldown_s))))
                self._last_forced[principal] = now
            self._in_flight = True
            return ("ok", 0)

    def end(self) -> None:
        with self._lock:
            self._in_flight = False

    def reset(self) -> None:
        with self._lock:
            self._in_flight = False
            self._last_forced.clear()


_GATE = _CheckGate()


def reset_check_gate_for_tests() -> None:
    _GATE.reset()


def _request_id(handler: Any) -> str:
    return str(getattr(handler, "_correlation_id", "") or "")


def _principal_id(handler: Any) -> str:
    identity = getattr(handler, "_team_identity", None)
    if identity is not None:
        uid = str(getattr(identity, "user_id", "") or "")
        if uid:
            return f"user:{uid}"
    return "local"


def _reply_json(
    handler: Any,
    payload: Any,
    status: int = 200,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    """JSON with ``Cache-Control: no-store`` on every status, errors included.

    ``Handler._json`` hard-wires ``no-cache``; a cached update answer is exactly
    what this surface must not serve, so this goes through ``_send_static_bytes``
    when present and falls back to ``_json`` for unit doubles that only stub it.
    """
    sender = getattr(handler, "_send_static_bytes", None)
    if callable(sender):
        body_obj = public_failure(payload, status, _request_id(handler))
        body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        extra = {"Cache-Control": "no-store", **(headers or {})}
        sender(status, body, _JSON_TYPE, extra, None)
        return
    json_fn = getattr(handler, "_json", None)
    if callable(json_fn):
        json_fn(payload, status)
        return
    raise RuntimeError("handler cannot send JSON")


def _refuse_member(handler: Any) -> bool:
    """True if a 403 was sent. Members never reach the cache or the network."""
    if team_policy.may_change_instance_config(handler):
        return False
    _reply_json(handler, {"error": "admin only", "code": "admin_only"}, 403)
    return True


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bounded_text(value: Any, limit: int) -> str:
    if value in (None, ""):
        return ""
    return str(value)[:limit]


def _running_version() -> str:
    from openai4s.update import discovery

    try:
        return str(discovery.running_version())
    except Exception:  # noqa: BLE001 - a read-only status must not fail on this
        return ""


def _channel_view(cfg: Any) -> dict[str, str]:
    """The detected channel, id only.

    ``Channel.evidence`` names absolute install paths and is admin-only by
    default; the projection here is the id alone, so the refusal copy and the
    manual upgrade instructions stay a client-side static mapping.
    """
    from openai4s.update import channel as update_channel

    try:
        return {"id": str(update_channel.detect(cfg).id)}
    except Exception:  # noqa: BLE001 - detection is pure but must not blank the route
        return {"id": "unknown"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cached_document(cfg: Any) -> dict[str, Any] | None:
    """The cached answer, only if it describes *this* install.

    No check is called and nothing is written: this reads the file the check
    path owns. A cache recorded against another running version or another
    index is not this install's answer, and reporting it as one is how the UI
    would tell a user something true about a different release.
    """
    from openai4s.update import discovery

    try:
        document = discovery.read_cache(discovery.cache_path(cfg))
        if not isinstance(document, dict):
            return None
        if str(document.get("running") or "") != _running_version():
            return None
        index = discovery.resolved_index()
        if str(document.get("index") or "") != index:
            return None
        return document
    except Exception:  # noqa: BLE001 - unreadable cache reads as "not checked"
        return None


def _previous_view(value: Any) -> dict[str, Any]:
    from openai4s.update import discovery

    if not isinstance(value, dict):
        return {}
    status = str(value.get("status") or "")
    if status not in discovery.STATUSES:
        return {}
    return {
        "status": status,
        "latest": _bounded_text(value.get("latest"), _MAX_VERSION_CHARS),
        "checked_at_ms": _as_int(value.get("checked_at_ms")),
    }


def _trust_view(document: dict[str, Any]) -> dict[str, str]:
    witnesses = document.get("witnesses")
    trust = witnesses.get("trust") if isinstance(witnesses, dict) else None
    if not isinstance(trust, dict):
        return {}
    out: dict[str, str] = {}
    for key in _TRUST_FIELDS:
        value = trust.get(key)
        if isinstance(value, str) and value:
            out[key] = value[:_MAX_TRUST_CHARS]
    return out


def _check_view(document: Any, *, cached: bool, age_s: int | None) -> dict[str, Any]:
    from openai4s.update import discovery

    if not isinstance(document, dict):
        # No record. `cached: false` says "this is not a cached answer"; the age
        # is unknown rather than zero, because "zero seconds old" would read as
        # a check that just happened.
        return {
            "status": "unknown",
            "reason": REASON_NOT_CHECKED,
            "running": _running_version(),
            "latest": "",
            "checked_at_ms": 0,
            "cached": False,
            "age_s": None,
            "retry_after_at": 0,
            "previous": {},
            "trust": {},
        }
    status = str(document.get("status") or "unknown")
    if status not in discovery.STATUSES:
        status = "unknown"
    reason = str(document.get("reason") or "unknown")
    if reason not in discovery.REASONS:
        reason = "unknown"
    return {
        "status": status,
        "reason": reason,
        "running": _bounded_text(document.get("running"), _MAX_VERSION_CHARS),
        "latest": _bounded_text(document.get("latest"), _MAX_VERSION_CHARS),
        "checked_at_ms": _as_int(document.get("checked_at_ms")),
        "cached": bool(cached),
        "age_s": age_s,
        "retry_after_at": _as_int(document.get("retry_after_at")),
        "previous": _previous_view(document.get("previous")),
        "trust": _trust_view(document),
    }


def _status(handler: Any, cfg: Any) -> None:
    document = _cached_document(cfg)
    if document is None:
        check = _check_view(None, cached=False, age_s=None)
    else:
        checked = _as_int(document.get("checked_at_ms"))
        age = max(0, (_now_ms() - checked) // 1000) if checked > 0 else None
        check = _check_view(document, cached=True, age_s=age)
    _reply_json(
        handler,
        {
            "request_id": _request_id(handler),
            "channel": _channel_view(cfg),
            "check": check,
            "apply_available": False,
        },
    )


def _refresh_request(handler: Any) -> bool:
    """Exactly ``{"refresh": boolean}``, and nothing else.

    The body is the only input this route has; a client that names an index, a
    host or a version is asking the daemon to fetch from somewhere it chose,
    which is not a knob this version exposes. Absent input is refused rather
    than defaulted, because "the caller sent nothing" and "the caller asked for
    a cached read" are different requests.
    """
    body = handler._body()
    extra = set(body) - {"refresh"}
    if extra or "refresh" not in body:
        raise GatewayError(
            400, 'body must be exactly {"refresh": boolean}', "invalid_request"
        )
    if not isinstance(body["refresh"], bool):
        raise GatewayError(400, "refresh must be a boolean", "invalid_request")
    return body["refresh"]


def _check_route(handler: Any, cfg: Any) -> None:
    try:
        refresh = _refresh_request(handler)
    except GatewayError as error:
        # `_body` raises this for malformed JSON too. Answering it here keeps
        # the same no-store writer for every status this route can return.
        _reply_json(
            handler,
            {"error": error.message, "code": error.error_code or "invalid_request"},
            error.code,
        )
        return

    principal = _principal_id(handler)
    decision, retry_after_s = _GATE.try_begin(principal, force=refresh)
    if decision != "ok":
        reason = "check_in_progress" if decision == "busy" else "refresh_cooldown"
        message = (
            "an update check is already in progress"
            if decision == "busy"
            else "wait before forcing another update check"
        )
        _reply_json(
            handler,
            {
                "error": message,
                "code": "rate_limited",
                "reason": reason,
                "retry_after_s": retry_after_s,
            },
            429,
            headers={"Retry-After": str(retry_after_s)},
        )
        return

    try:
        from openai4s.update import discovery

        document = discovery.check(cfg, refresh=refresh)
    finally:
        _GATE.end()

    cached = bool(document.get("cached")) if isinstance(document, dict) else False
    age = _as_int(document.get("age_s")) if cached else 0
    _reply_json(
        handler,
        {
            "request_id": _request_id(handler),
            "channel": _channel_view(cfg),
            "check": _check_view(document, cached=cached, age_s=age),
            "apply_available": False,
        },
    )


def handle(self: Any, method: str, sub: str, *, cfg: Any) -> bool:
    """Answer an update route, or report that this group does not own it."""
    path = sub.split("?")[0]
    if path not in _OWNED:
        return False
    if _STATUS.match(method, path):
        if _refuse_member(self):
            return True
        _status(self, cfg)
        return True
    if _CHECK.match(method, path):
        if _refuse_member(self):
            return True
        _check_route(self, cfg)
        return True
    return False


__all__ = [
    "REASON_NOT_CHECKED",
    "ROUTES",
    "handle",
    "reset_check_gate_for_tests",
]

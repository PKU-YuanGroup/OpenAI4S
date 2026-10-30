"""Explicit update checks: read-only status, and a check the operator pressed.

Two routes, one feature (F14, F15, appendix B.2), and a boundary that is the
whole point of the feature:

* ``GET  /updates/status`` — the running version, the detected channel, and the
  check cache *if* one exists. It never calls ``discovery.check``, never writes
  the cache and opens no socket. ``OPENAI4S_UPDATE_SOURCE=offline`` answers the
  offline state without reading the cache; otherwise only ``read_cache`` runs and
  ``check`` is ``null`` when there is no valid record.
* ``POST /updates/check`` — the explicit action. The body may be ``{}`` or
  ``{"refresh": boolean}``; anything else is ``400 bad_request``. It calls the
  existing discovery check with the server-side index fixed, and the answer is
  still only ``unknown`` / ``update_available`` / ``up_to_date`` — a failure
  never becomes "up to date".

Both are admin-only in team mode (the ``/updates/`` surface is a daemon
operation, F14), and that refusal happens before the cache is read or a socket
is opened. Every response carries ``Cache-Control: no-store``. The projection is
appendix B.2's whitelist: no ``detail``, ``assets``, ``index``, raw
``witnesses``, ``Channel.evidence``, ``install_root``, ``interpreter``, download
URL or absolute path. Nothing here installs, stops, replaces or rolls back
anything, and nothing is reachable from an Agent Tool, Host capability or Skill
(INV-9).
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

#: Per-value ceiling for the one free-form version string we publish. The values
#: come from discovery, which only accepts the parsed release grammar, and from a
#: cache file the check path owns; clipping is belt-and-braces, not a parser.
_MAX_VERSION_CHARS = 64

#: The two trust states appendix B.2 allows. Authorship is never established by
#: digest agreement, and the response must say so rather than imply otherwise.
_TRUST_INTEGRITY_NONE = "none"
_TRUST_INTEGRITY_SINGLE = "single_witness"
_TRUST_INTEGRITY_TWO = "two_witnesses"
_TRUST_AUTHORSHIP = "not_established"


class _CheckGate:
    """Process-wide single-flight, plus a per-principal forced-refresh cooldown.

    The stamp is written when the check *begins*, so two clicks cannot both pass
    the cooldown; one check in flight is the instance-wide cap, because the
    release index and the cache are both instance-global.
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
                # Stamps past the cooldown decide nothing; keep only live ones
                # so the map stays bounded by recent principals.
                self._last_forced = {
                    key: stamp
                    for key, stamp in self._last_forced.items()
                    if (now - stamp) < self._cooldown_s
                }
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

    ``Handler._json`` hard-wires ``no-cache``; a cached update answer is the one
    thing this surface must never serve, so this goes through
    ``_send_static_bytes`` when present and falls back to ``_json`` for unit
    doubles that only stub it.
    """
    sender = getattr(handler, "_send_static_bytes", None)
    if callable(sender):
        body_obj = public_failure(
            payload, status, str(getattr(handler, "_correlation_id", "") or "")
        )
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


def _channel_view(cfg: Any) -> dict[str, Any]:
    """The detected channel, as appendix B.2's three static fields.

    ``Channel.evidence``, ``install_root`` and ``interpreter`` name absolute
    paths and are admin-only; they are not part of this projection. ``refusal``
    is the prose sentence and is left out too — the UI maps ``refusal_text_key``
    and ``refusal_command`` (static text, no paths) itself.
    """
    from openai4s.update import channel as update_channel

    try:
        detected = update_channel.detect(cfg)
    except Exception:  # noqa: BLE001 - detection is pure but must not blank the route
        return {"id": "unknown", "refusal_text_key": "", "refusal_command": []}
    commands = getattr(detected, "refusal_command", ())
    return {
        "id": str(detected.id),
        "refusal_text_key": str(getattr(detected, "refusal_text_key", "") or ""),
        "refusal_command": [str(command) for command in (commands or ())],
    }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cached_document(cfg: Any) -> dict[str, Any] | None:
    """The cached answer if there is a valid one; no check, no write.

    ``read_cache`` already rejects a document whose ``status``/``reason`` is
    outside the frozen vocabularies, so what comes back is a shape this route is
    allowed to project.
    """
    from openai4s.update import discovery

    try:
        document = discovery.read_cache(discovery.cache_path(cfg))
    except Exception:  # noqa: BLE001 - an unreadable cache is "no record"
        return None
    return document if isinstance(document, dict) else None


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
    """Appendix B.2's two-word trust summary.

    Only two independent witnesses were compared, or only one was readable, or
    no comparison happened at all. Authorship is never established by digest
    agreement, and the response says so every time.
    """
    witnesses = document.get("witnesses")
    integrity = _TRUST_INTEGRITY_NONE
    if isinstance(witnesses, dict) and witnesses:
        integrity = (
            _TRUST_INTEGRITY_SINGLE
            if witnesses.get("single_witness")
            else _TRUST_INTEGRITY_TWO
        )
    return {"integrity": integrity, "authorship": _TRUST_AUTHORSHIP}


def _check_view(
    document: dict[str, Any], *, cached: bool, age_s: int
) -> dict[str, Any]:
    from openai4s.update import discovery

    status = str(document.get("status") or "unknown")
    if status not in discovery.STATUSES:
        status = "unknown"
    reason = str(document.get("reason") or "unknown")
    if reason not in discovery.REASONS:
        reason = "unknown"
    source = str(document.get("source") or "none")
    if source not in discovery.SOURCES:
        source = "none"
    return {
        "status": status,
        "reason": reason,
        "latest": _bounded_text(document.get("latest"), _MAX_VERSION_CHARS),
        "source": source,
        "checked_at_ms": _as_int(document.get("checked_at_ms")),
        "retry_after_at": _as_int(document.get("retry_after_at")),
        "cached": bool(cached),
        "age_s": max(0, _as_int(age_s)),
        "previous": _previous_view(document.get("previous")),
        "trust": _trust_view(document),
    }


def _offline_check() -> dict[str, Any]:
    """The answer of a check that the offline pin forbids before it starts.

    No cache is consulted when ``OPENAI4S_UPDATE_SOURCE=offline`` (F14's GET
    rule): reporting a cached answer as this install's current state would be a
    claim the pinned configuration says cannot be made.
    """
    return {
        "status": "unknown",
        "reason": "offline_pin",
        "latest": "",
        "source": "offline",
        "checked_at_ms": 0,
        "retry_after_at": 0,
        "cached": False,
        "age_s": 0,
        "previous": {},
        "trust": {"integrity": _TRUST_INTEGRITY_NONE, "authorship": _TRUST_AUTHORSHIP},
    }


def _status(handler: Any, cfg: Any) -> None:
    from openai4s.update import discovery

    if discovery.offline_pinned():
        check: dict[str, Any] | None = _offline_check()
    else:
        document = _cached_document(cfg)
        if document is None:
            check = None
        else:
            checked = _as_int(document.get("checked_at_ms"))
            age = max(0, (_now_ms() - checked) // 1000) if checked > 0 else 0
            check = _check_view(document, cached=True, age_s=age)
    _reply_json(
        handler,
        {
            "running": _running_version(),
            "channel": _channel_view(cfg),
            "check": check,
            "apply_available": False,
        },
    )


def _refresh_request(handler: Any) -> bool:
    """``{}`` or ``{"refresh": boolean}`` — nothing else.

    The body is the only input this route has. A client that names an index, a
    host or a version is asking the daemon to fetch from somewhere it chose,
    which is not a knob this version exposes. An absent body means "use the
    cached answer if it is still fresh" — that is the read the UI performs when
    the card's button is pressed without forcing a re-check.
    """
    body = handler._body()
    unknown = set(body) - {"refresh"}
    if unknown:
        raise GatewayError(400, "body may only contain 'refresh'", "bad_request")
    refresh = body.get("refresh", False)
    if not isinstance(refresh, bool):
        raise GatewayError(400, "refresh must be a boolean", "bad_request")
    return refresh


def _check_route(handler: Any, cfg: Any) -> None:
    try:
        refresh = _refresh_request(handler)
    except GatewayError as error:
        # `_body` raises this for malformed JSON and non-object bodies too. The
        # contract names one code for every bad request on this route (B.2), so
        # the reply keeps the gateway's sentence and reports `bad_request`.
        _reply_json(handler, {"error": error.message, "code": "bad_request"}, 400)
        return

    principal = _principal_id(handler)
    decision, retry_after_s = _GATE.try_begin(principal, force=refresh)
    if decision != "ok":
        message = (
            "an update check is already in progress"
            if decision == "busy"
            else "wait before forcing another update check"
        )
        _reply_json(
            handler,
            {"error": message, "code": "rate_limited", "retry_after_s": retry_after_s},
            429,
            headers={"Retry-After": str(retry_after_s)},
        )
        return

    try:
        from openai4s.update import discovery

        document = discovery.check(cfg, refresh=refresh)
    finally:
        _GATE.end()

    cached = bool(document.get("cached"))
    age = _as_int(document.get("age_s")) if cached else 0
    _reply_json(
        handler,
        {
            "running": _running_version(),
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
    "ROUTES",
    "handle",
    "reset_check_gate_for_tests",
]

"""The one call the rest of the program makes: `emit(...)`.

Everything upstream of here is about not sending the wrong thing. This module is
about not harming the program that is trying to tell the truth. Three rules:

* **It never raises.** A telemetry bug must not become a failed turn. Every
  path is wrapped, and the wrap is the feature, not defensive habit.
* **It never blocks.** The send happens on a daemon thread, so a slow or
  unreachable collector cannot add a second to a turn that had nothing to do
  with telemetry.
* **It reads consent every time.** There is no cached "enabled" that could
  outlive a revoke. The check is one SQLite read of one row.

And it does all of that only after consent, so on the overwhelmingly common
path -- no consent -- `emit` is a settings read that returns None and stops.

Records are accumulated per install and flushed as one envelope, because one
POST carrying five counts is less to send, and less to receive, than five.
"""
from __future__ import annotations

import threading
from typing import Any

from openai4s.telemetry import consent as consent_mod
from openai4s.telemetry import schema

#: Buffered records, flushed together. Bounded: past the cap the oldest are
#: dropped, because a telemetry buffer that grows without limit is a memory bug
#: wearing a privacy feature's clothes.
_LOCK = threading.Lock()
_BUFFER: list[dict[str, Any]] = []
_MAX_BUFFER = 256

#: ``(install_id, root_frame_id)`` pairs seen this process, so `session_start`
#: fires once per session per identity. Keyed by identity too, so a revoke and
#: regrant mid-session — a new install id — is a new participation period that
#: gets its own session_start. A set, not persisted: a dedup within one run.
_SEEN_SESSIONS: set[tuple[str, str]] = set()


def _store_for(store: Any) -> Any | None:
    if store is not None:
        return store
    # No store passed: resolve the daemon's, but never construct one just to
    # emit -- that would touch disk on the disabled path.
    try:
        from openai4s.config import get_config
        from openai4s.store import get_store

        return get_store(get_config().db_path)
    except Exception:  # noqa: BLE001
        return None


def emit(event: str, *, store: Any = None, **fields: Any) -> None:
    """Record one event. Safe on any thread, cheap when telemetry is off.

    The heavy lifting -- sanitising, buffering, sending -- happens only after
    the consent read succeeds, so a disabled install pays one row lookup.
    """
    try:
        target = _store_for(store)
        if target is None:
            return
        active = consent_mod.read(target)
        if active is None:
            return
        record = schema.sanitise_record({"event": event, **fields})
        if not record.get("event"):
            return

        with _LOCK:
            _BUFFER.append(record)
            if len(_BUFFER) > _MAX_BUFFER:
                del _BUFFER[: len(_BUFFER) - _MAX_BUFFER]
            batch = list(_BUFFER)
            _BUFFER.clear()

        _dispatch(target, active.install_id, batch)
    except Exception:  # noqa: BLE001 - telemetry must never break a caller
        pass


#: The engine owns the stop-reason vocabulary (openai4s/agent/engine.py,
#: loop.py, server/agent_run.py). Mapping it to the telemetry `outcome` enum is
#: done here, as a pure function, so it can be tested against the real values
#: rather than guessed at the call site.
#:
#: `submitted` is the engine's *normal* structured completion — the most common
#: web turn — and `plan` is a successful plan-mode exit. Both were absent, so
#: they fell to the unknown fallback and reported `error`, inverting the
#: success metric this exists to measure.
_OUTCOME = {
    "completed": "ok",
    "done": "ok",
    "stopped": "ok",
    "submitted": "ok",
    "plan": "ok",
    "cancelled": "cancelled",
    "max_turns": "timeout",
    "failed": "error",
}


def turn_outcome(stop_reason: Any) -> str:
    """A stop reason as one of the declared `outcome` enum members.

    Unknown reasons map to "error" rather than being dropped: a stop this
    function does not recognise is more likely a failure it has not seen than a
    success worth reporting as one.
    """
    return _OUTCOME.get(str(stop_reason or ""), "error")


def emit_session_start(root_frame_id: str, *, store: Any = None, **fields: Any) -> None:
    """`session_start`, at most once per session **per install identity**.

    The dedup mark is set only *after* consent, and it is keyed by
    ``(install_id, root_frame_id)`` rather than the frame alone. Both matter:

    * set-before-consent marked a default-off session seen, `emit` dropped the
      event, and a later opt-in on the same session sent turns with no
      session_start in front of them;
    * keying by frame alone meant a revoke-and-regrant mid-session — which mints
      a *new* install id — still found the frame marked, so the new
      participation period also had turns with no session_start. A new identity
      is a new participation period, and it gets its own session_start.
    """
    try:
        target = _store_for(store)
        if target is None:
            return
        consent = consent_mod.read(target)
        if consent is None:
            return
        key = (consent.install_id, root_frame_id)
        with _LOCK:
            if key in _SEEN_SESSIONS:
                return
            _SEEN_SESSIONS.add(key)
    except Exception:  # noqa: BLE001
        return
    emit("session_start", store=target, **fields)


def _dispatch(store: Any, install_id: str, batch: list[dict[str, Any]]) -> None:
    """Seal and hand to the transmission gate. Never on the caller's thread.

    This used to start a daemon thread per flush. ``_MAX_BUFFER`` bounded the
    *records* and nothing bounded the threads, sockets or payloads in flight,
    so at a high event rate — or against a collector that simply stalls —
    every event cleared the buffer and started another thread. A five-second
    stall was enough to turn an event rate into a thread rate inside the
    daemon. The gate owns one worker and one bounded queue; overflow is a
    counted drop, never a block on the caller.
    """
    from openai4s.telemetry import gate, wire

    payload = wire.seal(install_id, batch)
    if payload is None:
        return
    gate.submit(store, payload)


def _reset_for_tests() -> None:
    """Clear process-local dedup and buffer. Test hook only."""
    from openai4s.telemetry import gate

    with _LOCK:
        _BUFFER.clear()
        _SEEN_SESSIONS.clear()
    gate._reset_for_tests()


__all__ = ["emit", "emit_session_start"]

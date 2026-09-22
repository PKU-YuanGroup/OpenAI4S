"""Bounded background channel for task-mode shadow recording.

W4-A owns ``openai4s/judgment/shadow.py`` on a parallel branch. This module
is the W4-B-sized equivalent so the two packages do not share a file.
Merger may fold ``submit`` into a common ``shadow.submit``; the public
shape to preserve is:

* ``submit(*, request, rule_mode, explicit)`` — never raises, never
  blocks the caller, no-op when the ``task_mode_shadow`` capability is
  off or when ``explicit`` is true.
* audit event ``judgment_shadow`` with ``kind="task_mode"`` and
  ``{rule_mode, explicit, shadow_choice, probabilities, confidence,
  agree}``. The raw request text is never an audit field.

Queue capacity, two daemon workers, drop-when-full, and atexit draining
match the W4-A convention.
"""

from __future__ import annotations

import atexit
import queue
import threading
from typing import Any, Mapping

QUEUE_CAPACITY = 64
WORKER_COUNT = 2

_QUEUE: queue.Queue[object] = queue.Queue(maxsize=QUEUE_CAPACITY)
_STATE = threading.Lock()
_IDLE = threading.Condition(_STATE)
_RUNNING = threading.Event()
_RUNNING.set()
_PARKED = threading.Event()
_PARK = object()

_WORKERS: list[threading.Thread] = []
_GENERATION = 0
_IN_FLIGHT = 0
_SUBMITTED = 0
_DROPPED = 0
_PROCESSED = 0
_AGREE = 0
_DISAGREE = 0
_INDETERMINATE = 0
_SKIPPED_EXPLICIT = 0

_BOUND_ENABLED: bool | None = None
_BOUND_SERVICE: Any | None = None
_DEFAULT_SERVICE: Any | None = None

_ATEXIT_REGISTERED = False


def submit(*, request: str, rule_mode: str, explicit: bool) -> None:
    """Enqueue one shadow classify. Never raises. Never blocks."""

    try:
        _submit(
            request=str(request or ""),
            rule_mode=str(rule_mode),
            explicit=bool(explicit),
        )
    except Exception:  # noqa: BLE001 - shadow must not affect the caller
        return


def stats() -> dict[str, int]:
    """Counters for diagnostics and tests. No request text."""

    with _STATE:
        return {
            "submitted": _SUBMITTED,
            "dropped": _DROPPED,
            "processed": _PROCESSED,
            "agree": _AGREE,
            "disagree": _DISAGREE,
            "indeterminate": _INDETERMINATE,
            "skipped_explicit": _SKIPPED_EXPLICIT,
            "in_flight": _IN_FLIGHT,
            "pending": _QUEUE.qsize(),
        }


def bind(*, service: Any | None = None, enabled: bool | None = None) -> None:
    """Test injection. Production callers never need this."""

    global _BOUND_SERVICE, _BOUND_ENABLED
    with _STATE:
        if service is not None:
            _BOUND_SERVICE = service
        if enabled is not None:
            _BOUND_ENABLED = bool(enabled)


def wait_idle(timeout: float = 5.0) -> bool:
    """Block until the queue is empty and no worker is in flight."""

    with _IDLE:
        return _IDLE.wait_for(
            lambda: _QUEUE.empty() and _IN_FLIGHT == 0, timeout=timeout
        )


def pause_workers(timeout: float = 5.0) -> None:
    """Park workers so submitted jobs stay observably queued."""

    _PARKED.clear()
    _RUNNING.clear()
    with _STATE:
        alive = any(worker.is_alive() for worker in _WORKERS)
    if not alive:
        return
    try:
        _QUEUE.put_nowait(_PARK)
    except queue.Full:
        return
    _PARKED.wait(timeout=timeout)


def resume_workers() -> None:
    _PARKED.clear()
    _RUNNING.set()


def reset_for_tests() -> None:
    """Drain the queue, drop test binds, and zero counters. Daemon threads stay."""

    global _BOUND_SERVICE, _BOUND_ENABLED, _DEFAULT_SERVICE
    global _GENERATION, _SUBMITTED, _DROPPED, _PROCESSED
    global _AGREE, _DISAGREE, _INDETERMINATE, _SKIPPED_EXPLICIT, _IN_FLIGHT
    resume_workers()
    _discard_pending()
    with _STATE:
        _BOUND_SERVICE = None
        _BOUND_ENABLED = None
        _DEFAULT_SERVICE = None
        _GENERATION += 1
        _SUBMITTED = 0
        _DROPPED = 0
        _PROCESSED = 0
        _AGREE = 0
        _DISAGREE = 0
        _INDETERMINATE = 0
        _SKIPPED_EXPLICIT = 0
        _IN_FLIGHT = 0
        _IDLE.notify_all()


def _submit(*, request: str, rule_mode: str, explicit: bool) -> None:
    global _SUBMITTED, _DROPPED, _SKIPPED_EXPLICIT
    if explicit:
        with _STATE:
            _SKIPPED_EXPLICIT += 1
        return
    if not _capability_enabled():
        return
    _ensure_workers()
    with _STATE:
        generation = _GENERATION
        _SUBMITTED += 1
    try:
        _QUEUE.put_nowait((generation, request, rule_mode))
    except queue.Full:
        with _STATE:
            _DROPPED += 1
            _SUBMITTED -= 1


def _capability_enabled() -> bool:
    if _BOUND_ENABLED is not None:
        return _BOUND_ENABLED
    try:
        from openai4s.config import _strict_env_tristate

        master = _strict_env_tristate("OPENAI4S_EXPERIMENTAL_JUDGMENT")
        cap = _strict_env_tristate("OPENAI4S_JUDGMENT_TASK_MODE_SHADOW")
        if master is False or cap is False:
            return False
        if master is True and cap is True:
            return True
        from openai4s.config import get_config
        from openai4s.judgment.flags import resolve
        from openai4s.store import get_store

        cfg = get_config(initialize_dirs=False)
        store = get_store(cfg.db_path)
        return bool(resolve(cfg, store).task_mode_shadow.enabled)
    except Exception:  # noqa: BLE001 - off is the fail-safe
        return False


def _service() -> Any:
    if _BOUND_SERVICE is not None:
        return _BOUND_SERVICE
    global _DEFAULT_SERVICE
    with _STATE:
        if _DEFAULT_SERVICE is not None:
            return _DEFAULT_SERVICE
    from openai4s.config import get_config
    from openai4s.host.judgment import JudgmentService
    from openai4s.judgment.settings import resolve_settings_config
    from openai4s.store import get_store

    cfg = get_config()
    service = JudgmentService(
        lambda: resolve_settings_config(cfg, get_store(cfg.db_path)),
        lambda: get_store(cfg.db_path),
    )
    with _STATE:
        if _DEFAULT_SERVICE is None:
            _DEFAULT_SERVICE = service
        return _DEFAULT_SERVICE


def _ensure_workers() -> None:
    global _ATEXIT_REGISTERED
    with _STATE:
        alive = [worker for worker in _WORKERS if worker.is_alive()]
        _WORKERS[:] = alive
        needed = WORKER_COUNT - len(_WORKERS)
        for index in range(needed):
            worker = threading.Thread(
                target=_worker_loop,
                name=f"openai4s-task-mode-shadow-{index}",
                daemon=True,
            )
            _WORKERS.append(worker)
            worker.start()
        if not _ATEXIT_REGISTERED:
            atexit.register(_atexit_cleanup)
            _ATEXIT_REGISTERED = True


def _worker_loop() -> None:
    global _IN_FLIGHT
    while True:
        _RUNNING.wait()
        item = _QUEUE.get()
        if item is _PARK:
            _QUEUE.task_done()
            _PARKED.set()
            continue
        if not isinstance(item, tuple) or len(item) != 3:
            _QUEUE.task_done()
            continue
        generation, request, rule_mode = item
        if not isinstance(generation, int):
            _QUEUE.task_done()
            continue
        with _STATE:
            stale = generation != _GENERATION
            if not stale:
                _IN_FLIGHT += 1
        try:
            if not stale:
                _process(generation, str(request), str(rule_mode))
        except Exception:  # noqa: BLE001 - a failed shadow is not the user's
            pass
        finally:
            _QUEUE.task_done()
            if not stale:
                with _IDLE:
                    if generation == _GENERATION and _IN_FLIGHT > 0:
                        _IN_FLIGHT -= 1
                    _IDLE.notify_all()


def _process(generation: int, request: str, rule_mode: str) -> None:
    with _STATE:
        if generation != _GENERATION:
            return
    from openai4s.judgment.templates.task_mode import (
        PURPOSE,
        TEMPLATE_ID,
        shadow_agree,
    )

    service = _service()
    result = service.run(
        purpose=PURPOSE,
        template_id=TEMPLATE_ID,
        state={"request": request},
    )
    answer = None
    try:
        answer = result.answers.get("mode")
    except Exception:  # noqa: BLE001
        answer = None
    choice: str | None = None
    probabilities: Mapping[str, float] | None = None
    confidence: float | None = None
    if answer is not None and getattr(answer, "kind", None) == "choice":
        choice = str(answer.value)
        if answer.probabilities is not None:
            probabilities = dict(answer.probabilities)
        raw_conf = answer.confidence
        if raw_conf is not None:
            try:
                confidence = float(raw_conf)
            except (TypeError, ValueError):
                confidence = None
    agreed = shadow_agree(rule_mode, choice, confidence)
    with _STATE:
        if generation != _GENERATION:
            return
        global _PROCESSED, _AGREE, _DISAGREE, _INDETERMINATE
        _PROCESSED += 1
        if agreed is True:
            _AGREE += 1
        elif agreed is False:
            _DISAGREE += 1
        else:
            _INDETERMINATE += 1
    _audit(
        rule_mode=rule_mode,
        shadow_choice=choice,
        probabilities=probabilities,
        confidence=confidence,
        agree=agreed,
        status=getattr(result, "status", None),
        latency_ms=getattr(result, "latency_ms", None),
        state_sha256=getattr(result, "state_sha256", None),
    )


def _audit(
    *,
    rule_mode: str,
    shadow_choice: str | None,
    probabilities: Mapping[str, float] | None,
    confidence: float | None,
    agree: bool | None,
    status: str | None,
    latency_ms: int | None,
    state_sha256: str | None,
) -> None:
    payload: dict[str, Any] = {
        "kind": "task_mode",
        "rule_mode": rule_mode,
        "explicit": False,
        "shadow_choice": shadow_choice,
        "probabilities": dict(probabilities) if probabilities is not None else None,
        "confidence": confidence,
        "agree": agree,
        "status": status,
        "latency_ms": latency_ms,
        "state_sha256": state_sha256,
    }
    try:
        from openai4s.observability import log_event

        log_event("judgment_shadow", **payload)
    except Exception:  # noqa: BLE001 - auditing must not fail the caller
        return


def _discard_pending() -> None:
    while True:
        try:
            item = _QUEUE.get_nowait()
        except queue.Empty:
            break
        _QUEUE.task_done()
        del item


def _atexit_cleanup() -> None:
    """Non-blocking: drop queued work; do not join in-flight workers."""

    try:
        _discard_pending()
    except Exception:  # noqa: BLE001
        return


__all__ = [
    "QUEUE_CAPACITY",
    "WORKER_COUNT",
    "bind",
    "pause_workers",
    "reset_for_tests",
    "resume_workers",
    "stats",
    "submit",
    "wait_idle",
]

"""Bounded background channel for task-mode shadow recording.

W4-A owns ``openai4s/judgment/shadow.py`` on a parallel branch. This module
is the W4-B-sized equivalent so the two packages do not share a file.
Merger may fold ``submit`` into a common ``shadow.submit``; the public
shape to preserve is:

* ``submit(*, request, rule_mode, explicit, cfg)`` — never raises, never
  blocks the caller, no-op when the ``task_mode_shadow`` capability is
  off, when ``explicit`` is true, or when no ``cfg`` is handed over.
* audit event ``judgment_shadow`` with ``kind="task_mode"`` and
  ``{rule_mode, explicit, shadow_choice, probabilities, confidence,
  agree}``. The raw request text is never an audit field.

``cfg`` is the Config of the run that resolved the mode, and both the
capability check and the background judgment read that run's data dir. They
used to read ``get_config()`` instead, which is only the run's data dir inside
the daemon. A caller with its own (a benchmark case, a capture drive, any
embedder) got ``~/.openai4s``: on 2026-10-08 a benchmark's delegated child
opened a developer's real database here and migrated it to an unreleased
schema. Without a ``cfg`` the shadow stays off and opens nothing.

Queue capacity, two daemon workers, drop-when-full, and atexit draining
match the W4-A convention.
"""

from __future__ import annotations

import atexit
import queue
import threading
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from openai4s.config import Config

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
# The service for the last run Config seen, as ``(cfg, service)``. Reused only
# for that same object (a daemon hands over one Config for every turn), so a
# run's judgment never reads another run's data dir.
_RUN_SERVICE: tuple[Any, Any] | None = None

_ATEXIT_REGISTERED = False


def submit(
    *,
    request: str,
    rule_mode: str,
    explicit: bool,
    cfg: Config | None = None,
) -> None:
    """Enqueue one shadow classify. Never raises. Never blocks.

    ``cfg`` is the triggering run's Config; without one this is a no-op.
    """

    try:
        _submit(
            request=str(request or ""),
            rule_mode=str(rule_mode),
            explicit=bool(explicit),
            cfg=cfg,
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
    """Wait until every accepted queue item has finished processing."""

    # A worker can dequeue the last item before incrementing _IN_FLIGHT;
    # reset_for_tests can also clear that counter while a job is still running.
    # Queue's completion accounting covers both gaps and notifies only after
    # task_done(), including stale jobs and parked-worker sentinels.
    with _QUEUE.all_tasks_done:
        return _QUEUE.all_tasks_done.wait_for(
            lambda: _QUEUE.unfinished_tasks == 0, timeout=timeout
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

    global _BOUND_SERVICE, _BOUND_ENABLED, _RUN_SERVICE
    global _GENERATION, _SUBMITTED, _DROPPED, _PROCESSED
    global _AGREE, _DISAGREE, _INDETERMINATE, _SKIPPED_EXPLICIT, _IN_FLIGHT
    resume_workers()
    _discard_pending()
    with _STATE:
        _BOUND_SERVICE = None
        _BOUND_ENABLED = None
        _RUN_SERVICE = None
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


def _submit(
    *, request: str, rule_mode: str, explicit: bool, cfg: Config | None
) -> None:
    global _SUBMITTED, _DROPPED, _SKIPPED_EXPLICIT
    if explicit:
        with _STATE:
            _SKIPPED_EXPLICIT += 1
        return
    if not _capability_enabled(cfg):
        return
    _ensure_workers()
    with _STATE:
        generation = _GENERATION
        _SUBMITTED += 1
    try:
        _QUEUE.put_nowait((generation, request, rule_mode, cfg))
    except queue.Full:
        with _STATE:
            _DROPPED += 1
            _SUBMITTED -= 1


def _capability_enabled(cfg: Config | None) -> bool:
    if _BOUND_ENABLED is not None:
        return _BOUND_ENABLED
    try:
        from openai4s.config import _strict_env_tristate

        master = _strict_env_tristate("OPENAI4S_EXPERIMENTAL_JUDGMENT")
        cap = _strict_env_tristate("OPENAI4S_JUDGMENT_TASK_MODE_SHADOW")
        if master is False or cap is False:
            return False
        if cfg is None:
            # Never fall back to get_config(): outside the daemon the process
            # default is a data dir nobody chose for this run.
            return False
        if master is True and cap is True:
            return True
        from openai4s.judgment.flags import resolve
        from openai4s.store import get_store

        store = get_store(cfg.db_path)
        return bool(resolve(cfg, store).task_mode_shadow.enabled)
    except Exception:  # noqa: BLE001 - off is the fail-safe
        return False


def _service(cfg: Config | None) -> Any | None:
    if _BOUND_SERVICE is not None:
        return _BOUND_SERVICE
    if cfg is None:
        return None
    global _RUN_SERVICE
    with _STATE:
        if _RUN_SERVICE is not None and _RUN_SERVICE[0] is cfg:
            return _RUN_SERVICE[1]
    from openai4s.host.judgment import JudgmentService
    from openai4s.judgment.settings import resolve_settings_config
    from openai4s.store import get_store

    service = JudgmentService(
        lambda: resolve_settings_config(cfg, get_store(cfg.db_path)),
        lambda: get_store(cfg.db_path),
    )
    with _STATE:
        if _RUN_SERVICE is None or _RUN_SERVICE[0] is not cfg:
            _RUN_SERVICE = (cfg, service)
        return _RUN_SERVICE[1]


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
        if not isinstance(item, tuple) or len(item) != 4:
            _QUEUE.task_done()
            continue
        generation, request, rule_mode, cfg = item
        if not isinstance(generation, int):
            _QUEUE.task_done()
            continue
        with _STATE:
            stale = generation != _GENERATION
            if not stale:
                _IN_FLIGHT += 1
        try:
            if not stale:
                _process(generation, str(request), str(rule_mode), cfg)
        except Exception:  # noqa: BLE001 - a failed shadow is not the user's
            pass
        finally:
            _QUEUE.task_done()
            if not stale:
                with _IDLE:
                    if generation == _GENERATION and _IN_FLIGHT > 0:
                        _IN_FLIGHT -= 1
                    _IDLE.notify_all()


def _process(generation: int, request: str, rule_mode: str, cfg: Config | None) -> None:
    with _STATE:
        if generation != _GENERATION:
            return
    from openai4s.judgment.templates.task_mode import (
        PURPOSE,
        TEMPLATE_ID,
        shadow_agree,
    )

    service = _service(cfg)
    if service is None:
        return
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

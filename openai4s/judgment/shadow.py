"""Bounded background shadow judgments for the three safety gates.

``submit`` never changes a verdict, never blocks the caller, and swallows
every exception. When ``safety_shadow`` is off it returns immediately and
starts no threads.
"""

from __future__ import annotations

import atexit
import os
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

QUEUE_CAPACITY = 64
WORKER_COUNT = 2
DISAGREE_HASH_CAP = 8

_TRUE = frozenset(("1", "true", "yes", "on"))
_FALSE = frozenset(("0", "false", "no", "off"))

_KINDS = ("code", "injection", "trajectory", "bio_prescan")

_STOP = object()


@dataclass(frozen=True)
class _Job:
    generation: int
    kind: str
    state: dict[str, Any]
    existing_verdict: object


_lock = threading.Lock()
_queue: queue.Queue[Any] = queue.Queue(maxsize=QUEUE_CAPACITY)
_workers: list[threading.Thread] = []
_started = False
_atexit_registered = False
_allow_workers = True
_generation = 0
_backend_factory: Callable[[], Any] | None = None
_service: Any = None

_submitted = 0
_dropped = 0
_inflight = 0
_agree_counts: dict[str, int] = {kind: 0 for kind in _KINDS}
_disagree_counts: dict[str, int] = {kind: 0 for kind in _KINDS}
_indeterminate_counts: dict[str, int] = {kind: 0 for kind in _KINDS}
_disagree_hashes: dict[str, list[str]] = {kind: [] for kind in _KINDS}


def _env_token(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = str(raw).strip().lower()
    return value if value else None


def _env_false(raw: str | None) -> bool:
    return raw is not None and raw in _FALSE


def _env_true(raw: str | None) -> bool:
    return raw is not None and raw in _TRUE


def _capability_on() -> bool:
    """Cheap enable check. Env kill-switch / env-on skip Store."""

    master = _env_token("OPENAI4S_EXPERIMENTAL_JUDGMENT")
    cap = _env_token("OPENAI4S_JUDGMENT_SAFETY_SHADOW")
    if _env_false(master) or _env_false(cap):
        return False
    if _env_true(master) and _env_true(cap):
        return True
    try:
        from openai4s.config import get_config

        cfg = get_config()
        from openai4s.judgment.flags import resolve
        from openai4s.store import get_store

        # Settings and disclosure may change while the daemon is running.
        # A cache keyed only by environment/database path would retain an old
        # off decision after the user enables the capability in Customize.
        flags = resolve(cfg, get_store(cfg.db_path))
        return bool(flags.master.enabled and flags.safety_shadow.enabled)
    except Exception:
        return False


def _live_config() -> Any:
    from dataclasses import replace

    from openai4s.config import ExperimentalJudgmentFlags, get_config
    from openai4s.judgment.settings import resolve_settings_config
    from openai4s.store import get_store

    base = get_config()
    live = ExperimentalJudgmentFlags()
    if live != base.experimental_judgment:
        try:
            base = replace(base, experimental_judgment=live)
        except Exception:
            pass
    return resolve_settings_config(base, get_store(base.db_path))


def _store() -> Any:
    from openai4s.store import get_store

    return get_store(_live_config().db_path)


def _get_service() -> Any:
    global _service
    with _lock:
        if _service is not None:
            return _service
        from openai4s.host.judgment import JudgmentService

        _service = JudgmentService(
            cfg_provider=_live_config,
            store_provider=_store,
            backend_factory=_backend_factory,
        )
        return _service


def _empty_kind_stats() -> dict[str, dict[str, int]]:
    return {kind: {"agree": 0, "disagree": 0, "indeterminate": 0} for kind in _KINDS}


def _snapshot_stats() -> dict[str, Any]:
    kinds = _empty_kind_stats()
    hashes: dict[str, list[str]] = {}
    for kind in _KINDS:
        kinds[kind] = {
            "agree": _agree_counts[kind],
            "disagree": _disagree_counts[kind],
            "indeterminate": _indeterminate_counts[kind],
        }
        hashes[kind] = list(_disagree_hashes[kind])
    return {
        "submitted": _submitted,
        "dropped": _dropped,
        "kinds": kinds,
        "disagree_sha256": hashes,
    }


def _note_dropped() -> None:
    global _dropped
    with _lock:
        _dropped += 1


def _note_submitted() -> None:
    global _submitted
    with _lock:
        _submitted += 1


def _note_result(kind: str, agree: bool | None, state_sha256: str) -> None:
    bucket = kind if kind in _agree_counts else _KINDS[0]
    with _lock:
        if agree is True:
            _agree_counts[bucket] += 1
        elif agree is False:
            _disagree_counts[bucket] += 1
            if state_sha256:
                held = _disagree_hashes[bucket]
                held.append(state_sha256)
                overflow = len(held) - DISAGREE_HASH_CAP
                if overflow > 0:
                    del held[0:overflow]
        else:
            _indeterminate_counts[bucket] += 1


def _answers_payload(result: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    answers = getattr(result, "answers", None) or {}
    if not isinstance(answers, Mapping):
        return payload
    for key, answer in answers.items():
        to_dict = getattr(answer, "to_dict", None)
        if callable(to_dict):
            payload[str(key)] = to_dict()
        else:
            payload[str(key)] = answer
    return payload


def _run_job(job: _Job) -> None:
    from openai4s.judgment.templates import safety as safety_templates
    from openai4s.observability import log_event

    template_id = safety_templates.KIND_TO_TEMPLATE.get(job.kind)
    if template_id is None:
        _note_result(job.kind, None, "")
        return
    result = _get_service().run(
        purpose=safety_templates.PURPOSE,
        template_id=template_id,
        state=job.state,
    )
    if job.generation != _generation:
        return
    answers = getattr(result, "answers", {}) or {}
    status = str(getattr(result, "status", "") or "")
    agree = safety_templates.agree(
        job.kind, answers, job.existing_verdict, status=status
    )
    state_sha256 = str(getattr(result, "state_sha256", "") or "")
    _note_result(job.kind, agree, state_sha256)
    log_event(
        "judgment_shadow",
        kind=job.kind,
        existing_verdict=job.existing_verdict,
        shadow_answers=_answers_payload(result),
        agree=agree,
        status=status,
        latency_ms=int(getattr(result, "latency_ms", 0) or 0),
        state_sha256=state_sha256,
    )


def _worker() -> None:
    global _inflight
    while True:
        try:
            item = _queue.get()
        except Exception:
            continue
        try:
            if item is _STOP:
                return
            if not isinstance(item, _Job):
                continue
            if item.generation != _generation:
                continue
            with _lock:
                _inflight += 1
            try:
                _run_job(item)
            finally:
                with _lock:
                    _inflight -= 1
        except Exception:
            pass
        finally:
            try:
                _queue.task_done()
            except Exception:
                pass


def _atexit_cleanup() -> None:
    """Non-blocking. Daemon workers die with the process; do not join."""

    for _ in range(WORKER_COUNT):
        try:
            _queue.put_nowait(_STOP)
        except Exception:
            return


def _ensure_workers() -> None:
    global _started, _atexit_registered
    if _started or not _allow_workers:
        return
    with _lock:
        if _started or not _allow_workers:
            return
        for index in range(WORKER_COUNT):
            thread = threading.Thread(
                target=_worker,
                name=f"openai4s-judgment-shadow-{index}",
                daemon=True,
            )
            thread.start()
            _workers.append(thread)
        if not _atexit_registered:
            atexit.register(_atexit_cleanup)
            _atexit_registered = True
        _started = True


def submit(kind: str, *, state: Mapping[str, Any], existing_verdict: object) -> None:
    """Queue one shadow judgment. Returns immediately. Never raises."""

    try:
        if not _capability_on():
            return
        payload = dict(state)
        job = _Job(
            generation=_generation,
            kind=str(kind),
            state=payload,
            existing_verdict=existing_verdict,
        )
        _ensure_workers()
        try:
            _queue.put_nowait(job)
        except queue.Full:
            _note_dropped()
            _note_submitted()
            return
        _note_submitted()
    except Exception:
        return


def stats() -> dict[str, Any]:
    """Cumulative submitted / dropped counts and per-kind agreement."""

    with _lock:
        return _snapshot_stats()


def set_backend_factory(factory: Callable[[], Any] | None) -> None:
    """Tests inject a JudgmentBackend factory. Production leaves this None."""

    global _backend_factory, _service
    with _lock:
        _backend_factory = factory
        _service = None


def set_allow_workers(allow: bool) -> None:
    """Tests can fill the queue without starting workers."""

    global _allow_workers
    _allow_workers = bool(allow)


def reset_for_tests() -> None:
    """Drop queued work, reset counters, and forget test doubles."""

    global _generation, _submitted, _dropped, _service
    global _backend_factory, _allow_workers
    with _lock:
        _generation += 1
        _submitted = 0
        _dropped = 0
        for kind in _KINDS:
            _agree_counts[kind] = 0
            _disagree_counts[kind] = 0
            _indeterminate_counts[kind] = 0
            _disagree_hashes[kind] = []
        _service = None
        _backend_factory = None
        _allow_workers = True
    while True:
        try:
            _queue.get_nowait()
        except queue.Empty:
            break
        try:
            _queue.task_done()
        except Exception:
            pass


def wait_idle(timeout_s: float = 5.0) -> None:
    """Block until the queue is empty. Tests only. Never used in production."""

    import time

    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        with _lock:
            inflight = _inflight
        pending = getattr(_queue, "unfinished_tasks", 0)
        if inflight == 0 and pending == 0:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("judgment shadow workers did not drain")
        time.sleep(0.01)


__all__ = [
    "QUEUE_CAPACITY",
    "WORKER_COUNT",
    "reset_for_tests",
    "set_allow_workers",
    "set_backend_factory",
    "stats",
    "submit",
    "wait_idle",
]

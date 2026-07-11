"""Session lifecycle reconciliation and deterministic idle-kernel sweeping.

This is intentionally a lifecycle foundation, not namespace recovery.  A
generation left live by an older daemon is marked ``abandoned`` and remains
auditable; no code here deserializes objects or claims that memory survived.
"""

from __future__ import annotations

import math
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping

PROCESS_INSTANCE_ID = f"daemon-{uuid.uuid4()}"


def kernel_idle_ttl(
    environ: Mapping[str, str] | None = None,
    *,
    default: float = 0.0,
) -> float:
    """Return the configured TTL in seconds; zero disables automatic release."""

    source = os.environ if environ is None else environ
    raw = source.get("OPENAI4S_KERNEL_IDLE_TTL")

    def normalize(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) and number > 0 else 0.0

    fallback = normalize(default)
    if raw is None or not str(raw).strip():
        return fallback
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) and value > 0 else 0.0


class SessionRecoveryService:
    """Own daemon reconciliation, recovery occupancy, and the idle sweeper.

    Every external fact is injected so ``sweep_once`` can be tested without a
    thread, a real kernel, wall-clock sleeps, or a Web server.
    """

    def __init__(
        self,
        *,
        store: Any,
        sessions: Callable[[], Iterable[Any]],
        turn_active: Callable[[str], bool],
        approval_pending: Callable[[str], bool],
        background_active: Callable[[Any], bool],
        release_idle: Callable[[Any, str], bool],
        background_last_activity_ms: Callable[[Any], int | None] | None = None,
        owner_instance_id: str = PROCESS_INSTANCE_ID,
        ttl_s: float | None = None,
        sweep_interval_s: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self._sessions = sessions
        self._turn_active = turn_active
        self._approval_pending = approval_pending
        self._background_active = background_active
        self._background_last_activity_ms = (
            background_last_activity_ms or (lambda _session: None)
        )
        self._release_idle = release_idle
        self.owner_instance_id = owner_instance_id
        self.ttl_s = (
            kernel_idle_ttl()
            if ttl_s is None
            else kernel_idle_ttl(
                {"OPENAI4S_KERNEL_IDLE_TTL": str(ttl_s)}
            )
        )
        self.sweep_interval_s = (
            self._default_interval(self.ttl_s)
            if sweep_interval_s is None
            else max(0.01, sweep_interval_s)
        )
        self._clock = clock or time.time
        self._recovering: set[str] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _default_interval(ttl_s: float) -> float:
        return min(60.0, max(1.0, ttl_s / 4.0 if ttl_s else 60.0))

    def reconcile_startup(self) -> int:
        """Mark only older-daemon live rows abandoned, never recovered."""

        now = self.now_ms()
        generations = self.store.abandon_live_kernel_generations(
            owner_instance_id=self.owner_instance_id,
            reason="daemon_restart",
            ended_at=now,
        )
        abandon_attempts = getattr(
            self.store, "abandon_incomplete_execution_attempts", None
        )
        if callable(abandon_attempts):
            abandon_attempts(
                owner_instance_id=self.owner_instance_id,
                finished_at=now,
            )
        return generations

    def start(self) -> bool:
        """Start one explicit sweeper thread when TTL release is enabled."""

        with self._lock:
            if self.ttl_s <= 0 or (
                self._thread is not None and self._thread.is_alive()
            ):
                return False
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                name="openai4s-kernel-idle-sweeper",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the sweeper explicitly; safe before start and after stop."""

        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        with self._lock:
            if self._thread is thread and (
                thread is None or not thread.is_alive()
            ):
                self._thread = None

    close = stop

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def now_ms(self) -> int:
        return int(self._clock() * 1000)

    def touch(
        self,
        session: Any,
        language: str | None = None,
        *,
        state: str | None = None,
        at_ms: int | None = None,
    ) -> int:
        return int(
            session.kernels.touch(
                language,
                state=state,
                at_ms=self.now_ms() if at_ms is None else at_ms,
            )
        )

    def begin_recovery(self, session: Any) -> None:
        root_frame_id = str(session.root_frame_id)
        with self._lock:
            self._recovering.add(root_frame_id)
        self.touch(session, state="recovering")

    def end_recovery(self, session: Any) -> None:
        root_frame_id = str(session.root_frame_id)
        self.touch(session, state="active")
        with self._lock:
            self._recovering.discard(root_frame_id)

    @contextmanager
    def recovery_scope(self, session: Any) -> Iterator[None]:
        self.begin_recovery(session)
        try:
            yield
        finally:
            self.end_recovery(session)

    def is_recovering(self, root_frame_id: str) -> bool:
        with self._lock:
            return root_frame_id in self._recovering

    def blocked(self, session: Any) -> bool:
        root_frame_id = str(session.root_frame_id)
        return bool(
            self._turn_active(root_frame_id)
            or self._approval_pending(root_frame_id)
            or self._background_active(session)
            or self.is_recovering(root_frame_id)
        )

    def idle_expired(self, session: Any, *, now_ms: int | None = None) -> bool:
        """Re-evaluate TTL against the latest activity for barrier race checks."""

        if self.ttl_s <= 0:
            return False
        now = self.now_ms() if now_ms is None else int(now_ms)
        live = [
            state
            for state in session.kernels.status().values()
            if state.get("alive")
        ]
        if not live:
            return False
        background_at = self._background_last_activity_ms(session)
        if background_at is not None:
            recorded = max(
                int(state.get("last_activity_at") or 0) for state in live
            )
            if background_at > recorded:
                self.touch(session, at_ms=background_at)
                live = [
                    state
                    for state in session.kernels.status().values()
                    if state.get("alive")
                ]
        observed: list[int] = []
        for state in live:
            value = state.get("last_activity_at")
            if value is None:
                value = state.get("started_at")
            observed.append(now if value is None else int(value))
        last_activity = max(observed)
        return last_activity < now - int(self.ttl_s * 1000)

    def sweep_once(self) -> list[str]:
        """Release eligible sessions once and return their root frame IDs."""

        if self.ttl_s <= 0:
            return []
        now = self.now_ms()
        released: list[str] = []
        for session in list(self._sessions()):
            if not self.idle_expired(session, now_ms=now) or self.blocked(session):
                continue
            if self._release_idle(session, "idle_ttl"):
                released.append(str(session.root_frame_id))
        return released

    def _run(self) -> None:
        while not self._stop.wait(self.sweep_interval_s):
            try:
                self.sweep_once()
            except Exception:  # noqa: BLE001 — one sweep cannot kill the daemon
                continue


__all__ = [
    "PROCESS_INSTANCE_ID",
    "SessionRecoveryService",
    "kernel_idle_ttl",
]

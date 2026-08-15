"""Leases that lapse, so an idle session stops holding a GPU (M3b-4).

The reclaimer states an intent and stops: an expired lease becomes
`request_stop(reason=...)`, and the reconciler's cancel barrier does the
actual releasing. That split is not ceremony. The barrier has a fixed
order, is re-entrant, and is the single place a resource is given back; a
reclaimer that called the backend itself would be a second, subtly
different teardown path — and the two would drift on exactly the failure
where it matters.

**A worker being alive is not a user being present.** This is the whole
subtlety of the feature, and it is easy to get backwards. A cluster
session whose process is healthy, whose socket is connected and whose
heartbeats arrive on schedule is still idle if nobody has run anything in
it — and it is still holding the resource somebody else is queued for. So
this loop reads `last_active_at` and never writes it, and nothing in the
transport or the watchdog writes it either. Only a user executing
something, or explicitly asking to keep the session, does.

Which clock ran out decides the reason code, and the reason code is what
the user reads afterwards: `SESSION_IDLE_TIMEOUT` means "come back and it
will be here again", `SESSION_MAX_LIFETIME_EXCEEDED` means "this one is
over regardless of what you do". Reporting the first when it was the
second sends a user to retry a thing that cannot work.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from openai4s.orchestration.models import Reason

#: How often expiry is checked. A minute: the deadlines are hours, and a
#: loop that woke every second would spend a day's CPU to release a
#: resource fifty-nine seconds sooner.
DEFAULT_SWEEP_S = 60.0

_REASON_FOR = {
    "idle": Reason.SESSION_IDLE_TIMEOUT,
    "max_lifetime": Reason.SESSION_MAX_LIFETIME_EXCEEDED,
}


@dataclass
class SweepReport:
    """What one pass did — the shape the tests assert against."""

    examined: int = 0
    expired: int = 0
    stopped: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class LeaseReclaimer:
    """Sweeps live leases and asks for a stop when one has lapsed."""

    def __init__(
        self,
        *,
        leases: Any,
        workloads: Any,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sweep_s: float = DEFAULT_SWEEP_S,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._leases = leases
        self._workloads = workloads
        self._clock_ms = clock_ms
        self._sweep_s = sweep_s
        self._on_event = on_event or (lambda kind, payload: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.sweeps = 0
        self.last_error: str | None = None
        self.exited_reason: str | None = None

    # --- the loop ---------------------------------------------------------

    def running(self) -> bool:
        """Is the loop actually sweeping? Not 'does a thread object exist' —
        a loop that exited on its own leaves the attribute set, and reading
        that as 'already started' means it can never be restarted."""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self.running():
            return
        self._thread = None
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="orchestration-lease-reclaimer", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweeps += 1
                self.sweep()
            except Exception as exc:  # noqa: BLE001 — a sweep must not kill it
                self.last_error = f"{type(exc).__name__}: {exc}"
                if _store_is_gone(exc):
                    # The database this loop exists to sweep is closed, so
                    # the world it was built for is over. Exiting beats an
                    # endless stream of "closed database" lines from a
                    # thread outliving the process state it depends on.
                    self.exited_reason = "store closed"
                    self._stop.set()
                    return
            self._stop.wait(self._sweep_s)
        self.exited_reason = self.exited_reason or "stopped"

    # --- one pass ---------------------------------------------------------

    def sweep(self) -> SweepReport:
        report = SweepReport()
        now = self._clock_ms()
        for lease in self._leases.live_leases():
            report.examined += 1
            expiry = lease.expiry(now)
            if expiry is None:
                continue
            report.expired += 1
            reason = _REASON_FOR[expiry]
            try:
                stopped = self._workloads.request_stop(lease.workload_id, reason=reason)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"{lease.workload_id}: {exc}")
                self.last_error = f"{lease.workload_id}: {type(exc).__name__}: {exc}"
                if _store_is_gone(exc):
                    raise
                # Leave the lease live and move on. One workload that
                # cannot be stopped must not stop the other ninety-nine
                # from being reclaimed; and not releasing the row is the
                # point — a stop that could not be recorded has not
                # happened, so the next sweep must try again rather than
                # inherit a lease it believes was dealt with.
                continue
            # Released whether or not the stop landed. `request_stop`
            # returning False means the workload is already terminal — the
            # resource is gone and the lease has nothing left to govern, so
            # keeping the row live would re-examine a dead session forever.
            self._leases.release(lease.workload_id)
            if stopped:
                report.stopped += 1
            self._emit(
                "lease_expired",
                {
                    "workload_id": lease.workload_id,
                    "expiry": expiry,
                    "reason": reason.value,
                    "stop_requested": bool(stopped),
                },
            )
        return report

    def _emit(self, kind: str, payload: dict) -> None:
        try:
            self._on_event(kind, payload)
        except Exception:  # noqa: BLE001
            pass


def _store_is_gone(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "closed database" in text or "cannot operate on a closed database" in text


__all__ = ["DEFAULT_SWEEP_S", "LeaseReclaimer", "SweepReport"]

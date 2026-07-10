"""Protocol-neutral timeout recovery for one supervised kernel cell.

The watchdog knows nothing about Web sessions, stores, artifacts, or task
completion.  It runs one callable against a frozen ``KernelLease`` and applies
the namespace-preserving interrupt -> exact kill -> restart/abandon ladder.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TypeVar

from openai4s.kernel.supervisor import KernelLease, KernelSupervisor

T = TypeVar("T")
Flag = Callable[[], bool]


def _never() -> bool:
    return False


@dataclass(frozen=True)
class WatchdogPolicy:
    """Timing policy for a long-running persistent-kernel cell."""

    timeout_s: float = 900.0
    poll_s: float = 1.0
    interrupt_grace_s: float = 10.0
    kill_grace_s: float = 10.0

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        poll_s: float = 1.0,
        interrupt_grace_s: float = 10.0,
        kill_grace_s: float = 10.0,
    ) -> "WatchdogPolicy":
        source = os.environ if environ is None else environ
        try:
            timeout_s = float(source.get("OPENAI4S_CELL_TIMEOUT", "900") or 900)
        except (TypeError, ValueError):
            timeout_s = 900.0
        return cls(
            timeout_s=timeout_s,
            poll_s=poll_s,
            interrupt_grace_s=interrupt_grace_s,
            kill_grace_s=kill_grace_s,
        )

    @property
    def enabled(self) -> bool:
        return math.isfinite(self.timeout_s) and self.timeout_s > 0


def execute_with_watchdog(
    supervisor: KernelSupervisor,
    lease: KernelLease,
    run: Callable[[Any], T],
    *,
    policy: WatchdogPolicy,
    cancelled: Flag = _never,
    paused: Flag = _never,
    after_restart: Callable[[Any], None] | None = None,
    thread_name: str | None = None,
) -> T:
    """Run one exact lease and recover a worker that stops producing frames.

    ``paused`` freezes the timeout budget while a human permission decision is
    pending. Cancellation still cuts through a pause. A hard recovery always
    raises ``TimeoutError``; a successful SIGINT may return the cell's normal
    interrupted result so the caller can persist it before observing cancel.
    """
    kernel = lease.kernel
    if not policy.enabled:
        return run(kernel)

    box: dict[str, Any] = {}

    def invoke() -> None:
        try:
            box["result"] = run(kernel)
        except BaseException as error:  # noqa: BLE001 — relay on the owner thread
            box["error"] = error

    worker = threading.Thread(target=invoke, name=thread_name, daemon=True)
    worker.start()
    remaining = policy.timeout_s
    poll_s = max(0.001, policy.poll_s)
    while remaining > 0:
        slice_s = min(remaining, poll_s)
        worker.join(slice_s)
        if not worker.is_alive():
            return _completed(box)
        if _flag(cancelled):
            break
        if _flag(paused):
            continue
        remaining -= slice_s

    supervisor.interrupt_if_current(lease)
    worker.join(max(0.0, policy.interrupt_grace_s))
    if not worker.is_alive():
        if "error" in box:
            raise box["error"]
        if "result" in box:
            return box["result"]
        return {
            "stdout": "",
            "stderr": "",
            "error": f"cell interrupted after exceeding {int(policy.timeout_s)}s",
        }

    supervisor.kill_if_current(lease)
    worker.join(max(0.0, policy.kill_grace_s))
    if worker.is_alive():
        supervisor.abandon_if_current(lease)
    else:
        try:
            restarted = supervisor.restart_if_current(lease)
            if restarted is not None and after_restart is not None:
                try:
                    after_restart(restarted.kernel)
                except Exception:
                    supervisor.shutdown_if_current(restarted)
        except Exception:  # noqa: BLE001 — next ensure lazily recovers the slot
            pass
    raise TimeoutError(
        f"cell exceeded {int(policy.timeout_s)}s with no result and was stopped; "
        "the kernel was reset (variables from earlier cells were cleared). Break "
        "the work into smaller steps, or raise OPENAI4S_CELL_TIMEOUT."
    )


def _completed(box: dict[str, Any]) -> Any:
    if "error" in box:
        raise box["error"]
    return box["result"]


def _flag(probe: Flag) -> bool:
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 — a telemetry probe cannot strand a reader
        return False


__all__ = ["WatchdogPolicy", "execute_with_watchdog"]

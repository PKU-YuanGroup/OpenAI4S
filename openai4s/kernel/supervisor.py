"""Session-level ownership for Python and R kernel worker slots.

The supervisor never reads protocol frames and never proxies ``execute``.  A
``Kernel`` still owns its single synchronous frame reader; this class only
coordinates worker identity, lifecycle, and ABA-safe watchdog recovery.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Hashable

KernelFactory = Callable[[], Any]


@dataclass(frozen=True)
class KernelLease:
    language: str
    key: Hashable | None
    generation: int
    kernel: Any


@dataclass
class _Slot:
    key: Hashable | None = None
    factory: KernelFactory | None = None
    kernel: Any = None
    generation: int = -1
    manual_stop: bool = False


class KernelSupervisor:
    """Own long-lived language kernel slots without touching their protocol.

    Callers must hold their session execution barrier around ``ensure``,
    ``restart`` and ``stop`` so none can race a direct ``Kernel.execute`` frame
    reader. ``interrupt``, ``kill_if_current`` and ``abandon_if_current`` are
    the only operations intended for concurrent watchdog/cancellation paths.
    """

    def __init__(self) -> None:
        self._slots: dict[str, _Slot] = {}
        self._lock = threading.RLock()

    def ensure(
        self, language: str, key: Hashable | None, factory: KernelFactory
    ) -> KernelLease:
        """Return a live matching worker, replacing it only when necessary."""
        with self._lock:
            slot = self._slot(language)
            current = slot.kernel
            if current is not None and slot.key == key and self._alive(current):
                slot.factory = factory
                slot.manual_stop = False
                return self._lease(language, slot)

            # Build first: a failed replacement must not destroy a usable worker.
            replacement = self._create_live(language, factory)
            old = slot.kernel
            slot.kernel = replacement
            slot.key = key
            slot.factory = factory
            slot.generation += 1
            slot.manual_stop = False
            if old is not None:
                self._shutdown(old)
            return self._lease(language, slot)

    def lease(self, language: str) -> KernelLease | None:
        with self._lock:
            slot = self._slots.get(language)
            return self._lease(language, slot) if slot and slot.kernel else None

    current = lease

    def kernel(self, language: str) -> Any:
        lease = self.lease(language)
        return lease.kernel if lease is not None else None

    def alive(self, language: str) -> bool:
        kernel = self.kernel(language)
        return kernel is not None and self._alive(kernel)

    def status(self, language: str | None = None) -> dict:
        with self._lock:
            names = [language] if language is not None else sorted(self._slots)
            states = {
                name: self._slot_status(name, self._slots.get(name)) for name in names
            }
        return states[language] if language is not None else states

    def interrupt(self, language: str | None = None) -> int:
        with self._lock:
            names = [language] if language is not None else list(self._slots)
            count = 0
            for name in names:
                slot = self._slots.get(name)
                if slot is None or slot.kernel is None:
                    continue
                count += 1
                try:
                    slot.kernel.interrupt()
                except Exception:  # noqa: BLE001 — interruption is best-effort
                    pass
            return count

    def stop(self, language: str | None = None, *, manual: bool = True) -> int:
        with self._lock:
            if language is not None:
                # An explicit stop is meaningful even before the first worker:
                # callers can distinguish a user-stopped session from one that
                # has never been started.
                self._slot(language)
                names = [language]
            else:
                names = list(self._slots)
            kernels: list[Any] = []
            for name in names:
                slot = self._slots.get(name)
                if slot is None:
                    continue
                if slot.kernel is not None:
                    kernels.append(slot.kernel)
                    slot.kernel = None
                slot.manual_stop = manual
        for kernel in kernels:
            self._shutdown(kernel)
        return len(kernels)

    def restart(
        self, language: str, after_restart: Callable[[Any], None] | None = None
    ) -> KernelLease:
        with self._lock:
            slot = self._slot(language)
            if slot.kernel is None:
                if slot.factory is None:
                    raise RuntimeError(f"no {language} kernel factory configured")
                slot.kernel = self._create_live(language, slot.factory)
            else:
                slot.kernel.restart()
                if not self._alive(slot.kernel):
                    raise RuntimeError(f"restarted {language} kernel is not alive")
            slot.generation += 1
            slot.manual_stop = False
            lease = self._lease(language, slot)
        if after_restart is not None:
            after_restart(lease.kernel)
        return lease

    def abandon_if_current(self, lease: KernelLease) -> bool:
        """Detach a wedged exact worker without touching a newer replacement."""
        with self._lock:
            slot = self._slots.get(lease.language)
            if not self._matches(slot, lease):
                return False
            slot.kernel = None
            return True

    def shutdown_if_current(self, lease: KernelLease, *, manual: bool = False) -> bool:
        """Detach and shut down an exact desynchronized worker if still current."""
        with self._lock:
            slot = self._slots.get(lease.language)
            if not self._matches(slot, lease):
                return False
            kernel = slot.kernel
            slot.kernel = None
            slot.manual_stop = manual
        self._shutdown(kernel)
        return True

    def restart_if_current(
        self,
        lease: KernelLease,
        after_restart: Callable[[Any], None] | None = None,
    ) -> KernelLease | None:
        with self._lock:
            slot = self._slots.get(lease.language)
            if not self._matches(slot, lease):
                return None
            slot.kernel.restart()
            if not self._alive(slot.kernel):
                raise RuntimeError(f"restarted {lease.language} kernel is not alive")
            slot.generation += 1
            slot.manual_stop = False
            restarted = self._lease(lease.language, slot)
        if after_restart is not None:
            after_restart(restarted.kernel)
        return restarted

    def kill_if_current(self, lease: KernelLease) -> bool:
        with self._lock:
            slot = self._slots.get(lease.language)
            if not self._matches(slot, lease):
                return False
            try:
                slot.kernel.kill_worker()
            except Exception:  # noqa: BLE001 — hard kill is best-effort
                pass
            return True

    def interrupt_if_current(self, lease: KernelLease) -> bool:
        """Interrupt only the worker generation captured by ``lease``."""
        with self._lock:
            slot = self._slots.get(lease.language)
            if not self._matches(slot, lease):
                return False
            try:
                slot.kernel.interrupt()
            except Exception:  # noqa: BLE001 — interruption is best-effort
                pass
            return True

    def _slot(self, language: str) -> _Slot:
        if not language:
            raise ValueError("language must be non-empty")
        return self._slots.setdefault(language, _Slot())

    @staticmethod
    def _alive(kernel: Any) -> bool:
        if kernel is None:
            return False
        try:
            return not hasattr(kernel, "is_alive") or bool(kernel.is_alive())
        except Exception:  # noqa: BLE001 — a broken probe is not a live worker
            return False

    def _create_live(self, language: str, factory: KernelFactory) -> Any:
        replacement = factory()
        if self._alive(replacement):
            return replacement
        self._shutdown(replacement)
        raise RuntimeError(f"{language} kernel factory returned a dead worker")

    @staticmethod
    def _shutdown(kernel: Any) -> None:
        try:
            kernel.shutdown()
        except Exception:  # noqa: BLE001 — replacement must still become current
            pass

    @staticmethod
    def _lease(language: str, slot: _Slot) -> KernelLease:
        return KernelLease(language, slot.key, slot.generation, slot.kernel)

    @staticmethod
    def _matches(slot: _Slot | None, lease: KernelLease) -> bool:
        return bool(
            slot
            and slot.kernel is lease.kernel
            and slot.generation == lease.generation
            and slot.key == lease.key
        )

    def _slot_status(self, language: str, slot: _Slot | None) -> dict:
        kernel = slot.kernel if slot else None
        alive = kernel is not None and self._alive(kernel)
        return {
            "language": language,
            "state": "running" if alive else ("stopped" if slot and slot.manual_stop else "none"),
            "alive": alive,
            "generation": slot.generation if slot and slot.generation >= 0 else 0,
            "manual_stop": bool(slot and slot.manual_stop),
            "key": slot.key if slot else None,
        }


__all__ = ["KernelLease", "KernelSupervisor"]

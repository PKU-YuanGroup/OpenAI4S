"""Session-level ownership for Python and R kernel worker slots.

The supervisor never reads protocol frames and never proxies ``execute``.  A
``Kernel`` still owns its single synchronous frame reader; this class only
coordinates worker identity, lifecycle, and ABA-safe watchdog recovery.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Hashable

KernelFactory = Callable[[], Any]


@dataclass(frozen=True)
class KernelLease:
    language: str
    key: Hashable | None
    generation: int
    kernel: Any
    generation_id: str | None = None
    persistent_ordinal: int | None = None


@dataclass
class _Slot:
    key: Hashable | None = None
    factory: KernelFactory | None = None
    kernel: Any = None
    generation: int = -1
    generation_id: str | None = None
    persistent_ordinal: int | None = None
    started_at: int | None = None
    last_activity_at: int | None = None
    ended_reason: str | None = None
    manual_stop: bool = False


class KernelSupervisor:
    """Own long-lived language kernel slots without touching their protocol.

    Callers must hold their session execution barrier around ``ensure``,
    ``restart`` and ``stop`` so none can race a direct ``Kernel.execute`` frame
    reader. ``interrupt``, ``kill_if_current`` and ``abandon_if_current`` are
    the only operations intended for concurrent watchdog/cancellation paths.
    """

    def __init__(
        self,
        *,
        root_frame_id: str | None = None,
        branch_id: str | None = None,
        generations: Any = None,
        owner_instance_id: str | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._slots: dict[str, _Slot] = {}
        self._lock = threading.RLock()
        self._root_frame_id = root_frame_id
        self._branch_id = branch_id or root_frame_id
        self._generations = generations
        self._owner_instance_id = owner_instance_id
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

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
                self._touch_slot(slot)
                return self._lease(language, slot)

            # Build first: a failed replacement must not destroy a usable worker.
            replacement = self._create_live(language, factory)
            old_generation_id = slot.generation_id
            old_was_alive = current is not None and self._alive(current)
            try:
                identity = self._begin_generation(
                    language,
                    replacement,
                    key,
                    parent_generation_id=old_generation_id,
                )
            except Exception:
                self._shutdown(replacement)
                raise
            old = slot.kernel
            slot.kernel = replacement
            slot.key = key
            slot.factory = factory
            slot.generation += 1
            slot.generation_id = identity["generation_id"]
            slot.persistent_ordinal = identity.get("ordinal")
            slot.started_at = identity["started_at"]
            slot.last_activity_at = identity["last_activity_at"]
            slot.ended_reason = None
            slot.manual_stop = False
            if old_generation_id is not None:
                self._finish_generation(
                    old_generation_id,
                    state="released" if old_was_alive else "crashed",
                    reason="replaced" if old_was_alive else "worker_died",
                )
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

    def inspect_variables(self, language: str, *, limit: int = 200) -> dict:
        """Inspect one already-running language slot without starting it.

        The concrete ``Kernel`` remains the sole frame reader.  We freeze and
        later revalidate its lease so a lifecycle replacement can never make
        an old worker's namespace appear to belong to a new generation.
        """

        with self._lock:
            slot = self._slots.get(language)
            if slot is None or slot.kernel is None or not self._alive(slot.kernel):
                raise RuntimeError(f"no live {language} kernel to inspect")
            lease = self._lease(language, slot)
        response = lease.kernel.inspect_variables(limit=limit)
        with self._lock:
            if not self._matches(self._slots.get(language), lease):
                raise RuntimeError(f"{language} kernel changed during inspection")
        return response

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
                    self._touch_slot(slot)
                except Exception:  # noqa: BLE001 — interruption is best-effort
                    pass
            return count

    def stop(
        self,
        language: str | None = None,
        *,
        manual: bool = True,
        reason: str | None = None,
    ) -> int:
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
            ended: list[str] = []
            for name in names:
                slot = self._slots.get(name)
                if slot is None:
                    continue
                if slot.kernel is not None:
                    kernels.append(slot.kernel)
                    slot.kernel = None
                    if slot.generation_id is not None:
                        ended.append(slot.generation_id)
                slot.manual_stop = manual
                slot.ended_reason = reason or ("manual_stop" if manual else "released")
            for generation_id in ended:
                self._finish_generation(
                    generation_id,
                    state="manually_stopped" if manual else "released",
                    reason=reason or ("manual_stop" if manual else "released"),
                )
        for kernel in kernels:
            self._shutdown(kernel)
        return len(kernels)

    def restart(
        self, language: str, after_restart: Callable[[Any], None] | None = None
    ) -> KernelLease:
        with self._lock:
            slot = self._slot(language)
            old_generation_id = slot.generation_id
            if slot.kernel is None:
                if slot.factory is None:
                    raise RuntimeError(f"no {language} kernel factory configured")
                slot.kernel = self._create_live(language, slot.factory)
            else:
                slot.kernel.restart()
                if not self._alive(slot.kernel):
                    raise RuntimeError(f"restarted {language} kernel is not alive")
            try:
                identity = self._begin_generation(
                    language,
                    slot.kernel,
                    slot.key,
                    parent_generation_id=old_generation_id,
                )
            except Exception:
                failed = slot.kernel
                slot.kernel = None
                slot.ended_reason = "generation_record_failed"
                self._finish_generation(
                    old_generation_id,
                    state="crashed",
                    reason="generation_record_failed",
                )
                self._shutdown(failed)
                raise
            if old_generation_id is not None:
                self._finish_generation(
                    old_generation_id,
                    state="released",
                    reason="restarted",
                )
            slot.generation += 1
            slot.generation_id = identity["generation_id"]
            slot.persistent_ordinal = identity.get("ordinal")
            slot.started_at = identity["started_at"]
            slot.last_activity_at = identity["last_activity_at"]
            slot.ended_reason = None
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
            slot.ended_reason = "watchdog_abandoned"
            self._finish_generation(
                lease.generation_id,
                state="crashed",
                reason="watchdog_abandoned",
            )
            return True

    def shutdown_if_current(
        self,
        lease: KernelLease,
        *,
        manual: bool = False,
        reason: str | None = None,
        terminal_state: str | None = None,
    ) -> bool:
        """Detach and shut down an exact desynchronized worker if still current."""
        with self._lock:
            slot = self._slots.get(lease.language)
            if not self._matches(slot, lease):
                return False
            kernel = slot.kernel
            slot.kernel = None
            slot.manual_stop = manual
            slot.ended_reason = reason or (
                "manual_stop" if manual else "desynchronized"
            )
            self._finish_generation(
                lease.generation_id,
                state=terminal_state or ("manually_stopped" if manual else "crashed"),
                reason=slot.ended_reason,
            )
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
            old_generation_id = slot.generation_id
            slot.kernel.restart()
            if not self._alive(slot.kernel):
                raise RuntimeError(f"restarted {lease.language} kernel is not alive")
            try:
                identity = self._begin_generation(
                    lease.language,
                    slot.kernel,
                    slot.key,
                    parent_generation_id=old_generation_id,
                )
            except Exception:
                failed = slot.kernel
                slot.kernel = None
                slot.ended_reason = "generation_record_failed"
                self._finish_generation(
                    old_generation_id,
                    state="crashed",
                    reason="generation_record_failed",
                )
                self._shutdown(failed)
                raise
            self._finish_generation(
                old_generation_id,
                state="crashed",
                reason="watchdog_restart",
            )
            slot.generation += 1
            slot.generation_id = identity["generation_id"]
            slot.persistent_ordinal = identity.get("ordinal")
            slot.started_at = identity["started_at"]
            slot.last_activity_at = identity["last_activity_at"]
            slot.ended_reason = None
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
                self._touch_slot(slot, state="busy")
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
                self._touch_slot(slot)
            except Exception:  # noqa: BLE001 — interruption is best-effort
                pass
            return True

    def touch(
        self,
        language: str | None = None,
        *,
        state: str | None = None,
        at_ms: int | None = None,
    ) -> int:
        """Record activity for live slots without touching protocol I/O."""

        with self._lock:
            names = [language] if language is not None else list(self._slots)
            touched = 0
            for name in names:
                slot = self._slots.get(name)
                if slot is None or slot.kernel is None:
                    continue
                self._touch_slot(slot, state=state, at_ms=at_ms)
                touched += 1
            return touched

    def record_bootstrap_if_current(
        self,
        language: str,
        kernel: Any,
        metadata: dict[str, Any],
        *,
        state: str = "active",
    ) -> bool:
        """Attach truthful bootstrap metadata to an exact live worker."""

        with self._lock:
            slot = self._slots.get(language)
            if slot is None or slot.kernel is not kernel:
                return False
            now = self._clock_ms()
            slot.last_activity_at = now
            generation_id = slot.generation_id
            if generation_id is not None and self._generations is not None:
                try:
                    self._generations.touch_kernel_generation(
                        generation_id,
                        state=state,
                        bootstrap=metadata,
                        at=now,
                    )
                except Exception:  # noqa: BLE001 — never strand a live worker
                    pass
            return True

    def publish_candidate(
        self,
        language: str,
        key: Hashable | None,
        kernel: Any,
        *,
        factory: KernelFactory,
        generation_id: str,
        expected: KernelLease | None,
        recovered_from_generation_id: str | None = None,
        bootstrap: dict[str, Any] | None = None,
    ) -> KernelLease:
        """Atomically adopt one already-live, already-validated worker.

        Recovery builds and exercises ``kernel`` outside this supervisor.  The
        exact lease observed before that work is supplied as ``expected``;
        publishing fails if another lifecycle operation changed the slot in
        the meantime.  Persistence is allocated before the in-memory pointer
        changes, so a failed generation insert also leaves the current worker
        untouched.

        ``expected=None`` is deliberately strict: it means the slot must have
        no current worker, not "replace whatever happens to be there".
        """

        if not generation_id:
            raise ValueError("generation_id must be non-empty")
        if not callable(factory):
            raise TypeError("recovery candidate factory must be callable")
        if not self._alive(kernel):
            raise RuntimeError(f"{language} recovery candidate is not alive")
        with self._lock:
            slot = self._slot(language)
            if kernel is slot.kernel:
                raise ValueError("recovery candidate is already published")
            if expected is None:
                if slot.kernel is not None:
                    raise RuntimeError(
                        f"{language} kernel changed before recovery publish"
                    )
            elif not self._matches(slot, expected):
                raise RuntimeError(f"{language} kernel changed before recovery publish")

            old = slot.kernel
            old_generation_id = slot.generation_id
            old_was_alive = old is not None and self._alive(old)
            identity = self._begin_generation(
                language,
                kernel,
                key,
                parent_generation_id=old_generation_id,
                generation_id=generation_id,
                recovered_from_generation_id=recovered_from_generation_id,
                bootstrap=bootstrap,
                state="active",
            )
            slot.kernel = kernel
            slot.key = key
            slot.factory = factory
            slot.generation += 1
            slot.generation_id = identity["generation_id"]
            slot.persistent_ordinal = identity.get("ordinal")
            slot.started_at = identity["started_at"]
            slot.last_activity_at = identity["last_activity_at"]
            slot.ended_reason = None
            slot.manual_stop = False
            if old_generation_id is not None:
                self._finish_generation(
                    old_generation_id,
                    state="released" if old_was_alive else "crashed",
                    reason=(
                        "recovery_replaced"
                        if old_was_alive
                        else "recovery_replaced_dead_worker"
                    ),
                )
            lease = self._lease(language, slot)
        if old is not None:
            self._shutdown(old)
        return lease

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

    def _begin_generation(
        self,
        language: str,
        kernel: Any,
        key: Hashable | None,
        *,
        parent_generation_id: str | None,
        generation_id: str | None = None,
        recovered_from_generation_id: str | None = None,
        bootstrap: dict[str, Any] | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        now = self._clock_ms()
        generation_id = generation_id or str(uuid.uuid4())
        environment = self._environment_metadata(kernel, key)
        if self._generations is None or self._root_frame_id is None:
            return {
                "generation_id": generation_id,
                "ordinal": None,
                "started_at": now,
                "last_activity_at": now,
            }
        return self._generations.create_kernel_generation(
            root_frame_id=self._root_frame_id,
            branch_id=self._branch_id,
            language=language,
            generation_id=generation_id,
            parent_generation_id=parent_generation_id,
            environment=environment,
            bootstrap=(
                dict(bootstrap)
                if bootstrap is not None
                else {
                    "status": ("pending" if language == "python" else "not_applicable"),
                    "loaded_sidecars": [],
                }
            ),
            worker_pid=self._pid(kernel),
            owner_instance_id=self._owner_instance_id,
            state=state or ("bootstrapping" if language == "python" else "active"),
            recovered_from_generation_id=recovered_from_generation_id,
            started_at=now,
        )

    def _touch_slot(
        self,
        slot: _Slot,
        *,
        state: str | None = None,
        at_ms: int | None = None,
    ) -> None:
        now = self._clock_ms() if at_ms is None else int(at_ms)
        slot.last_activity_at = now
        if slot.generation_id is not None and self._generations is not None:
            try:
                self._generations.touch_kernel_generation(
                    slot.generation_id,
                    state=state,
                    worker_pid=self._pid(slot.kernel),
                    at=now,
                )
            except Exception:  # noqa: BLE001 — activity cannot break execution
                pass

    def _finish_generation(
        self,
        generation_id: str | None,
        *,
        state: str,
        reason: str,
    ) -> None:
        if generation_id is None or self._generations is None:
            return
        try:
            self._generations.finish_kernel_generation(
                generation_id,
                state=state,
                reason=reason,
                ended_at=self._clock_ms(),
            )
        except Exception:  # noqa: BLE001 — persistence cannot leak a worker
            pass

    @staticmethod
    def _pid(kernel: Any) -> int | None:
        try:
            pid = getattr(kernel, "pid", None)
            return int(pid) if pid is not None else None
        except (TypeError, ValueError, OSError):
            return None

    @classmethod
    def _environment_metadata(cls, kernel: Any, key: Hashable | None) -> dict[str, Any]:
        mode = getattr(kernel, "mode", None)
        argv = getattr(kernel, "argv", None)
        interpreter = getattr(kernel, "python", None)
        if mode == "r" and isinstance(argv, (list, tuple)) and len(argv) >= 2:
            # r_kernel.r_argv ends with ``<Rscript> <r_worker.R>``.
            interpreter = argv[-2]
        metadata: dict[str, Any] = {
            "key": cls._json_safe(key),
            "runtime": mode or "python",
            "interpreter": interpreter,
            "worker_argv": cls._json_safe(argv),
            "environment_root": getattr(kernel, "env_root", None),
            "environment_name": getattr(kernel, "env_name", None),
            "working_directory": getattr(kernel, "cwd", None),
        }
        try:
            sandbox = getattr(kernel, "sandbox_status", None)
            if sandbox is not None:
                metadata["sandbox"] = cls._json_safe(sandbox)
        except Exception:  # noqa: BLE001 — metadata must not break a spawn
            pass
        return metadata

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        return repr(value)

    @staticmethod
    def _shutdown(kernel: Any) -> None:
        try:
            kernel.shutdown()
        except Exception:  # noqa: BLE001 — replacement must still become current
            pass

    @staticmethod
    def _lease(language: str, slot: _Slot) -> KernelLease:
        return KernelLease(
            language,
            slot.key,
            slot.generation,
            slot.kernel,
            slot.generation_id,
            slot.persistent_ordinal,
        )

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
        status = {
            "language": language,
            "state": (
                "running"
                if alive
                else (
                    "stopped"
                    if slot and slot.manual_stop
                    else (
                        "ended"
                        if slot
                        and slot.ended_reason
                        in {"idle_ttl", "session_closed", "daemon_shutdown"}
                        else "none"
                    )
                )
            ),
            "alive": alive,
            "generation": slot.generation if slot and slot.generation >= 0 else 0,
            "manual_stop": bool(slot and slot.manual_stop),
            "key": slot.key if slot else None,
        }
        if slot is not None and slot.generation_id is not None:
            status.update(
                {
                    "generation_id": slot.generation_id,
                    "generation_ordinal": slot.persistent_ordinal,
                    "started_at": slot.started_at,
                    "last_activity_at": slot.last_activity_at,
                    "ended_reason": slot.ended_reason if not alive else None,
                }
            )
        return status


__all__ = ["KernelLease", "KernelSupervisor"]

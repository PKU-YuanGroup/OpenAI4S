"""Thread-safe lazy ownership for a single local kernel worker."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

KernelFactory = Callable[[], Any]
KernelBootstrap = Callable[[Any], None]
KernelPublisher = Callable[[Any | None], None]


class LazyKernel:
    """Create a worker only when code first needs an interpreter.

    Control-tool and structured-finalization turns can carry this object
    without spawning a process.  Candidate publication happens before
    bootstrap so an external cancellation owner can interrupt bootstrap; any
    bootstrap failure atomically detaches and shuts down that candidate.
    """

    def __init__(
        self,
        factory: KernelFactory,
        *,
        bootstrap: KernelBootstrap | None = None,
        publish: KernelPublisher | None = None,
    ) -> None:
        self._factory = factory
        self._bootstrap = bootstrap
        self._publish = publish or (lambda _kernel: None)
        self._kernel: Any | None = None
        self._lock = threading.RLock()

    @property
    def spawned(self) -> bool:
        with self._lock:
            return self._kernel is not None

    @property
    def generation(self) -> Any:
        with self._lock:
            kernel = self._kernel
        return getattr(kernel, "generation", None) if kernel is not None else None

    @property
    def current(self) -> Any | None:
        with self._lock:
            return self._kernel

    def execute(self, code: str, **kwargs: Any) -> dict:
        return self._ensure().execute(code, **kwargs)

    @contextmanager
    def bind_action_context(self, context: dict[str, Any] | None):
        kernel = self._ensure()
        binder = getattr(kernel, "bind_action_context", None)
        if callable(binder):
            with binder(context):
                yield
            return
        yield

    def inspect_variables(self, *, limit: int = 200) -> dict:
        """Inspect an existing worker without defeating lazy startup."""

        with self._lock:
            kernel = self._kernel
        if kernel is None:
            raise RuntimeError("kernel worker has not been started")
        return kernel.inspect_variables(limit=limit)

    def is_alive(self) -> bool:
        with self._lock:
            kernel = self._kernel
        if kernel is None:
            return False
        try:
            return bool(kernel.is_alive())
        except Exception:  # noqa: BLE001 - status must not create a worker
            return False

    def interrupt(self) -> bool:
        with self._lock:
            kernel = self._kernel
        if kernel is None:
            return False
        # As above: report what the kernel says was delivered, not that a call
        # was made. `None` is a double making no claim.
        delivery = kernel.interrupt()
        return delivery is None or bool(delivery)

    def shutdown(self) -> None:
        with self._lock:
            kernel = self._kernel
            self._kernel = None
            if kernel is not None:
                self._notify(None)
        if kernel is not None:
            try:
                kernel.shutdown()
            except Exception:  # noqa: BLE001 - ownership is already detached
                pass

    close = shutdown

    def __enter__(self) -> "LazyKernel":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()

    def _ensure(self) -> Any:
        with self._lock:
            if self._kernel is not None:
                return self._kernel
            kernel = self._factory()
            self._kernel = kernel
            self._notify(kernel)
            try:
                if self._bootstrap is not None:
                    self._bootstrap(kernel)
            except BaseException:
                self._kernel = None
                self._notify(None)
                try:
                    kernel.shutdown()
                except Exception:  # noqa: BLE001 - preserve bootstrap failure
                    pass
                raise
            return kernel

    def _notify(self, kernel: Any | None) -> None:
        try:
            self._publish(kernel)
        except Exception:  # noqa: BLE001 - observation cannot break ownership
            pass


__all__ = ["LazyKernel"]

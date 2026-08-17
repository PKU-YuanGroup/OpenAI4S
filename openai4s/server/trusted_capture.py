"""Session-local admission for truthful Stage 1 Artifact capture.

Foreground capture diffs one shared workspace.  A background kernel or a
person-facing Artifact mutation that is allowed to write during that interval
makes authorship unknowable: its bytes can be swept up as output of the
foreground Cell or native action.  This coordinator gives those lifetimes one
small, deterministic boundary.

The lock protects counters only; it is never held while a Cell or background
job runs.  Capture leases may nest (a foreground Cell can synchronously run a
delegated child), background leases may coexist, and external-mutation leases
may nest only on their owning thread.  The three classes may never overlap.
Admission and counter increment happen under the same lock, so there is no
check-then-start window for the losing side to enter.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from openai4s.server.errors import GatewayError

TRUSTED_CAPTURE_BUSY = "trusted_capture_busy"
TRUSTED_CAPTURE_UNAVAILABLE = "trusted_capture_unavailable"


class TrustedCaptureCoordinator:
    """Coordinate one Web session's capture and background lifetimes."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._lock = threading.Lock()
        self._captures = 0
        self._capture_owner: int | None = None
        self._backgrounds = 0
        self._mutations = 0
        self._mutation_owner: int | None = None
        self._poisoned = False

    def _state_is_valid(self) -> bool:
        counts_are_valid = (
            type(self._captures) is int
            and self._captures >= 0
            and type(self._backgrounds) is int
            and self._backgrounds >= 0
            and type(self._mutations) is int
            and self._mutations >= 0
        )
        if not counts_are_valid:
            return False
        owner_is_valid = (
            self._captures == 0
            and self._capture_owner is None
            or self._captures > 0
            and type(self._capture_owner) is int
        )
        mutation_owner_is_valid = (
            self._mutations == 0
            and self._mutation_owner is None
            or self._mutations > 0
            and type(self._mutation_owner) is int
        )
        active_classes = sum(
            bool(count)
            for count in (self._captures, self._backgrounds, self._mutations)
        )
        return owner_is_valid and mutation_owner_is_valid and active_classes <= 1

    def _poison(self) -> None:
        self._poisoned = True
        # Normalize arbitrary corruption so later cleanup cannot itself raise
        # while the permanent poison bit remains the source of truth.
        self._captures = 0
        self._capture_owner = None
        self._backgrounds = 0
        self._mutations = 0
        self._mutation_owner = None

    def _raise_if_unavailable(self) -> None:
        if not self._state_is_valid():
            self._poison()
        if self._poisoned:
            raise GatewayError(
                503,
                "trusted Artifact capture admission is unavailable",
                TRUSTED_CAPTURE_UNAVAILABLE,
            )

    @contextmanager
    def capture(self) -> Iterator[None]:
        """Admit a capture lifetime, or fail before its action can run."""

        if not self._enabled:
            yield
            return
        owner = threading.get_ident()
        with self._lock:
            self._raise_if_unavailable()
            if self._backgrounds:
                raise GatewayError(
                    409,
                    "trusted Artifact capture is unavailable while a "
                    "background execution is running",
                    TRUSTED_CAPTURE_BUSY,
                )
            if self._mutations:
                raise GatewayError(
                    409,
                    "trusted Artifact capture is unavailable while an "
                    "external workspace mutation is running",
                    TRUSTED_CAPTURE_BUSY,
                )
            if self._capture_owner not in {None, owner}:
                raise GatewayError(
                    409,
                    "another trusted Artifact capture is already running",
                    TRUSTED_CAPTURE_BUSY,
                )
            self._capture_owner = owner
            self._captures += 1
        try:
            yield
        finally:
            with self._lock:
                if (
                    not self._state_is_valid()
                    or self._capture_owner != owner
                    or self._captures <= 0
                ):
                    # Never mask a primary execution/capture exception from a
                    # finally block.  Poisoning is permanent and makes every
                    # later admission fail closed instead.
                    self._poison()
                else:
                    self._captures -= 1
                    if self._captures == 0:
                        self._capture_owner = None

    @contextmanager
    def background(self) -> Iterator[None]:
        """Admit one background job for its complete execution lifetime."""

        if not self._enabled:
            yield
            return
        with self._lock:
            self._raise_if_unavailable()
            if self._captures:
                raise GatewayError(
                    409,
                    "background execution cannot start during trusted "
                    "Artifact capture",
                    TRUSTED_CAPTURE_BUSY,
                )
            if self._mutations:
                raise GatewayError(
                    409,
                    "background execution cannot start during an external "
                    "workspace mutation",
                    TRUSTED_CAPTURE_BUSY,
                )
            self._backgrounds += 1
        try:
            yield
        finally:
            with self._lock:
                if not self._state_is_valid() or self._backgrounds <= 0:
                    # As above: do not replace a job failure during cleanup,
                    # but make all future decisions refuse.
                    self._poison()
                else:
                    self._backgrounds -= 1

    @contextmanager
    def external_mutation(self) -> Iterator[None]:
        """Admit one complete person-facing workspace mutation.

        A single HTTP operation may compose multiple Artifact helpers, so the
        owning thread may nest this lease.  It deliberately cannot nest inside
        a capture lease held by that same thread: allowing that special case
        would recreate the exact provenance ambiguity this boundary closes.
        """

        if not self._enabled:
            yield
            return
        owner = threading.get_ident()
        with self._lock:
            self._raise_if_unavailable()
            if self._captures:
                raise GatewayError(
                    409,
                    "external workspace mutation cannot run during trusted "
                    "Artifact capture",
                    TRUSTED_CAPTURE_BUSY,
                )
            if self._backgrounds:
                raise GatewayError(
                    409,
                    "external workspace mutation cannot run while a background "
                    "execution is running",
                    TRUSTED_CAPTURE_BUSY,
                )
            if self._mutation_owner not in {None, owner}:
                raise GatewayError(
                    409,
                    "another external workspace mutation is already running",
                    TRUSTED_CAPTURE_BUSY,
                )
            self._mutation_owner = owner
            self._mutations += 1
        try:
            yield
        finally:
            with self._lock:
                if (
                    not self._state_is_valid()
                    or self._mutation_owner != owner
                    or self._mutations <= 0
                ):
                    self._poison()
                else:
                    self._mutations -= 1
                    if self._mutations == 0:
                        self._mutation_owner = None


__all__ = [
    "TRUSTED_CAPTURE_BUSY",
    "TRUSTED_CAPTURE_UNAVAILABLE",
    "TrustedCaptureCoordinator",
]

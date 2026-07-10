"""Deterministic clocks, identifiers, and scheduled fault injection."""

from __future__ import annotations

from typing import Iterable

from .schema import FaultSpec

_UUID_TAIL_MAX = 16**12


class FakeClock:
    """A monotonic clock whose sleeps advance instantly."""

    def __init__(self, start_ms: int = 1_000_000):
        self._now_ms = int(start_ms)

    def monotonic_ms(self) -> int:
        return self._now_ms

    def advance_ms(self, milliseconds: int = 1) -> None:
        if milliseconds < 0:
            raise ValueError("FakeClock cannot move backwards")
        self._now_ms += milliseconds

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep duration cannot be negative")
        self.advance_ms(round(seconds * 1000))


class FakeUUIDFactory:
    """Generate stable, valid UUID-shaped identifiers in call order."""

    def __init__(self, start: int = 1):
        if start < 1:
            raise ValueError("FakeUUIDFactory start must be positive")
        self._next = start

    def __call__(self) -> str:
        value = self._next
        if value >= _UUID_TAIL_MAX:
            raise ValueError("FakeUUIDFactory exhausted the 12-hex-digit tail")
        self._next += 1
        return f"00000000-0000-4000-8000-{value:012x}"


class InjectedFault(RuntimeError):
    # A plain exception subclass (not a dataclass): BaseException.args stays
    # populated for logging/pickle, and identity equality/hash are preserved.
    def __init__(
        self, point: str, kind: str, message: str, retryable: bool = False
    ) -> None:
        super().__init__(point, kind, message, retryable)
        self.point = point
        self.kind = kind
        self.message = message
        self.retryable = retryable

    def __str__(self) -> str:
        return self.message


class FaultSchedule:
    """Match each fault once at an exact point/occurrence pair."""

    def __init__(self, specs: Iterable[FaultSpec] = ()):
        self._specs = {(spec.point, spec.occurrence): spec for spec in specs}
        self._visits: dict[str, int] = {}
        self._fired: set[tuple[str, int]] = set()

    def check(self, point: str) -> InjectedFault | None:
        occurrence = self._visits.get(point, 0) + 1
        self._visits[point] = occurrence
        key = (point, occurrence)
        spec = self._specs.get(key)
        if spec is None:
            return None
        self._fired.add(key)
        return InjectedFault(
            point=point,
            kind=spec.kind,
            message=spec.message,
            retryable=spec.retryable,
        )

    @property
    def unfired(self) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(set(self._specs) - self._fired))

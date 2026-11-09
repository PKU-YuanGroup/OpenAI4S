"""Explicit hard/soft budget accounting for rollout allocations.

The ledger separates an estimated reservation from measured consumption.  A
hard limit rejects a reservation before provider work starts; a soft limit
records the reservation and lets the caller continue.  Once a provider
receipt is bound, an allocation cannot be released optimistically.  Unknown
terminal state keeps its reservation until an explicit reconciliation supplies
actual usage.

This is an in-process provider-neutral boundary.  A durable Store-backed
allocation ledger can persist the same records later without changing the
semantics exposed here.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping

BudgetMode = Literal["hard", "soft"]
BudgetReservationState = Literal["reserved", "committed", "released", "unknown"]


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _amount(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number >= 0")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite number >= 0")
    return result


def _amounts(name: str, values: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result: dict[str, float] = {}
    for key, value in values.items():
        result[_text(f"{name} key", str(key))] = _amount(f"{name}[{key}]", value)
    return result


@dataclass(frozen=True)
class BudgetReservation:
    allocation_id: str
    estimated: Mapping[str, float]
    reserved_at: int
    provider_receipt: str | None = None
    state: BudgetReservationState = "reserved"
    actual: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text("allocation_id", self.allocation_id)
        if isinstance(self.reserved_at, bool) or not isinstance(self.reserved_at, int):
            raise ValueError("reserved_at must be an integer")
        if self.reserved_at < 0:
            raise ValueError("reserved_at must be >= 0")
        if self.provider_receipt is not None:
            _text("provider_receipt", self.provider_receipt)
        if self.state not in {"reserved", "committed", "released", "unknown"}:
            raise ValueError("unsupported budget reservation state")
        object.__setattr__(self, "estimated", _amounts("estimated", self.estimated))
        object.__setattr__(self, "actual", _amounts("actual", self.actual))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "estimated": dict(self.estimated),
            "reserved_at": self.reserved_at,
            "provider_receipt": self.provider_receipt,
            "state": self.state,
            "actual": dict(self.actual),
        }


@dataclass(frozen=True)
class BudgetState:
    limits: Mapping[str, float | None]
    modes: Mapping[str, BudgetMode]
    used: Mapping[str, float]
    reserved: Mapping[str, float]
    reservations: tuple[BudgetReservation, ...]

    def __post_init__(self) -> None:
        normalized_limits: dict[str, float | None] = {}
        for key, limit_value in self.limits.items():
            name = _text("limit key", str(key))
            normalized_limits[name] = (
                None if limit_value is None else _amount(f"limits[{name}]", limit_value)
            )
        normalized_modes: dict[str, BudgetMode] = {}
        for key, mode_value in self.modes.items():
            name = _text("mode key", str(key))
            if mode_value not in {"hard", "soft"}:
                raise ValueError("budget mode must be hard or soft")
            normalized_modes[name] = mode_value
        object.__setattr__(self, "limits", normalized_limits)
        object.__setattr__(self, "modes", normalized_modes)
        object.__setattr__(self, "used", _amounts("used", self.used))
        object.__setattr__(self, "reserved", _amounts("reserved", self.reserved))
        object.__setattr__(self, "reservations", tuple(self.reservations))

    def remaining(self, name: str) -> float | None:
        _text("budget field", name)
        limit = self.limits.get(name)
        if limit is None:
            return None
        return max(0.0, limit - self.used.get(name, 0.0) - self.reserved.get(name, 0.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "limits": dict(self.limits),
            "modes": dict(self.modes),
            "used": dict(self.used),
            "reserved": dict(self.reserved),
            "remaining": {name: self.remaining(name) for name in self.limits},
            "reservations": [item.to_dict() for item in self.reservations],
        }


class BudgetExceeded(ValueError):
    """A hard budget refused estimated usage before work began."""

    def __init__(self, fields: Mapping[str, float], message: str = "budget exceeded"):
        super().__init__(message)
        self.fields = dict(fields)


class BudgetLedger:
    """Thread-safe, idempotent estimated/actual usage ledger."""

    def __init__(
        self,
        limits: Mapping[str, float | None],
        *,
        modes: Mapping[str, BudgetMode] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(limits, Mapping):
            raise TypeError("limits must be a mapping")
        self._limits = {
            _text("limit key", str(key)): (
                None if value is None else _amount(f"limits[{key}]", value)
            )
            for key, value in limits.items()
        }
        self._modes: dict[str, BudgetMode] = {key: "hard" for key in self._limits}
        if modes is not None:
            for key, mode in modes.items():
                name = _text("mode key", str(key))
                if mode not in {"hard", "soft"}:
                    raise ValueError("budget mode must be hard or soft")
                self._modes[name] = mode
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._used: dict[str, float] = {}
        self._reservations: dict[str, BudgetReservation] = {}

    def reserve(
        self,
        allocation_id: str,
        estimated: Mapping[str, Any],
        *,
        reserved_at: int | None = None,
    ) -> BudgetReservation:
        """Reserve an estimate, refusing only fields configured as hard."""

        _text("allocation_id", allocation_id)
        estimate = _amounts("estimated", estimated)
        now = self._now(reserved_at)
        with self._lock:
            current = self._reservations.get(allocation_id)
            if current is not None:
                if dict(current.estimated) != estimate:
                    raise ValueError(
                        "allocation_id is already bound to another estimate"
                    )
                return current
            reserved = self._reserved_totals()
            exceeded: dict[str, float] = {}
            for name, value in estimate.items():
                limit = self._limits.get(name)
                if (
                    self._modes.get(name, "hard") != "hard"
                    or limit is None
                    or self._used.get(name, 0.0) + reserved.get(name, 0.0) + value
                    <= limit
                ):
                    continue
                exceeded[name] = (
                    self._used.get(name, 0.0) + reserved.get(name, 0.0) + value - limit
                )
            if exceeded:
                raise BudgetExceeded(exceeded)
            result = BudgetReservation(allocation_id, estimate, now)
            self._reservations[allocation_id] = result
            return result

    def bind_receipt(
        self, allocation_id: str, provider_receipt: str
    ) -> BudgetReservation:
        _text("provider_receipt", provider_receipt)
        with self._lock:
            current = self._get(allocation_id)
            if current.provider_receipt is not None:
                if current.provider_receipt != provider_receipt:
                    raise ValueError("allocation is already bound to another receipt")
                return current
            if current.state != "reserved":
                raise ValueError("provider receipt must be bound while reserved")
            result = BudgetReservation(
                current.allocation_id,
                current.estimated,
                current.reserved_at,
                provider_receipt,
                current.state,
                current.actual,
            )
            self._reservations[allocation_id] = result
            return result

    def release(self, allocation_id: str) -> BudgetReservation:
        with self._lock:
            current = self._get(allocation_id)
            if current.state == "released":
                return current
            if current.state != "reserved" or current.provider_receipt is not None:
                raise ValueError("only an unstarted reservation can be released")
            result = self._replace(current, state="released")
            self._reservations[allocation_id] = result
            return result

    def mark_unknown(self, allocation_id: str) -> BudgetReservation:
        """Keep the estimate reserved until the provider is reconciled."""

        with self._lock:
            current = self._get(allocation_id)
            if current.state == "unknown":
                return current
            if current.state != "reserved" or current.provider_receipt is None:
                raise ValueError("unknown requires a receipt-bound reservation")
            result = self._replace(current, state="unknown")
            self._reservations[allocation_id] = result
            return result

    def commit(
        self, allocation_id: str, actual: Mapping[str, Any]
    ) -> BudgetReservation:
        """Settle measured usage; no estimate is silently treated as actual."""

        measured = _amounts("actual", actual)
        with self._lock:
            current = self._get(allocation_id)
            if current.state == "committed":
                if dict(current.actual) != measured:
                    raise ValueError(
                        "committed allocation has conflicting actual usage"
                    )
                return current
            if current.state not in {"reserved", "unknown"}:
                raise ValueError("only a reserved or unknown allocation can commit")
            if current.provider_receipt is None:
                raise ValueError("actual usage requires a provider receipt")
            combined = dict(current.actual)
            combined.update(measured)
            missing = set(current.estimated).difference(combined)
            if missing:
                result = self._replace(current, state="unknown", actual=combined)
                self._reservations[allocation_id] = result
                return result
            result = self._replace(current, state="committed", actual=combined)
            self._reservations[allocation_id] = result
            for name, value in combined.items():
                self._used[name] = self._used.get(name, 0.0) + value
            return result

    def reconcile(
        self, allocation_id: str, actual: Mapping[str, Any]
    ) -> BudgetReservation:
        """Settle an unknown receipt after the provider supplies all fields."""

        return self.commit(allocation_id, actual)

    def state(self) -> BudgetState:
        with self._lock:
            return BudgetState(
                self._limits,
                self._modes,
                dict(self._used),
                self._reserved_totals(),
                tuple(
                    sorted(
                        self._reservations.values(), key=lambda item: item.allocation_id
                    )
                ),
            )

    def get(self, allocation_id: str) -> BudgetReservation | None:
        with self._lock:
            return self._reservations.get(allocation_id)

    def _reserved_totals(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for item in self._reservations.values():
            if item.state not in {"reserved", "unknown"}:
                continue
            for name, value in item.estimated.items():
                totals[name] = totals.get(name, 0.0) + value
        return totals

    def _get(self, allocation_id: str) -> BudgetReservation:
        _text("allocation_id", allocation_id)
        try:
            return self._reservations[allocation_id]
        except KeyError as exc:
            raise KeyError(f"unknown allocation {allocation_id!r}") from exc

    @staticmethod
    def _replace(current: BudgetReservation, **changes: Any) -> BudgetReservation:
        return BudgetReservation(
            changes.get("allocation_id", current.allocation_id),
            changes.get("estimated", current.estimated),
            changes.get("reserved_at", current.reserved_at),
            changes.get("provider_receipt", current.provider_receipt),
            changes.get("state", current.state),
            changes.get("actual", current.actual),
        )

    def _now(self, value: int | None) -> int:
        timestamp = self._clock_ms() if value is None else value
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp < 0
        ):
            raise ValueError("reserved_at must be an integer >= 0")
        return timestamp


__all__ = [
    "BudgetExceeded",
    "BudgetLedger",
    "BudgetMode",
    "BudgetReservation",
    "BudgetReservationState",
    "BudgetState",
]

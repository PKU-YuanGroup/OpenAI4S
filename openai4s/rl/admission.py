"""Provider-neutral resource admission for RL rollouts.

This controller is intentionally a small in-process boundary.  Durable
``compute_jobs`` rows remain the source of truth for remote work; an adapter
may use this controller before submitting one of those jobs.  Admission wait
is measured from reservation to start, while actual compute time is measured
from start to terminal.  The two intervals are never combined or relabeled as
GPU time.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

AdmissionState = Literal["reserved", "running", "completed", "released", "unknown"]
AdmissionStatus = Literal["admitted", "already_admitted", "waiting"]


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _integer(name: str, value: int, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _number(name: str, value: float, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number >= {minimum}")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return result


@dataclass(frozen=True)
class ResourceRequest:
    """Resources requested before a rollout submits provider work."""

    cpu_cores: int = 0
    gpu_count: int = 0
    gpu_memory_gb: float = 0.0
    max_wall_time_ms: int | None = None
    priority: str = "normal"
    budget_group: str = "default"

    def __post_init__(self) -> None:
        _integer("cpu_cores", self.cpu_cores)
        _integer("gpu_count", self.gpu_count)
        object.__setattr__(
            self, "gpu_memory_gb", _number("gpu_memory_gb", self.gpu_memory_gb)
        )
        if self.max_wall_time_ms is not None:
            _integer("max_wall_time_ms", self.max_wall_time_ms, minimum=1)
        _text("priority", self.priority)
        _text("budget_group", self.budget_group)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_cores": self.cpu_cores,
            "gpu_count": self.gpu_count,
            "gpu_memory_gb": self.gpu_memory_gb,
            "max_wall_time_ms": self.max_wall_time_ms,
            "priority": self.priority,
            "budget_group": self.budget_group,
        }


@dataclass(frozen=True)
class ResourceCapacity:
    """Capacity against which active reservations are admitted."""

    cpu_cores: int
    gpu_count: int
    gpu_memory_gb: float

    def __post_init__(self) -> None:
        _integer("cpu_cores", self.cpu_cores)
        _integer("gpu_count", self.gpu_count)
        object.__setattr__(
            self, "gpu_memory_gb", _number("gpu_memory_gb", self.gpu_memory_gb)
        )


@dataclass(frozen=True)
class AdmissionRecord:
    """One idempotent allocation and its measured lifecycle."""

    allocation_id: str
    request: ResourceRequest
    reserved_at: int
    provider_receipt: str | None = None
    state: AdmissionState = "reserved"
    started_at: int | None = None
    terminal_at: int | None = None
    admission_wait_ms: int | None = None
    actual_compute_time_ms: int | None = None

    def __post_init__(self) -> None:
        _text("allocation_id", self.allocation_id)
        _integer("reserved_at", self.reserved_at)
        if self.provider_receipt is not None:
            _text("provider_receipt", self.provider_receipt)
        if self.state not in {
            "reserved",
            "running",
            "completed",
            "released",
            "unknown",
        }:
            raise ValueError("unsupported admission state")
        for name in (
            "started_at",
            "terminal_at",
            "admission_wait_ms",
            "actual_compute_time_ms",
        ):
            value = getattr(self, name)
            if value is not None:
                _integer(name, value)
        if self.terminal_at is not None and self.started_at is not None:
            if self.terminal_at < self.started_at:
                raise ValueError("terminal_at precedes started_at")
        if self.admission_wait_ms is not None and self.started_at is None:
            raise ValueError("admission_wait_ms requires started_at")
        if self.actual_compute_time_ms is not None and self.terminal_at is None:
            raise ValueError("actual_compute_time_ms requires terminal_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "request": self.request.to_dict(),
            "reserved_at": self.reserved_at,
            "provider_receipt": self.provider_receipt,
            "state": self.state,
            "started_at": self.started_at,
            "terminal_at": self.terminal_at,
            "admission_wait_ms": self.admission_wait_ms,
            "actual_compute_time_ms": self.actual_compute_time_ms,
        }


@dataclass(frozen=True)
class AdmissionResult:
    status: AdmissionStatus
    record: AdmissionRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "record": self.record.to_dict() if self.record is not None else None,
        }


class ResourceAdmission:
    """Thread-safe, idempotent resource reservation boundary."""

    def __init__(
        self,
        capacity: ResourceCapacity,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.capacity = capacity
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._records: dict[str, AdmissionRecord] = {}

    def admit(
        self,
        allocation_id: str,
        request: ResourceRequest,
        *,
        reserved_at: int | None = None,
    ) -> AdmissionResult:
        """Reserve resources, or return ``waiting`` without consuming them."""

        _text("allocation_id", allocation_id)
        if not isinstance(request, ResourceRequest):
            raise TypeError("request must be ResourceRequest")
        now = self._now(reserved_at)
        with self._lock:
            current = self._records.get(allocation_id)
            if current is not None:
                if current.request != request:
                    raise ValueError(
                        "allocation_id is already bound to another request"
                    )
                return AdmissionResult("already_admitted", current)
            if not self._fits(request):
                return AdmissionResult("waiting")
            record = AdmissionRecord(allocation_id, request, now)
            self._records[allocation_id] = record
            return AdmissionResult("admitted", record)

    def bind_receipt(
        self, allocation_id: str, provider_receipt: str
    ) -> AdmissionRecord:
        """Bind the provider's receipt before the work is started."""

        _text("provider_receipt", provider_receipt)
        with self._lock:
            record = self._get(allocation_id)
            if record.provider_receipt is not None:
                if record.provider_receipt != provider_receipt:
                    raise ValueError("allocation is already bound to another receipt")
                return record
            if record.state != "reserved":
                raise ValueError("provider receipt must be bound while reserved")
            updated = replace(record, provider_receipt=provider_receipt)
            self._records[allocation_id] = updated
            return updated

    def start(
        self, allocation_id: str, *, started_at: int | None = None
    ) -> AdmissionRecord:
        """Start work after receipt binding and record admission wait separately."""

        with self._lock:
            record = self._get(allocation_id)
            if record.state == "running":
                return record
            if record.state != "reserved":
                raise ValueError("only a reserved allocation can start")
            if record.provider_receipt is None:
                raise ValueError("provider receipt must be bound before start")
            started = self._now(started_at)
            if started < record.reserved_at:
                raise ValueError("started_at precedes reserved_at")
            updated = replace(
                record,
                state="running",
                started_at=started,
                admission_wait_ms=started - record.reserved_at,
            )
            self._records[allocation_id] = updated
            return updated

    def finish(
        self,
        allocation_id: str,
        *,
        terminal_at: int | None = None,
        state: Literal["completed", "unknown"] = "completed",
    ) -> AdmissionRecord:
        """Record terminal time; actual compute time starts at ``start``."""

        with self._lock:
            record = self._get(allocation_id)
            if record.state in {"completed", "unknown"}:
                return record
            if record.state != "running" or record.started_at is None:
                raise ValueError("only a running allocation can finish")
            terminal = self._now(terminal_at)
            if terminal < record.started_at:
                raise ValueError("terminal_at precedes started_at")
            updated = replace(
                record,
                state=state,
                terminal_at=terminal,
                actual_compute_time_ms=terminal - record.started_at,
            )
            self._records[allocation_id] = updated
            return updated

    def release(self, allocation_id: str) -> AdmissionRecord:
        """Release an allocation that has not started, idempotently."""

        with self._lock:
            record = self._get(allocation_id)
            if record.state == "released":
                return record
            if record.state != "reserved":
                raise ValueError("only a reserved allocation can be released")
            updated = replace(record, state="released")
            self._records[allocation_id] = updated
            return updated

    def get(self, allocation_id: str) -> AdmissionRecord | None:
        with self._lock:
            return self._records.get(allocation_id)

    def _fits(self, request: ResourceRequest) -> bool:
        active = [
            item
            for item in self._records.values()
            if item.state in {"reserved", "running", "unknown"}
        ]
        return (
            sum(item.request.cpu_cores for item in active) + request.cpu_cores
            <= self.capacity.cpu_cores
            and sum(item.request.gpu_count for item in active) + request.gpu_count
            <= self.capacity.gpu_count
            and sum(item.request.gpu_memory_gb for item in active)
            + request.gpu_memory_gb
            <= self.capacity.gpu_memory_gb
        )

    def _get(self, allocation_id: str) -> AdmissionRecord:
        _text("allocation_id", allocation_id)
        try:
            return self._records[allocation_id]
        except KeyError as exc:
            raise KeyError(f"unknown allocation {allocation_id!r}") from exc

    def _now(self, value: int | None) -> int:
        return _integer("timestamp", self._clock_ms() if value is None else value)


__all__ = [
    "AdmissionRecord",
    "AdmissionResult",
    "AdmissionState",
    "AdmissionStatus",
    "ResourceAdmission",
    "ResourceCapacity",
    "ResourceRequest",
]

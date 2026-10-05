"""Explicit device registration; importing this module registers nothing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from openai4s.lab.models import DeviceDescriptor, ErrorCode, LabError, RunMode
from openai4s.lab.ports import DevicePort


@dataclass(frozen=True)
class DeviceRegistration:
    device_id: str
    backend: str
    mode: RunMode
    title: str
    profiles: tuple[str, ...]
    descriptor_loader: Callable[[str], DeviceDescriptor]
    port_factory: Callable[[], DevicePort]


class DeviceRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._devices: dict[str, DeviceRegistration] = {}

    def register(self, reg: DeviceRegistration) -> None:
        with self._lock:
            if reg.device_id in self._devices:
                raise ValueError("device_id already registered")
            self._devices[reg.device_id] = reg

    def get(self, device_id: str) -> DeviceRegistration:
        with self._lock:
            try:
                return self._devices[device_id]
            except KeyError:
                raise LabError(
                    ErrorCode.DEVICE_NOT_FOUND, "Device is not registered"
                ) from None

    def list(self) -> list[DeviceRegistration]:
        with self._lock:
            return [self._devices[key] for key in sorted(self._devices)]

    def describe(self, device_id: str, profile: str) -> DeviceDescriptor:
        reg = self.get(device_id)
        if profile not in reg.profiles:
            raise LabError(ErrorCode.UNSUPPORTED_ACTION, "Profile is not supported")
        # A loader can be slow/reentrant. Do not hold the registry lock here.
        return reg.descriptor_loader(profile)

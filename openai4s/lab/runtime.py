"""The process composition boundary used by CLI and gateway adapters."""

import time
from collections.abc import Callable

from openai4s.lab.devices import DeviceRegistry
from openai4s.lab.manager import LabLimits, LabManager
from openai4s.lab.ports import LabLedgerPort
from openai4s.lab.reconcile import reconcile_on_startup
from openai4s.process_instance import PROCESS_INSTANCE_ID


def wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def build_lab_manager(
    *,
    ledger_provider: Callable[[], LabLedgerPort],
    registry: DeviceRegistry,
    instance_id: str = PROCESS_INSTANCE_ID,
    clock_ms: Callable[[], int] = wall_clock_ms,
    limits: LabLimits = LabLimits(),
) -> LabManager:
    manager = LabManager(
        ledger_provider,
        registry,
        instance_id=instance_id,
        clock_ms=clock_ms,
        limits=limits,
    )
    manager.startup_summary = reconcile_on_startup(
        ledger_provider(), instance_id=instance_id, clock_ms=clock_ms
    )
    return manager

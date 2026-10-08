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
    on_change: Callable[[str, str], None] | None = None,
) -> LabManager:
    manager = LabManager(
        ledger_provider,
        registry,
        instance_id=instance_id,
        clock_ms=clock_ms,
        limits=limits,
        on_change=on_change,
    )
    ledger = ledger_provider()
    cursors: dict[str, int] = {}
    if on_change is not None:
        try:
            for run in ledger.nonterminal_runs():
                root = run["root_frame_id"]
                if run["daemon_instance"] != instance_id and root not in cursors:
                    cursors[root] = ledger.latest_event_seq(root)
        except Exception:
            # A notification read must not prevent the existing reconciliation.
            pass
    try:
        manager.startup_summary = reconcile_on_startup(
            ledger, instance_id=instance_id, clock_ms=clock_ms
        )
    finally:
        # Read committed events even after partial reconciliation failed. This
        # reports the runs that actually changed, without another write path.
        for root, cursor in cursors.items():
            try:
                changed = dict.fromkeys(
                    event["run_id"]
                    for event in ledger.events_since(
                        root, after_seq=cursor, limit=2**63 - 1
                    )
                )
            except Exception:
                continue
            for run_id in changed:
                try:
                    assert on_change is not None
                    on_change(root, run_id)
                except Exception:
                    pass
    return manager

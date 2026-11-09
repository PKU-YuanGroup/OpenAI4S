from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.rl import ResourceCapacity, ResourceRequest
from openai4s.store import get_store


def test_store_backed_budget_survives_reopen_and_does_not_double_commit(tmp_path):
    db_path = Config(data_dir=tmp_path).db_path
    store = get_store(db_path)
    ledger = store.rl.budget_ledger(
        "rollout-budget",
        {"gpu_seconds": 10, "api_usd": 1},
        modes={"gpu_seconds": "hard", "api_usd": "soft"},
    )
    ledger.reserve("allocation-1", {"gpu_seconds": 4, "api_usd": 0.2})
    ledger.bind_receipt("allocation-1", "provider-receipt-1")
    ledger.mark_unknown("allocation-1")
    store.close()

    reopened = get_store(db_path)
    try:
        restored = reopened.rl.budget_ledger(
            "rollout-budget",
            {"gpu_seconds": 10, "api_usd": 1},
            modes={"gpu_seconds": "hard", "api_usd": "soft"},
        )
        assert restored.get("allocation-1") is not None
        assert restored.get("allocation-1").state == "unknown"
        assert restored.get("allocation-1").provider_receipt == "provider-receipt-1"
        assert restored.state().reserved["gpu_seconds"] == pytest.approx(4)

        restored.reconcile("allocation-1", {"gpu_seconds": 3, "api_usd": 0.1})
        restored.reconcile("allocation-1", {"gpu_seconds": 3, "api_usd": 0.1})
        assert restored.state().used == {"gpu_seconds": 3.0, "api_usd": 0.1}
    finally:
        reopened.close()


def test_store_backed_budget_rejects_changed_limits(tmp_path):
    db_path = Config(data_dir=tmp_path).db_path
    store = get_store(db_path)
    store.rl.budget_ledger("budget", {"gpu_seconds": 10})
    store.close()
    reopened = get_store(db_path)
    try:
        with pytest.raises(ValueError, match="limits conflict"):
            reopened.rl.budget_ledger("budget", {"gpu_seconds": 11})
    finally:
        reopened.close()


def test_store_backed_resource_admission_preserves_unknown_capacity_use(tmp_path):
    db_path = Config(data_dir=tmp_path).db_path
    store = get_store(db_path)
    admission = store.rl.resource_admission(
        "compute-admission", ResourceCapacity(4, 1, 80)
    )
    request = ResourceRequest(cpu_cores=4, gpu_count=1, gpu_memory_gb=80)
    admission.admit("allocation-remote", request, reserved_at=100)
    admission.bind_receipt("allocation-remote", "provider-receipt")
    admission.start("allocation-remote", started_at=250)
    admission.finish("allocation-remote", terminal_at=500, state="unknown")
    store.close()

    reopened = get_store(db_path)
    try:
        restored = reopened.rl.resource_admission(
            "compute-admission", ResourceCapacity(4, 1, 80)
        )
        record = restored.get("allocation-remote")
        assert record is not None
        assert record.state == "unknown"
        assert record.admission_wait_ms == 150
        assert record.actual_compute_time_ms == 250
        assert restored.admit("allocation-next", request).status == "waiting"
    finally:
        reopened.close()
